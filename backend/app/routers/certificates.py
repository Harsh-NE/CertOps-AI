import json
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db
from app.jobs import run_provider_job
from app.providers.network import NetworkProvider, TargetSpec, parse_target
from app.services.pipeline import revalidate_certificate
from app.timeutil import utcnow

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


@router.get("", response_model=List[schemas.CertificateOut])
def list_certificates(
    status: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
    environment: Optional[str] = None,
    expires_within_days: Optional[int] = None,
    q: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    response: Response = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    query = db.query(models.Certificate)
    try:
        if status:
            query = query.filter(models.Certificate.status == models.CertStatus(status))
        if risk_tier:
            query = query.filter(models.Certificate.risk_tier == models.RiskTier(risk_tier))
        if source:
            query = query.filter(models.Certificate.source == models.SourceType(source))
    except ValueError as e:
        raise HTTPException(422, f"invalid filter value: {e}") from e
    if environment:
        query = query.filter(models.Certificate.environment == environment.lower())
    if expires_within_days is not None:
        query = query.filter(models.Certificate.not_after <= utcnow() + timedelta(days=expires_within_days))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(models.Certificate.common_name.ilike(like), models.Certificate.issuer.ilike(like),
                                 models.Certificate.source_ref.ilike(like)))
    response.headers["X-Total-Count"] = str(query.count())
    query = query.order_by(models.Certificate.not_after.asc().nulls_last(), models.Certificate.id)
    if limit is not None:
        query = query.offset(offset).limit(limit)
    return query.all()


@router.get("/{cert_id}", response_model=schemas.CertificateOut)
def get_certificate(cert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    return cert


@router.get("/{cert_id}/validation", response_model=List[schemas.ValidationOut])
def get_validation_history(cert_id: str, limit: int = 20, db: Session = Depends(get_db),
                           user=Depends(get_current_user)):
    if not db.get(models.Certificate, cert_id):
        raise HTTPException(404, "Certificate not found")
    rows = (db.query(models.ValidationResult).filter_by(certificate_id=cert_id)
            .order_by(models.ValidationResult.checked_at.desc()).limit(limit).all())
    return [schemas.ValidationOut(id=r.id, checked_at=r.checked_at, overall=r.overall, chain_valid=r.chain_valid,
                                  revoked=r.revoked, findings=json.loads(r.findings)) for r in rows]


def _to_spec(t) -> TargetSpec:
    if isinstance(t, str):
        return parse_target(t)
    return TargetSpec(host=t.host, port=t.port, server_name=t.server_name, environment=t.environment)


@router.post("/scan", response_model=List[schemas.CertificateOut])
def scan_targets(payload: schemas.ScanTargetRequest, db: Session = Depends(get_db),
                 user=Depends(require_role("operator"))):
    """Register one or more TLS endpoints as monitored targets and scan them now."""
    specs, ids = [], []
    for t in payload.targets:
        try:
            spec = _to_spec(t)
        except ValueError as e:
            raise HTTPException(422, f"invalid target {t!r}: {e}") from e
        row = (db.query(models.ScanTarget)
               .filter_by(host=spec.host, port=spec.port, server_name=spec.server_name).first())
        if row is None:
            row = models.ScanTarget(host=spec.host, port=spec.port, server_name=spec.server_name,
                                    environment=spec.environment)
            db.add(row)
            db.commit()
        elif spec.environment and row.environment != spec.environment:
            row.environment = spec.environment
            db.commit()
        spec.target_id, spec.environment = row.id, row.environment
        specs.append(spec)
        ids.append(row.id)
    provider = NetworkProvider(specs)
    summary = run_provider_job(provider, triggered_by=f"user:{user.username}")
    certs = (db.query(models.Certificate)
             .filter(models.Certificate.source == models.SourceType.NETWORK_SCAN,
                     models.Certificate.source_ref.in_([s.ref for s in specs])).all())
    if not certs:
        raise HTTPException(422, detail={"errors": summary["errors"], "job_id": summary["job_id"]})
    return certs


@router.post("/{cert_id}/rescore", response_model=schemas.CertificateOut)
def rescore(cert_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    """Re-run validation from stored data (no network access) and refresh status + risk."""
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    return revalidate_certificate(db, cert)


@router.post("/{cert_id}/toggle-auto-renew", response_model=schemas.CertificateOut)
def toggle_auto_renew(cert_id: str, provider: str, db: Session = Depends(get_db),
                      user=Depends(require_role("operator"))):
    if provider not in ("letsencrypt", "aws_acm"):
        raise HTTPException(422, "provider must be one of letsencrypt, aws_acm")
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    cert.auto_renew_enabled = not cert.auto_renew_enabled
    cert.renewal_provider = provider if cert.auto_renew_enabled else None
    db.commit()
    return revalidate_certificate(db, cert)  # auto_renew is a risk feature: refresh the stored score
