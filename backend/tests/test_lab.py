"""The documented lab scenarios must produce the statuses the lab claims (guards lab/README drift)."""
from app import models
from app.config import settings
from app.database import SessionLocal
from app.devtools import lab
from app.jobs import run_provider_job
from app.providers.network import NetworkProvider, TargetSpec

EXPECTED_STATUS = {
    "healthy": "valid",
    "expiring-3d": "expiring_soon",
    "expired": "expired",
    "wrong-host": "hostname_mismatch",
    "self-signed": "invalid_chain",
    "no-intermediate": "invalid_chain",
    "weak-rsa-1024": "weak_crypto",
    "untrusted-ca": "invalid_chain",
    "wildcard": "valid",
    "ocsp-good": "valid",
    "ocsp-revoked": "revoked",
}


def test_lab_scenarios_produce_the_documented_statuses(tmp_path, monkeypatch):
    scenarios = lab.build(tmp_path, base_port=0)
    monkeypatch.setattr(settings, "TRUST_STORE_EXTRA_PEM", str(tmp_path / "ca.pem"))
    specs = [TargetSpec("127.0.0.1", sc.port, sc.server_name, sc.environment) for sc in scenarios]
    out = run_provider_job(NetworkProvider(specs, timeout=5), triggered_by="test")
    assert out["status"] == "succeeded" and out["discovered"] == len(scenarios), out["errors"]
    db = SessionLocal()
    try:
        for sc, spec in zip(scenarios, specs, strict=True):
            cert = db.query(models.Certificate).filter_by(source_ref=spec.ref).one()
            assert cert.status.value == EXPECTED_STATUS[sc.name], (sc.name, cert.status)
            assert cert.environment == sc.environment
        ok = db.query(models.Certificate).filter_by(source_ref=specs[9].ref).one()
        assert ok.revoked is False and ok.revocation_check_method == "OCSP"
        bad = db.query(models.Certificate).filter_by(source_ref=specs[10].ref).one()
        assert bad.revoked is True
        assert bad.risk_tier.value == "critical"  # was 'medium' before explicit floors for failing certificates
        healthy = db.query(models.Certificate).filter_by(source_ref=specs[0].ref).one()
        assert healthy.risk_tier.value in ("low", "medium") and healthy.predicted_renewal_delay_days is None
    finally:
        db.close()
