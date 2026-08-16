from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.auth import get_current_user
from app.services.auto_renewal import trigger_renewal
from app.services.notifications.jira import create_renewal_ticket
from app.services.cert_scanner import days_until_expiry

router = APIRouter(prefix="/api/renewals", tags=["renewals"])


@router.get("", response_model=List[schemas.RenewalTaskOut])
def list_renewal_tasks(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.query(models.RenewalTask).order_by(models.RenewalTask.created_at.desc()).all()


@router.post("/{cert_id}/create-ticket", response_model=schemas.RenewalTaskOut)
def create_ticket(cert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")

    d = days_until_expiry(cert.not_after) if cert.not_after else -1
    jira_result = create_renewal_ticket(cert.common_name, d, cert.owner.team if cert.owner else None)

    task = models.RenewalTask(
        certificate_id=cert.id,
        status=models.RenewalTaskStatus.JIRA_CREATED if jira_result.get("created") else models.RenewalTaskStatus.PENDING,
        jira_ticket_key=jira_result.get("key"),
        notes=None if jira_result.get("created") else jira_result.get("reason"),
        triggered_by="manual",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{cert_id}/renew-now", response_model=schemas.RenewalTaskOut)
def renew_now(cert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")
    if not cert.renewal_provider:
        raise HTTPException(400, "No renewal provider configured for this certificate")

    kwargs = {}
    if cert.renewal_provider == "letsencrypt":
        kwargs = {"domain": cert.common_name}
    elif cert.renewal_provider == "vault":
        kwargs = {"pki_role": "default", "common_name": cert.common_name}
    elif cert.renewal_provider == "aws_acm":
        kwargs = {"certificate_arn": cert.source_ref}

    result = trigger_renewal(cert.renewal_provider, **kwargs)

    task = models.RenewalTask(
        certificate_id=cert.id,
        status=models.RenewalTaskStatus.AUTO_RENEWED if result.get("success") else models.RenewalTaskStatus.FAILED,
        notes=str(result),
        triggered_by="manual",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
