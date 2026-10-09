from app import models
from app.database import SessionLocal
from tests.conftest import make_cert, serve_tls


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["registration_enabled"] is False


def test_requires_auth(client):
    assert client.get("/api/certificates").status_code == 401


def test_registration_disabled_by_default(client):
    r = client.post("/api/auth/register", json={"username": "x", "password": "y"})
    assert r.status_code == 403


def test_bad_login(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_viewer_cannot_scan_or_discover(client, viewer_headers):
    assert client.post("/api/certificates/scan", json={"targets": ["127.0.0.1:1"]}, headers=viewer_headers).status_code == 403
    assert client.post("/api/cloud/discover", json={"providers": ["aws_acm"]}, headers=viewer_headers).status_code == 403
    assert client.get("/api/certificates", headers=viewer_headers).status_code == 200


def test_scan_reports_chain_as_not_validated(client, admin_headers):
    """A self-signed cert must never be reported chain_valid=True (it used to be)."""
    key, cert = make_cert("scan.test", sans=["scan.test", "*.scan.test"])
    port = serve_tls(key, cert)
    r = client.post("/api/certificates/scan", json={"targets": [f"127.0.0.1:{port}"]}, headers=admin_headers)
    assert r.status_code == 200, r.text
    c = r.json()[0]
    assert c["chain_valid"] is None
    assert c["key_algorithm"] == "RSA" and c["key_size"] == 2048
    assert c["is_wildcard"] is True
    assert len(c["fingerprint_sha256"]) == 64
    assert c["status"] == "valid"


def _make_db_cert(provider="letsencrypt"):
    db = SessionLocal()
    try:
        cert = models.Certificate(common_name="renew.test", renewal_provider=provider, auto_renew_enabled=True)
        db.add(cert)
        db.commit()
        return cert.id
    finally:
        db.close()


def test_renew_defaults_to_dry_run_and_calls_no_provider(client, admin_headers, monkeypatch):
    import app.routers.renewals as renewals

    def boom(*a, **k):
        raise AssertionError("provider called during dry run")

    monkeypatch.setattr(renewals, "trigger_renewal", boom)
    cid = _make_db_cert()
    r = client.post(f"/api/renewals/{cid}/renew-now", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run" and r.json()["dry_run"] is True


def test_real_renewal_requires_confirmation(client, admin_headers, monkeypatch):
    import app.routers.renewals as renewals
    calls = []

    def fake(provider, **kwargs):
        calls.append(provider)
        return {"success": True, "serial_number": "ab"}

    monkeypatch.setattr(renewals, "trigger_renewal", fake)
    cid = _make_db_cert()
    r = client.post(f"/api/renewals/{cid}/renew-now?dry_run=false", headers=admin_headers)
    assert r.status_code == 400 and calls == []
    r = client.post(f"/api/renewals/{cid}/renew-now?dry_run=false&confirm=true", headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "auto_renewed" and calls == ["letsencrypt"]
    assert r.json()["new_serial"] == "ab"


def test_acm_never_claims_a_renewal_happened(client, admin_headers, monkeypatch):
    from app.services.cloud_connectors import aws_acm

    class FakeAcm:
        def describe_certificate(self, CertificateArn):
            return {"Certificate": {"RenewalEligibility": "ELIGIBLE"}}

    monkeypatch.setattr(aws_acm, "_client", lambda: FakeAcm())
    cid = _make_db_cert("aws_acm")
    r = client.post(f"/api/renewals/{cid}/renew-now?dry_run=false&confirm=true", headers=admin_headers)
    assert r.json()["status"] == "skipped"


def test_aws_discovery_is_opt_in(monkeypatch):
    from app.services.cloud_connectors import aws_acm

    def boom():
        raise AssertionError("boto3 client created while AWS_DISCOVERY_ENABLED=false")

    monkeypatch.setattr(aws_acm, "_client", boom)
    assert aws_acm.discover_certificates() == []


def test_create_owner_does_not_need_an_id(client, admin_headers):
    r = client.post("/api/owners", json={"name": "Platform Team", "team": "platform"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["id"]
