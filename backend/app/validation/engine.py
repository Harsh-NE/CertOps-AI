"""
Certificate validation engine. Pure function of (normalized certificate, trust roots, time): it performs
no database access. Network use is limited to OCSP and goes through an injectable fetcher.

Checks: expiry, hostname match, key size/algorithm, weak signature, self-signed, chain/trust path,
revocation (OCSP). Each check emits zero or more Findings; `chain_valid` / `revoked` are tri-state
(True / False / None = could not be determined) and are never guessed.
"""
import ipaddress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from cryptography import x509
from cryptography.x509 import verification as V

from app.providers.base import NormalizedCertificate
from app.timeutil import ensure_utc, utcnow
from app.validation.hostname import hostname_matches
from app.validation.ocsp import Fetcher, check_revocation, http_fetch

WEAK_HASHES = {"md5", "sha1"}
MIN_RSA_BITS = 2048
MIN_EC_BITS = 256


@dataclass
class Finding:
    check: str
    severity: str  # info | warning | error
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    findings: List[Finding] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    chain_valid: Optional[bool] = None
    chain_depth: Optional[int] = None
    revoked: Optional[bool] = None
    revocation_method: Optional[str] = None  # OCSP | none

    @property
    def overall(self) -> str:
        sev = {f.severity for f in self.findings}
        return "fail" if "error" in sev else "warn" if "warning" in sev else "pass"

    def has(self, check: str, severity: Optional[str] = None) -> bool:
        return any(f.check == check and (severity is None or f.severity == severity) for f in self.findings)

    def findings_as_dicts(self) -> List[dict]:
        return [asdict(f) for f in self.findings]


def _is_self_issued_and_signed(cert: x509.Certificate) -> bool:
    if cert.issuer != cert.subject:
        return False
    try:
        cert.verify_directly_issued_by(cert)
        return True
    except Exception:
        return False


def _chain_subject(n: NormalizedCertificate):
    """A name the certificate itself claims, so the trust-path check isolates chain problems from hostname problems."""
    for name in n.san_dns:
        if name.startswith("*."):
            name = "www." + name[2:]
        if name:
            return V.DNSName(name)
    if n.san_ips:
        return V.IPAddress(ipaddress.ip_address(n.san_ips[0]))
    return None


def validate(
    n: NormalizedCertificate,
    roots: List[x509.Certificate],
    *,
    now: Optional[datetime] = None,
    warn_days: int = 30,
    critical_days: int = 7,
    check_revocation_online: bool = True,
    ocsp_fetch: Fetcher = http_fetch,
    ocsp_timeout: float = 5.0,
) -> ValidationReport:
    now = ensure_utc(now) or utcnow()
    r = ValidationReport()
    add = r.findings.append

    certs = [x509.load_der_x509_certificate(d) for d in n.chain_der] if n.chain_der else []
    leaf = certs[0] if certs else None
    r.chain_depth = len(certs) or None

    # --- expiry -----------------------------------------------------------------------------
    r.checks_run.append("expiry")
    if n.not_after is None or n.not_before is None:
        add(Finding("expiry", "warning", "Validity period unknown"))
    elif now < n.not_before:
        add(Finding("not_yet_valid", "error", f"Certificate is not valid until {n.not_before.isoformat()}"))
    elif now > n.not_after:
        days = (now - n.not_after).days
        add(Finding("expired", "error", f"Certificate expired {days} day(s) ago", {"days_ago": days}))
    else:
        left = (n.not_after - now).days
        if left <= critical_days:
            add(Finding("expiring", "error", f"Certificate expires in {left} day(s)", {"days_left": left}))
        elif left <= warn_days:
            add(Finding("expiring", "warning", f"Certificate expires in {left} day(s)", {"days_left": left}))

    # --- hostname ---------------------------------------------------------------------------
    if n.expected_hostname:
        r.checks_run.append("hostname")
        ok, how = hostname_matches(n.expected_hostname, n.san_dns, n.san_ips, n.common_name)
        if not ok:
            add(Finding("hostname_mismatch", "error",
                        f"Certificate does not cover '{n.expected_hostname}'",
                        {"expected": n.expected_hostname, "san_dns": n.san_dns, "san_ips": n.san_ips}))
        elif how == "cn-fallback":
            add(Finding("hostname_cn_only", "warning", "Hostname matched only via the deprecated Common Name"))

    # --- key strength -----------------------------------------------------------------------
    r.checks_run.append("key")
    algo, bits = n.key_algorithm, n.key_size
    if algo == "RSA" and bits is not None and bits < MIN_RSA_BITS:
        add(Finding("weak_key", "error", f"RSA key is {bits} bits (minimum {MIN_RSA_BITS})", {"bits": bits}))
    elif algo == "EC" and bits is not None and bits < MIN_EC_BITS:
        add(Finding("weak_key", "error", f"EC key is {bits} bits (minimum {MIN_EC_BITS})", {"bits": bits}))
    elif algo == "DSA":
        add(Finding("weak_key", "error", "DSA keys are deprecated for TLS"))
    elif algo not in ("RSA", "EC", "Ed25519", "Ed448"):
        add(Finding("weak_key", "warning", f"Unrecognised key algorithm: {algo}"))

    # --- signature algorithm (leaf + non-root members of the chain) -------------------------
    r.checks_run.append("signature")
    if n.signature_hash in WEAK_HASHES:
        add(Finding("weak_signature", "error", f"Leaf is signed with {n.signature_hash.upper()}",
                    {"hash": n.signature_hash}))
    for c in certs[1:]:
        h = c.signature_hash_algorithm.name if c.signature_hash_algorithm else None
        if h in WEAK_HASHES and not _is_self_issued_and_signed(c):
            add(Finding("weak_signature", "warning", f"Intermediate '{c.subject.rfc4514_string()}' is signed with {h.upper()}"))

    # --- self-signed + trust path -----------------------------------------------------------
    if leaf is not None:
        leaf_is_root = any(x == leaf for x in roots)
        self_signed = _is_self_issued_and_signed(leaf)
        r.checks_run.append("chain")
        if self_signed and not leaf_is_root:
            add(Finding("self_signed", "error", "Certificate is self-signed and not in the trust store"))
            r.chain_valid = False
        else:
            subject = _chain_subject(n)
            if subject is None:
                add(Finding("chain", "warning", "Certificate has no SAN, so the trust path could not be checked"))
            else:
                # Verify at a time inside the intersection of the presented certs' validity windows, so the
                # leaf's expiry (reported above) is not double-counted as a trust failure. With no overlap
                # we fall back to "now" and let the verifier report the real problem.
                lo = max(ensure_utc(c.not_valid_before_utc) for c in certs)
                hi = min(ensure_utc(c.not_valid_after_utc) for c in certs)
                at = min(max(now, lo), hi) if lo <= hi else now
                try:
                    built = V.PolicyBuilder().store(V.Store(roots)).time(at).build_server_verifier(subject)
                    chain = built.verify(leaf, certs[1:])
                    r.chain_valid = True
                    r.chain_depth = len(chain)
                except V.VerificationError as e:
                    r.chain_valid = False
                    top = certs[-1]
                    if _is_self_issued_and_signed(top):
                        why = "chain ends at a root that is not in the trust store"
                    elif len(certs) == 1:
                        why = "server sent no intermediate certificate and the issuer is not in the trust store"
                    else:
                        why = "chain does not lead to a trusted root (missing or untrusted issuer)"
                    add(Finding("chain", "error", f"Trust path failed: {why}", {"reason": str(e), "presented": len(certs)}))

    # --- revocation -------------------------------------------------------------------------
    if check_revocation_online and leaf is not None and r.chain_valid is not False:
        r.checks_run.append("revocation")
        issuer = None
        for cand in certs[1:] + roots:
            try:
                leaf.verify_directly_issued_by(cand)
                issuer = cand
                break
            except Exception:
                continue
        out = check_revocation(leaf, issuer, now=now, fetch=ocsp_fetch, timeout=ocsp_timeout)
        if out.status in ("good", "revoked"):
            r.revoked = out.revoked
            r.revocation_method = "OCSP"
        if out.status == "revoked":
            add(Finding("revoked", "error", f"Certificate is revoked: {out.reason}", {"responder": out.url}))
        elif out.status in ("unknown", "unavailable"):
            add(Finding("revocation_unchecked", "info", f"Revocation status not determined: {out.reason}",
                        {"responder": out.url}))
    return r

