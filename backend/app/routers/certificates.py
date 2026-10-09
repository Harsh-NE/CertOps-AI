from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db
from app.services.cert_scanner import CertScanError
from app.services.pipeline import rescore_certificate, scan_and_process_network_target

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


@router.get("", response_model=List[schemas.CertificateOut])
def list_certificates(
    status: Optional[str] = None,
    risk_tier: Optional[str] = None,
    source: Optional[str] = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    q = db.query(models.Certificate)
    if status:
        q = q.filter(models.Certificate.status == status)
    if risk_tier:
        q = q.filter(models.Certificate.risk_tier == risk_tier)
    if source:
        q = q.filter(models.Certificate.source == source)
    return q.order_by(models.Certificate.not_after.asc().nulls_last()).all()


@router.get("/{cert_id}", response_model=schemas.CertificateOut)
def get_certificate(cert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")
    return cert


@router.post("/scan", response_model=List[schemas.CertificateOut])
def scan_targets(payload: schemas.ScanTargetRequest, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    """Scan one or more network targets (host or host:port) right now."""
    results = []
    errors = []
    for target in payload.targets:
        try:
            cert = scan_and_process_network_target(db, target)
            results.append(cert)
        except CertScanError as e:
            errors.append({"target": target, "error": str(e)})
    if errors and not results:
        raise HTTPException(422, detail={"errors": errors})
    return results


@router.post("/{cert_id}/rescore", response_model=schemas.CertificateOut)
def rescore(cert_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")
    return rescore_certificate(db, cert)


@router.post("/{cert_id}/toggle-auto-renew", response_model=schemas.CertificateOut)
def toggle_auto_renew(cert_id: str, provider: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")
    cert.auto_renew_enabled = not cert.auto_renew_enabled
    cert.renewal_provider = provider if cert.auto_renew_enabled else None
    db.commit()
    db.refresh(cert)
    return cert
