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


def test_self_signed_scan_is_reported_invalid(client, admin_headers):
    """A self-signed cert must never be reported valid (the original code said chain_valid=True)."""
    key, cert = make_cert("scan.test", sans=["scan.test", "*.scan.test"])
    port = serve_tls(key, cert)
    r = client.post("/api/certificates/scan", json={"targets": [f"127.0.0.1:{port}#scan.test"]}, headers=admin_headers)
    assert r.status_code == 200, r.text
    c = r.json()[0]
    assert c["chain_valid"] is False and c["status"] == "invalid_chain"
    assert c["key_algorithm"] == "RSA" and c["key_size"] == 2048 and c["is_wildcard"] is True
    assert len(c["fingerprint_sha256"]) == 64


def test_create_owner_does_not_need_an_id(client, admin_headers):
    r = client.post("/api/owners", json={"name": "Platform Team", "team": "platform"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["id"]
