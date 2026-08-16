"""
Predictive Risk Analysis.

Two lightweight scikit-learn models trained on synthetic-but-realistic
historical data (see train_risk_model.py):

  1. RiskClassifier      -> risk_tier in {low, medium, high, critical} + risk_score (0-100)
  2. RenewalDelayRegressor -> predicted days of delay before a renewal actually
                               completes, based on historical owner/team behaviour

Models are trained once (via `python -m app.services.train_risk_model`) and
persisted to backend/app/ml_models/*.joblib. If no trained model is found,
`score_certificate` falls back to a transparent rule-based heuristic so the
API never breaks on a fresh checkout.
"""
import os
from datetime import datetime
from typing import Dict, Any, Optional

import joblib
import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "ml_models")
CLASSIFIER_PATH = os.path.join(MODEL_DIR, "risk_classifier.joblib")
REGRESSOR_PATH = os.path.join(MODEL_DIR, "renewal_delay_regressor.joblib")

RISK_TIER_LABELS = ["low", "medium", "high", "critical"]


def _load_model(path):
    if os.path.exists(path):
        return joblib.load(path)
    return None


_classifier = None
_regressor = None


def _get_models():
    global _classifier, _regressor
    if _classifier is None:
        _classifier = _load_model(CLASSIFIER_PATH)
    if _regressor is None:
        _regressor = _load_model(REGRESSOR_PATH)
    return _classifier, _regressor


def extract_features(cert: Dict[str, Any]) -> Dict[str, float]:
    """
    Builds the numeric feature vector used by both models from a certificate
    record (works with either an ORM object or a plain dict with the same
    attribute names).
    """
    def g(key, default=None):
        return cert.get(key) if isinstance(cert, dict) else getattr(cert, key, default)

    not_after = g("not_after")
    days_to_expiry = 9999
    if not_after:
        na = not_after.replace(tzinfo=None) if getattr(not_after, "tzinfo", None) else not_after
        days_to_expiry = (na - datetime.utcnow()).days

    sig_algo = (g("signature_algorithm") or "").lower()
    weak_sig = 1.0 if any(w in sig_algo for w in ("sha1", "md5")) else 0.0

    key_size = g("key_size") or 2048
    weak_key = 1.0 if key_size < 2048 else 0.0

    chain_valid = g("chain_valid")
    chain_invalid_flag = 1.0 if chain_valid is False else 0.0

    revoked = g("revoked")
    revoked_flag = 1.0 if revoked is True else 0.0

    has_owner = 1.0 if g("owner_id") or g("owner") else 0.0

    auto_renew = 1.0 if g("auto_renew_enabled") else 0.0

    is_wildcard_or_multisan = 1.0 if (g("san_list") and len(str(g("san_list"))) > 20) else 0.0

    return {
        "days_to_expiry": float(min(max(days_to_expiry, -365), 9999)),
        "weak_signature": weak_sig,
        "weak_key": weak_key,
        "chain_invalid": chain_invalid_flag,
        "revoked": revoked_flag,
        "has_owner": has_owner,
        "auto_renew_enabled": auto_renew,
        "multi_san": is_wildcard_or_multisan,
    }


FEATURE_ORDER = [
    "days_to_expiry", "weak_signature", "weak_key",
    "chain_invalid", "revoked", "has_owner", "auto_renew_enabled", "multi_san",
]


def _heuristic_score(features: Dict[str, float]) -> Dict[str, Any]:
    """Transparent fallback used until a model has been trained."""
    score = 0.0
    d = features["days_to_expiry"]
    if d < 0:
        score += 45
    elif d <= 7:
        score += 35
    elif d <= 30:
        score += 20
    elif d <= 60:
        score += 8

    score += features["weak_signature"] * 20
    score += features["weak_key"] * 15
    score += features["chain_invalid"] * 25
    score += features["revoked"] * 40
    score += (1 - features["has_owner"]) * 10
    score -= features["auto_renew_enabled"] * 10
    score = float(np.clip(score, 0, 100))

    if score >= 70:
        tier = "critical"
    elif score >= 45:
        tier = "high"
    elif score >= 20:
        tier = "medium"
    else:
        tier = "low"

    predicted_delay = max(0.0, 10 - features["has_owner"] * 6 - features["auto_renew_enabled"] * 8)
    impact = "high" if (features["revoked"] or features["chain_invalid"] or d < 3) else (
        "medium" if d < 14 else "low"
    )
    return {
        "risk_score": round(score, 1),
        "risk_tier": tier,
        "predicted_renewal_delay_days": round(predicted_delay, 1),
        "predicted_outage_impact": impact,
        "model_used": "heuristic_fallback",
    }


def score_certificate(cert: Dict[str, Any]) -> Dict[str, Any]:
    features = extract_features(cert)
    classifier, regressor = _get_models()

    if classifier is None or regressor is None:
        return _heuristic_score(features)

    X = np.array([[features[f] for f in FEATURE_ORDER]])
    proba = classifier.predict_proba(X)[0]
    tier_idx = int(np.argmax(proba))
    tier = classifier.classes_[tier_idx]
    # Map tier probabilities to a continuous 0-100 score for the dashboard
    tier_weight = {"low": 10, "medium": 40, "high": 70, "critical": 95}
    risk_score = float(sum(p * tier_weight.get(c, 50) for p, c in zip(proba, classifier.classes_)))

    delay_days = float(max(0.0, regressor.predict(X)[0]))
    d = features["days_to_expiry"]
    impact = "high" if (features["revoked"] or features["chain_invalid"] or d < 3) else (
        "medium" if d < 14 else "low"
    )

    return {
        "risk_score": round(risk_score, 1),
        "risk_tier": str(tier),
        "predicted_renewal_delay_days": round(delay_days, 1),
        "predicted_outage_impact": impact,
        "model_used": "gradient_boosting",
    }
