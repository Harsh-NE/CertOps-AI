"""
Local certificate lab: generates a private PKI and serves a set of TLS endpoints, each with a different problem,
plus a local OCSP responder. Replaces the old openssl/nginx lab (no Docker, hosts-file edits or openssl needed).

    cd backend
    python -m app.devtools.lab              # generate, serve, and register the endpoints as scan targets

Then start the API with:
    ALLOW_PRIVATE_TARGETS=true TRUST_STORE_EXTRA_PEM=../lab/certs/ca.pem
and scan from the UI/API (POST /api/cloud/discover {"providers": ["network_scan"]}).

Everything here is synthetic test data on 127.0.0.1.
"""
import argparse
import socket
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.devtools import pki
from app.devtools.ocsp_responder import OcspResponder

BASE_PORT = 9441


@dataclass
class Scenario:
    name: str
    server_name: str
    environment: str
    expect: str  # what the platform should report
    port: int = 0


def _serve(key, certs, port: int, weak: bool = False, bind: str = "127.0.0.1") -> int:
    d = tempfile.mkdtemp(prefix="certops-lab-")
    cp, kp = Path(d, "c.pem"), Path(d, "k.pem")
    cp.write_bytes(pki.pem(*certs))
    kp.write_bytes(pki.key_pem(key))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if weak:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    ctx.load_cert_chain(str(cp), str(kp))
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    sock.listen(16)

    def loop():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            try:
                ctx.wrap_socket(conn, server_side=True).close()
            except Exception:
                conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return sock.getsockname()[1]


def build(out_dir: Path, base_port: int = BASE_PORT, bind: str = "127.0.0.1",
          advertise_host: str = "127.0.0.1") -> List[Scenario]:
    """base_port=0 picks free ports (used by tests)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    root = pki.make_root("CertOps Lab Root CA")
    inter = root.issue_intermediate("CertOps Lab Intermediate CA")
    ocsp = OcspResponder(inter, bind=bind, advertise_host=advertise_host)
    (out_dir / "ca.pem").write_bytes(pki.pem(root.cert))
    rogue = pki.make_root("Untrusted Rogue CA")

    s: List[Scenario] = []

    def add(name, server_name, env, expect, key_cert, chain, weak=False):
        sc = Scenario(name, server_name, env, expect)
        sc.port = _serve(key_cert[0], [key_cert[1], *chain], base_port + len(s) if base_port else 0, weak=weak, bind=bind)
        s.append(sc)
        return key_cert[1]

    add("healthy", "healthy.lab.test", "prod", "valid", inter.issue_leaf("healthy.lab.test", days_to=200), [inter.cert])
    add("expiring-3d", "expiring.lab.test", "prod", "expiring_soon + error finding",
        inter.issue_leaf("expiring.lab.test", days_to=3), [inter.cert])
    add("expired", "expired.lab.test", "staging", "expired",
        inter.issue_leaf("expired.lab.test", days_from=-400, days_to=-10), [inter.cert])
    add("wrong-host", "wrong-host.lab.test", "staging", "hostname_mismatch",
        inter.issue_leaf("some-other-name.lab.test"), [inter.cert])
    add("self-signed", "selfsigned.lab.test", "dev", "invalid_chain (self_signed)",
        pki.make_self_signed("selfsigned.lab.test"), [])
    add("no-intermediate", "nochain.lab.test", "dev", "invalid_chain (missing intermediate)",
        inter.issue_leaf("nochain.lab.test"), [])
    add("weak-rsa-1024", "legacy.lab.test", "dev", "weak_crypto",
        inter.issue_leaf("legacy.lab.test", key_kind="rsa1024"), [inter.cert], weak=True)
    add("untrusted-ca", "rogue.lab.test", "dev", "invalid_chain (untrusted CA)",
        rogue.issue_leaf("rogue.lab.test"), [])
    add("wildcard", "app.wild.lab.test", "prod", "valid (wildcard)",
        inter.issue_leaf("*.wild.lab.test", days_to=120), [inter.cert])
    add("ocsp-good", "ocsp-good.lab.test", "prod", "valid, revoked=false (OCSP)",
        inter.issue_leaf("ocsp-good.lab.test", ocsp_url=ocsp.url), [inter.cert])
    revoked = add("ocsp-revoked", "ocsp-revoked.lab.test", "prod", "revoked",
                  inter.issue_leaf("ocsp-revoked.lab.test", ocsp_url=ocsp.url), [inter.cert])
    ocsp.revoked.add(revoked.serial_number)
    return s


def register(scenarios: List[Scenario]) -> int:
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    added = 0
    try:
        for sc in scenarios:
            row = db.query(models.ScanTarget).filter_by(host="127.0.0.1", port=sc.port).first()
            if row is None:
                row = models.ScanTarget(host="127.0.0.1", port=sc.port)
                db.add(row)
                added += 1
            row.server_name, row.environment = sc.server_name, sc.environment
        db.commit()
    finally:
        db.close()
    return added


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="../lab/certs", help="where to write ca.pem")
    ap.add_argument("--bind", default="127.0.0.1", help="interface to listen on (0.0.0.0 to reach it from Docker)")
    ap.add_argument("--advertise-host", default="127.0.0.1", help="host name put in OCSP URLs (e.g. host.docker.internal)")
    ap.add_argument("--no-register", action="store_true", help="do not add the endpoints as scan targets in the DB")
    args = ap.parse_args(argv)
    scenarios = build(Path(args.out), bind=args.bind, advertise_host=args.advertise_host)
    print(f"CA bundle: {Path(args.out).resolve() / 'ca.pem'}\n")
    print(f"{'scenario':<18}{'target':<24}{'SNI / hostname':<24}{'env':<9}expected")
    for sc in scenarios:
        print(f"{sc.name:<18}127.0.0.1:{sc.port:<14}{sc.server_name:<24}{sc.environment:<9}{sc.expect}")
    if not args.no_register:
        print(f"\nRegistered {register(scenarios)} new scan target(s) in {_db_hint()}.")
    print("\nStart the API with ALLOW_PRIVATE_TARGETS=true and TRUST_STORE_EXTRA_PEM pointing at the CA bundle above.")
    print("Serving; press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _db_hint() -> str:
    from app.config import settings
    return settings.DATABASE_URL.split("@")[-1]


if __name__ == "__main__":
    main()
