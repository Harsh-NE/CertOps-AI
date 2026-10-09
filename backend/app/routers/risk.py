from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
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
