from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.ml.scoring import load_artifact, predict_raw, score_certificate
from app.timeutil import days_until

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/dashboard", response_model=schemas.DashboardStats)
def dashboard_stats(db: Session = Depends(get_db), user=Depends(get_current_user)):
    certs = db.query(models.Certificate).all()
    total = len(certs)
    expiring_30 = sum(1 for c in certs if c.status == models.CertStatus.EXPIRING_SOON)
    expiring_7 = sum(
        1 for c in certs
        if c.not_after and 0 <= days_until(c.not_after) <= 7
    )
    expired = sum(1 for c in certs if c.status == models.CertStatus.EXPIRED)
    invalid_chain = sum(1 for c in certs if c.status == models.CertStatus.INVALID_CHAIN)
    high_risk = sum(1 for c in certs if c.risk_tier in (models.RiskTier.HIGH, models.RiskTier.CRITICAL))
    auto_renew = sum(1 for c in certs if c.auto_renew_enabled)

    security_score = 100.0
    if total:
        penalty = (expired * 10 + invalid_chain * 8 + high_risk * 5) / total
        security_score = max(0.0, 100.0 - penalty)

    return schemas.DashboardStats(
        total_certificates=total, expiring_30d=expiring_30, expiring_7d=expiring_7,
        expired=expired, invalid_chain=invalid_chain, high_risk=high_risk,
        auto_renew_enabled=auto_renew, security_score=round(security_score, 1),
    )


@router.get("/model")
def model_info(user=Depends(get_current_user)):
    """Which model is serving, what it was trained on, and its held-out (synthetic-data) metrics."""
    art = load_artifact()
    if art is None:
        return {"model_used": "rule_baseline", "data_source": "none",
                "note": "No trained artifact found; run `python -m app.ml.train`. Scores come from an unvalidated rule."}
    meta = {k: v for k, v in art["metadata"].items() if k != "reference"}
    return {"model_used": "gradient_boosting", **meta}


@router.post("/predict", response_model=schemas.RiskPredictionOut)
def predict(payload: schemas.RiskPredictIn, user=Depends(get_current_user)):
    """What-if prediction from raw attributes (no database access)."""
    return predict_raw(payload.model_dump())


@router.get("/certificates/{cert_id}", response_model=schemas.RiskPredictionOut)
def certificate_risk(cert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Risk probability, tier and the top factors for one stored certificate."""
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    return score_certificate(db, cert)


TIMELINE_BUCKETS = [("expired", None, -1), ("0-7 d", 0, 7), ("8-14 d", 8, 14), ("15-30 d", 15, 30),
                    ("31-60 d", 31, 60), ("61-90 d", 61, 90), ("> 90 d", 91, None)]


@router.get("/expiry-timeline")
def expiry_timeline(environment: str | None = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Certificates per time-to-expiry bucket, split by risk tier (for the dashboard chart)."""
    q = db.query(models.Certificate.not_after, models.Certificate.risk_tier)
    if environment:
        q = q.filter(models.Certificate.environment == environment.lower())
    buckets = [{"label": label, "low": 0, "medium": 0, "high": 0, "critical": 0, "unscored": 0, "total": 0}
               for label, _, _ in TIMELINE_BUCKETS]
    for not_after, tier in q.all():
        d = days_until(not_after)
        if d is None:
            continue
        for b, (_, lo, hi) in zip(buckets, TIMELINE_BUCKETS, strict=True):
            if (lo is None or d >= lo) and (hi is None or d <= hi):
                b[tier.value if tier else "unscored"] += 1
                b["total"] += 1
                break
    return {"buckets": buckets}
