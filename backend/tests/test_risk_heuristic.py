from datetime import timedelta

from app.services.risk_ml import _heuristic_score, extract_features
from app.timeutil import utcnow


def _cert(**kw):
    base = {"not_after": utcnow() + timedelta(days=200), "signature_algorithm": "sha256WithRSAEncryption",
            "key_size": 2048, "chain_valid": True, "revoked": False, "owner_id": "o", "auto_renew_enabled": True,
            "san_list": None}
    base.update(kw)
    return base


def test_heuristic_score_flags_weak_crypto():
    result = _heuristic_score(extract_features(_cert(signature_algorithm="sha1WithRSAEncryption", key_size=1024,
                                                     owner_id=None, auto_renew_enabled=False)))
    assert result["risk_tier"] in ("medium", "high", "critical") and result["risk_score"] > 20


def test_heuristic_score_healthy_cert_is_low_risk():
    assert _heuristic_score(extract_features(_cert()))["risk_tier"] == "low"
