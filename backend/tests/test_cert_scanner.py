"""
Basic tests for pure-logic pieces that don't require network/cloud access.
Run with: pytest (from the backend/ directory)
"""
from datetime import datetime, timedelta
from app.services.cert_scanner import days_until_expiry
from app.services.risk_ml import extract_features, _heuristic_score


def test_days_until_expiry_future():
    future = datetime.utcnow() + timedelta(days=10)
    assert 8 <= days_until_expiry(future) <= 10


def test_days_until_expiry_past():
    past = datetime.utcnow() - timedelta(days=5)
    assert days_until_expiry(past) < 0


def test_heuristic_score_flags_weak_crypto():
    cert = {
        "not_after": datetime.utcnow() + timedelta(days=200),
        "signature_algorithm": "sha1WithRSAEncryption",
        "key_size": 1024,
        "chain_valid": True,
        "revoked": False,
        "owner_id": None,
        "auto_renew_enabled": False,
        "san_list": None,
    }
    features = extract_features(cert)
    result = _heuristic_score(features)
    assert result["risk_tier"] in ("high", "critical", "medium")
    assert result["risk_score"] > 20


def test_heuristic_score_healthy_cert_is_low_risk():
    cert = {
        "not_after": datetime.utcnow() + timedelta(days=200),
        "signature_algorithm": "sha256WithRSAEncryption",
        "key_size": 2048,
        "chain_valid": True,
        "revoked": False,
        "owner_id": "owner-1",
        "auto_renew_enabled": True,
        "san_list": None,
    }
    features = extract_features(cert)
    result = _heuristic_score(features)
    assert result["risk_tier"] == "low"
