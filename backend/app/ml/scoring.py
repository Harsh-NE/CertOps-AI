"""
Serving-side risk scoring.

risk probability = P(certificate causes a service problem within 30 days), from the calibrated model trained on
SYNTHETIC data (or a documented rule baseline if no model artifact exists). Certificates that are *already failing*
(expired, revoked, invalid chain, ...) are not forecasts, so explicit floors apply: final = max(model, floor).
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app import models
from app.ml.features import FEATURES, build_features, to_frame
from app.ml.train import DEFAULT_ARTIFACT
from app.timeutil import days_until

# Policy (not statistically derived): tiers on the calibrated probability. Base rate in training data is ~6%.
TIER_BOUNDS = [(0.05, "low"), (0.15, "medium"), (0.40, "high")]  # >= 0.40 -> critical
FLOORS = {  # certificate state -> (minimum tier, minimum probability)
    models.CertStatus.REVOKED: ("critical", 0.95),
    models.CertStatus.EXPIRED: ("critical", 0.95),
    models.CertStatus.INVALID_CHAIN: ("high", 0.50),
    models.CertStatus.HOSTNAME_MISMATCH: ("medium", 0.20),
    models.CertStatus.WEAK_CRYPTO: ("medium", 0.20),
}
TIER_ORDER = ["low", "medium", "high", "critical"]
PROD_NAMES = {"prod", "production", "prd"}


@lru_cache(maxsize=1)
def load_artifact(path: str = str(DEFAULT_ARTIFACT)) -> Optional[Dict[str, Any]]:
    p = Path(path)
    return joblib.load(p) if p.exists() else None  # our own file; never load user-supplied pickles


def reload_artifact() -> None:
    load_artifact.cache_clear()


def tier_for(prob: float) -> str:
    for bound, name in TIER_BOUNDS:
        if prob < bound:
            return name
    return "critical"


def flags_from_findings(findings: List[dict]) -> Dict[str, bool]:
    checks = {f["check"] for f in findings}
    return {"weak_signature": "weak_signature" in checks, "hostname_mismatch": "hostname_mismatch" in checks,
            "chain_invalid": bool(checks & {"chain", "self_signed"})}


def raw_from_certificate(db: Session, cert: models.Certificate, flags: Dict[str, bool]) -> Dict[str, Any]:
    d = days_until(cert.not_after)
    validity = (cert.not_after - cert.not_before).days if cert.not_after and cert.not_before else None
    tasks = (db.query(models.RenewalTask.status).filter(
        models.RenewalTask.certificate_id == cert.id, models.RenewalTask.dry_run.isnot(True)).all())
    done = {models.RenewalTaskStatus.AUTO_RENEWED, models.RenewalTaskStatus.COMPLETED}
    failed = sum(1 for (s,) in tasks if s == models.RenewalTaskStatus.FAILED)
    attempts = failed + sum(1 for (s,) in tasks if s in done)
    sans = json.loads(cert.san_list) if cert.san_list else []
    return {
        "days_to_expiry": d if d is not None else 365, "validity_days": validity, "key_size": cert.key_size,
        "chain_depth": cert.chain_depth, "san_count": len(sans), "is_wildcard": cert.is_wildcard,
        "auto_renew": cert.auto_renew_enabled, "has_owner": bool(cert.owner_id),
        "is_prod": (cert.environment or "").lower() in PROD_NAMES,
        "issuer": cert.issuer, "subject": cert.subject, "key_algorithm": cert.key_algorithm,
        "source": cert.source.value if cert.source else None,
        "prior_attempts": attempts, "prior_failures": failed, **flags,
    }


def _rule_baseline_probability(days: float) -> float:
    """Used only when no trained artifact exists. An unvalidated rule of thumb, not a model."""
    return 0.35 if days <= 7 else 0.20 if days <= 30 else 0.04


def predict_raw(raw: Dict[str, Any], explain: bool = True) -> Dict[str, Any]:
    art = load_artifact()
    feats = build_features(raw)
    if art is None:
        prob, used, version, factors = _rule_baseline_probability(feats["days_to_expiry"]), "rule_baseline", None, []
    else:
        meta = art["metadata"]
        frame = pd.DataFrame([feats])[FEATURES]
        prob = float(art["model"].predict_proba(frame)[0, 1])
        used, version = "gradient_boosting", meta["version"]
        factors = _ablation_factors(art, feats, prob) if explain else []
    return {"probability": round(prob, 4), "tier": tier_for(prob), "model_used": used, "model_version": version,
            "data_source": "synthetic" if art else "none", "factors": factors}


def _ablation_factors(art: Dict[str, Any], feats: Dict[str, Any], prob: float, top: int = 4) -> List[Dict[str, Any]]:
    """Which features push this prediction up? Replace each by its typical value and measure the change.
    A what-if ablation, not SHAP: interactions are attributed crudely."""
    ref = art["metadata"]["reference"]
    rows = []
    for f in FEATURES:
        row = dict(feats)
        row[f] = ref[f]
        rows.append(row)
    p_ref = art["model"].predict_proba(pd.DataFrame(rows)[FEATURES])[:, 1]
    out = [{"feature": f, "value": feats[f], "typical": ref[f], "effect": round(float(prob - p), 4)}
           for f, p in zip(FEATURES, p_ref, strict=True)]
    return [d for d in sorted(out, key=lambda d: -d["effect"]) if d["effect"] > 0.002][:top]


def score_certificate(db: Session, cert: models.Certificate, flags: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    if flags is None:
        latest = (db.query(models.ValidationResult).filter_by(certificate_id=cert.id)
                  .order_by(models.ValidationResult.checked_at.desc()).first())
        flags = flags_from_findings(json.loads(latest.findings)) if latest else {}
    raw = raw_from_certificate(db, cert, flags)
    pred = predict_raw(raw)
    prob, tier = pred["probability"], pred["tier"]
    floor = FLOORS.get(cert.status)
    floor_reason = None
    if floor and prob < floor[1]:
        prob, floor_reason = floor[1], f"certificate is {cert.status.value}"
        if TIER_ORDER.index(floor[0]) > TIER_ORDER.index(tier):
            tier = floor[0]
    return {**pred, "probability": prob, "tier": tier, "risk_score": round(prob * 100, 1),
            "floor_reason": floor_reason, "predicted_outage_impact": outage_impact(cert, raw)}


def outage_impact(cert: models.Certificate, raw: Dict[str, Any]) -> str:
    """Rule-based blast-radius estimate (NOT a model prediction)."""
    d, prod = raw["days_to_expiry"], raw["is_prod"]
    failing = cert.status in (models.CertStatus.EXPIRED, models.CertStatus.REVOKED, models.CertStatus.INVALID_CHAIN)
    if failing or (prod and d <= 7):
        return "high"
    return "medium" if (prod and d <= 30) or d <= 7 else "low"


def frame_from_raws(raws: List[Dict[str, Any]]) -> pd.DataFrame:
    return to_frame(raws)
