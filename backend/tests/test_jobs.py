import json
from pathlib import Path

import pytest

from app import models
from app.config import settings
from app.database import SessionLocal
from app.devtools import pki
from app.jobs import run_provider_job
from app.providers.base import (
    CertificateProvider,
    DiscoveryResult,
    ProviderAuthError,
    ProviderNotConfigured,
    TransientProviderError,
)
from app.providers.normalize import normalize_x509
from app.services.pipeline import revalidate_certificate
from tests.conftest import serve_tls


class ScriptedProvider(CertificateProvider):
    """Fails/succeeds according to a script of outcomes, one per discover() call."""

    name = "aws_acm"

    def __init__(self, script, configured=True):
        self.script, self.calls, self.configured = list(script), 0, configured

    def is_configured(self):
        return self.configured

    def discover(self):
        self.calls += 1
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(scope="module")
def inter():
    return pki.make_root().issue_intermediate("Jobs Intermediate")


def norm(inter, cn, ref, **kw):
    _, leaf = inter.issue_leaf(cn, **kw)
    return normalize_x509(leaf, provider="aws_acm", source_ref=ref, chain=[leaf, inter.cert])


def last_run(job_id):
    db = SessionLocal()
    try:
        return db.get(models.JobRun, job_id)
    finally:
        db.close()


def test_transient_errors_are_retried_with_exponential_backoff(inter):
    delays = []
    ok = DiscoveryResult(certificates=[norm(inter, "retry.test", "ref-retry")])
    provider = ScriptedProvider([TransientProviderError("throttled"), TransientProviderError("timeout"), ok])
    out = run_provider_job(provider, sleep=delays.append, max_attempts=3, backoff_seconds=1.5)
    assert provider.calls == 3 and delays == [1.5, 3.0]
    assert out["status"] == "succeeded" and out["discovered"] == 1 and out["attempts"] == 3
    run = last_run(out["job_id"])
    assert run.status == models.JobRunStatus.SUCCEEDED and run.finished_at is not None


def test_retries_are_bounded(inter):
    provider = ScriptedProvider([TransientProviderError("down")] * 3)
    out = run_provider_job(provider, sleep=lambda s: None, max_attempts=3, backoff_seconds=0)
    assert provider.calls == 3 and out["status"] == "failed" and len(out["errors"]) == 3
    assert json.loads(last_run(out["job_id"]).errors)[0].startswith("attempt 1/3")


def test_auth_errors_are_not_retried():
    provider = ScriptedProvider([ProviderAuthError("AccessDenied"), DiscoveryResult()])
    out = run_provider_job(provider, sleep=lambda s: None, max_attempts=3)
    assert provider.calls == 1 and out["status"] == "failed" and out["errors"][0].startswith("auth:")


def test_unconfigured_provider_is_skipped():
    provider = ScriptedProvider([], configured=False)
    out = run_provider_job(provider)
    assert out["status"] == "skipped" and provider.calls == 0


def test_not_configured_error_raised_by_provider_is_a_failed_run_with_kind():
    out = run_provider_job(ScriptedProvider([ProviderNotConfigured("no targets")]))
    assert out["status"] == "failed" and out["errors"][0].startswith("config:")


def test_provider_crash_is_contained():
    out = run_provider_job(ScriptedProvider([RuntimeError("bug in adapter")]))
    assert out["status"] == "failed" and "RuntimeError" in out["errors"][0]


def test_one_bad_certificate_does_not_abort_the_run(inter):
    good = norm(inter, "good-item.test", "ref-good")
    bad = norm(inter, "bad-item.test", "ref-bad")
    bad.provider = "not-a-real-provider"  # fails when persisted
    out = run_provider_job(ScriptedProvider([DiscoveryResult(certificates=[bad, good], errors=["ref-x: unreadable"])]))
    assert out["status"] == "partial" and out["discovered"] == 1
    assert any("ref-bad" in e for e in out["errors"]) and "ref-x: unreadable" in out["errors"]
    db = SessionLocal()
    try:
        assert db.query(models.Certificate).filter_by(source_ref="ref-good").count() == 1
        assert db.query(models.Certificate).filter_by(source_ref="ref-bad").count() == 0
    finally:
        db.close()


def test_rediscovery_updates_in_place_and_validation_history_records_changes_only(inter):
    first = norm(inter, "stable.test", "ref-stable")
    run_provider_job(ScriptedProvider([DiscoveryResult(certificates=[first])]))
    run_provider_job(ScriptedProvider([DiscoveryResult(certificates=[first])]))
    db = SessionLocal()
    try:
        cert = db.query(models.Certificate).filter_by(source_ref="ref-stable").one()
        assert db.query(models.ValidationResult).filter_by(certificate_id=cert.id).count() == 1  # no change, no new row
        assert cert.chain_valid is None or cert.chain_valid is False  # the test CA is not in the default trust store
    finally:
        db.close()


def test_revalidation_is_offline_and_derives_status_from_findings(inter):
    expired = norm(inter, "old.test", "ref-old", days_from=-400, days_to=-10)
    run_provider_job(ScriptedProvider([DiscoveryResult(certificates=[expired])]))
    db = SessionLocal()
    try:
        cert = db.query(models.Certificate).filter_by(source_ref="ref-old").one()
        assert cert.status == models.CertStatus.EXPIRED
        assert cert.not_after.tzinfo is not None  # timestamps come back timezone-aware
        revalidate_certificate(db, cert, online=False)
        assert cert.last_validated_at is not None
    finally:
        db.close()


# ---- End to end through the API, against real TLS servers -------------------------------------------------

def test_api_scan_end_to_end_with_lab_style_scenarios(client, admin_headers, tmp_path, monkeypatch):
    root = pki.make_root("E2E Root CA")
    inter = root.issue_intermediate("E2E Intermediate")
    ca_file = tmp_path / "ca.pem"
    ca_file.write_bytes(pki.pem(root.cert))
    monkeypatch.setattr(settings, "TRUST_STORE_EXTRA_PEM", str(Path(ca_file)))

    k1, healthy = inter.issue_leaf("healthy.e2e.test")
    k2, wrong = inter.issue_leaf("somewhere-else.e2e.test")
    k3, expired = inter.issue_leaf("expired.e2e.test", days_from=-400, days_to=-5)
    k4, nochain = inter.issue_leaf("nochain.e2e.test")
    k5, selfsigned = pki.make_self_signed("selfsigned.e2e.test")
    targets = [
        {"host": "127.0.0.1", "port": serve_tls(k1, healthy, inter.cert), "server_name": "healthy.e2e.test", "environment": "prod"},
        {"host": "127.0.0.1", "port": serve_tls(k2, wrong, inter.cert), "server_name": "wrong.e2e.test", "environment": "staging"},
        {"host": "127.0.0.1", "port": serve_tls(k3, expired, inter.cert), "server_name": "expired.e2e.test"},
        {"host": "127.0.0.1", "port": serve_tls(k4, nochain), "server_name": "nochain.e2e.test"},
        {"host": "127.0.0.1", "port": serve_tls(k5, selfsigned), "server_name": "selfsigned.e2e.test"},
    ]
    r = client.post("/api/certificates/scan", json={"targets": targets}, headers=admin_headers)
    assert r.status_code == 200, r.text
    status = {c["expected_hostname"]: c["status"] for c in r.json()}
    assert status == {
        "healthy.e2e.test": "valid",
        "wrong.e2e.test": "hostname_mismatch",
        "expired.e2e.test": "expired",
        "nochain.e2e.test": "invalid_chain",
        "selfsigned.e2e.test": "invalid_chain",
    }
    healthy_row = next(c for c in r.json() if c["expected_hostname"] == "healthy.e2e.test")
    assert healthy_row["chain_valid"] is True and healthy_row["environment"] == "prod" and healthy_row["chain_depth"] == 3

    findings = client.get(f"/api/certificates/{healthy_row['id']}/validation", headers=admin_headers).json()
    assert findings[0]["overall"] == "pass"
    bad = next(c for c in r.json() if c["expected_hostname"] == "nochain.e2e.test")
    msgs = [f["message"] for f in client.get(f"/api/certificates/{bad['id']}/validation", headers=admin_headers).json()[0]["findings"]]
    assert any("no intermediate" in m for m in msgs)

    # persisted run state + target bookkeeping
    runs = client.get("/api/jobs?provider=network_scan", headers=admin_headers).json()
    assert runs[0]["status"] == "succeeded" and runs[0]["discovered"] == 5
    tgts = client.get("/api/targets", headers=admin_headers).json()
    assert {t["server_name"] for t in tgts} >= {"healthy.e2e.test", "selfsigned.e2e.test"}
    assert all(t["last_scanned_at"] and t["last_error"] is None for t in tgts if t["server_name"] and "e2e" in t["server_name"])

    # filters used by the UI (scoped with q=e2e because other tests share the same database)
    def listed(query):
        r = client.get(f"/api/certificates?{query}", headers=admin_headers)
        assert r.status_code == 200, r.text
        return {c["expected_hostname"] for c in r.json()}

    assert listed("q=e2e&environment=prod") == {"healthy.e2e.test"}
    assert listed("q=e2e&status=expired") == {"expired.e2e.test"}
    assert "expired.e2e.test" in listed("q=e2e&expires_within_days=0")
    assert "healthy.e2e.test" not in listed("q=e2e&expires_within_days=0")
    assert listed("q=selfsigned") == {"selfsigned.e2e.test"}
    assert client.get("/api/certificates?status=bogus", headers=admin_headers).status_code == 422


def test_api_scan_of_unreachable_target_returns_422_with_job_id(client, admin_headers):
    r = client.post("/api/certificates/scan", json={"targets": [{"host": "127.0.0.1", "port": 1}]}, headers=admin_headers)
    assert r.status_code == 422 and r.json()["detail"]["job_id"]


def test_api_discover_unknown_and_unconfigured_providers(client, admin_headers):
    r = client.post("/api/cloud/discover", json={"providers": ["nope", "azure_key_vault", "gcp_cert_manager"]},
                    headers=admin_headers).json()
    assert r["nope"]["status"] == "failed"
    assert r["azure_key_vault"]["status"] == "skipped" and r["gcp_cert_manager"]["status"] == "skipped"


def test_api_blocks_private_scan_targets_when_guard_enabled(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", False)
    r = client.post("/api/certificates/scan", json={"targets": ["169.254.169.254:80"]}, headers=admin_headers)
    assert r.status_code == 422 and "not a public address" in json.dumps(r.json())
