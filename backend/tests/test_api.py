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


def test_me_endpoint_reports_username_and_role(client, admin_headers, viewer_headers):
    assert client.get("/api/auth/me", headers=admin_headers).json() == {"username": "admin", "role": "admin"}
    assert client.get("/api/auth/me", headers=viewer_headers).json()["role"] == "viewer"
    assert client.get("/api/auth/me").status_code == 401


def test_certificate_list_pages_with_a_total_count_header(client, admin_headers):
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    db.add_all([models.Certificate(common_name=f"paging-{i}.test") for i in range(5)])
    db.commit()
    db.close()
    full = client.get("/api/certificates", headers=admin_headers)
    total = int(full.headers["X-Total-Count"])
    assert total == len(full.json())
    page1 = client.get("/api/certificates?limit=2&offset=0", headers=admin_headers)
    page2 = client.get("/api/certificates?limit=2&offset=2", headers=admin_headers)
    assert int(page1.headers["X-Total-Count"]) == total and len(page1.json()) == 2
    assert {c["id"] for c in page1.json()}.isdisjoint({c["id"] for c in page2.json()})
    assert client.get("/api/certificates?limit=0", headers=admin_headers).status_code == 422


def test_expiry_timeline_buckets_cover_every_dated_certificate(client, admin_headers):
    r = client.get("/api/risk/expiry-timeline", headers=admin_headers).json()["buckets"]
    assert [b["label"] for b in r][0] == "expired" and r[-1]["label"] == "> 90 d"
    assert all(b["total"] == sum(b[t] for t in ("low", "medium", "high", "critical", "unscored")) for b in r)
    dated = client.get("/api/certificates", headers=admin_headers).json()
    assert sum(b["total"] for b in r) == sum(1 for c in dated if c["not_after"])


def test_renewal_history_filters_and_certificate_names(client, admin_headers):
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    cert = models.Certificate(common_name="history.test")
    db.add(cert)
    db.commit()
    db.add_all([models.RenewalTask(certificate_id=cert.id, status=models.RenewalTaskStatus.DRY_RUN, dry_run=True),
                models.RenewalTask(certificate_id=cert.id, status=models.RenewalTaskStatus.FAILED, dry_run=False)])
    db.commit()
    cid = cert.id
    db.close()
    rows = client.get(f"/api/renewals?certificate_id={cid}", headers=admin_headers).json()
    assert len(rows) == 2 and {r["certificate_name"] for r in rows} == {"history.test"}
    real = client.get(f"/api/renewals?certificate_id={cid}&include_dry_runs=false", headers=admin_headers).json()
    assert [r["status"] for r in real] == ["failed"]
    assert client.get(f"/api/renewals?certificate_id={cid}&status=dry_run", headers=admin_headers).json()[0]["dry_run"] is True
    assert client.get("/api/renewals?status=bogus", headers=admin_headers).status_code == 422
