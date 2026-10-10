import datetime as dt
import os
import socket
import ssl
import tempfile
import threading

import pytest

# Environment must be set before the app is imported.
_TMP = tempfile.mkdtemp(prefix="certops-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
    "SCHEDULER_ENABLED": "false",
    "SECRET_KEY": "test-secret-key-not-for-production",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin-pass-for-tests",
    "ALLOW_REGISTRATION": "false",
    "ALLOW_PRIVATE_TARGETS": "true",  # tests talk to 127.0.0.1; test_netguard resets this explicitly
    "JOB_BACKOFF_SECONDS": "0",
    "ENV": "test",
})

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def make_cert(cn="test.example", days_from=-1, days_to=90, key=None, sans=None):
    """Self-signed SHA-256 test certificate. Returns (private_key, certificate)."""
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now + dt.timedelta(days=days_from))
        .not_valid_after(now + dt.timedelta(days=days_to))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in (sans or [cn])]), False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def serve_tls(key, cert, *chain, weak: bool = False):
    """Serve `cert` (+ optional intermediates) on 127.0.0.1:<random port> in a daemon thread; returns the port."""
    d = tempfile.mkdtemp(dir=_TMP)
    cp, kp = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    with open(cp, "wb") as f:
        f.write(b"".join(c.public_bytes(serialization.Encoding.PEM) for c in (cert, *chain)))
    with open(kp, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if weak:
        ctx.set_ciphers("ALL:@SECLEVEL=0")  # allow serving a 1024-bit key
    ctx.load_cert_chain(cp, kp)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)

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


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    from app import models  # noqa: F401
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="session")
def client(_create_tables):
    from app.main import app
    with TestClient(app) as c:  # runs lifespan: bootstraps the admin user
        yield c


def _login(client, username, password):
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client):
    return _login(client, "admin", "admin-pass-for-tests")


@pytest.fixture(scope="session")
def viewer_headers(client):
    from app import models
    from app.auth import hash_password
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if not db.query(models.User).filter_by(username="viewer1").first():
            db.add(models.User(username="viewer1", hashed_password=hash_password("viewer-pass"), role="viewer"))
            db.commit()
    finally:
        db.close()
    return _login(client, "viewer1", "viewer-pass")


@pytest.fixture()
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(autouse=True)
def _hermetic_risk_model(monkeypatch):
    """
    Tests must not depend on a locally trained model file (it is gitignored, so CI never has one).
    By default no artifact is loaded; tests that need a model use the `serving` fixture, which trains a small one.
    """
    from app.ml import scoring
    monkeypatch.setattr(scoring, "load_artifact", lambda: None)
