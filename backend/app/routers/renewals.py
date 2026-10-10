from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db
from app.services.auto_renewal import trigger_renewal
from app.services.notifications.jira import create_renewal_ticket
from app.timeutil import days_until as days_until_expiry

router = APIRouter(prefix="/api/renewals", tags=["renewals"])


@router.get("", response_model=List[schemas.RenewalTaskOut])
def list_renewal_tasks(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.query(models.RenewalTask).order_by(models.RenewalTask.created_at.desc()).all()


@router.post("/{cert_id}/create-ticket", response_model=schemas.RenewalTaskOut)
def create_ticket(cert_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
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
        triggered_by=f"user:{user.username}", provider="jira",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _provider_kwargs(cert: models.Certificate) -> dict:
    if cert.renewal_provider == "letsencrypt":
        return {"domain": cert.common_name}
    if cert.renewal_provider == "vault":
        return {"pki_role": "default", "common_name": cert.common_name}
    if cert.renewal_provider == "aws_acm":
        return {"certificate_arn": cert.source_ref}
    return {}


@router.post("/{cert_id}/renew-now", response_model=schemas.RenewalTaskOut)
def renew_now(
    cert_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    db: Session = Depends(get_db),
    user=Depends(require_role("operator")),
):
    """
    Dry-run by default: records what *would* happen and changes nothing.
    A real renewal needs both ``dry_run=false`` and ``confirm=true``.
    """
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    if not cert:
        raise HTTPException(404, "Certificate not found")
    if not cert.renewal_provider:
        raise HTTPException(400, "No renewal provider configured for this certificate")
    if not dry_run and not confirm:
        raise HTTPException(400, "Real renewal requires confirm=true (or use dry_run=true)")

    task = models.RenewalTask(
        certificate_id=cert.id, provider=cert.renewal_provider, dry_run=dry_run,
        previous_serial=cert.serial_number, triggered_by=f"user:{user.username}",
    )
    if dry_run:
        task.status = models.RenewalTaskStatus.DRY_RUN
        task.notes = f"DRY RUN: would renew '{cert.common_name}' via {cert.renewal_provider}; no action taken."
    else:
        result = trigger_renewal(cert.renewal_provider, **_provider_kwargs(cert))
        if result.get("success"):
            task.status = models.RenewalTaskStatus.AUTO_RENEWED
        elif result.get("action_taken") is False:
            task.status = models.RenewalTaskStatus.SKIPPED
        else:
            task.status = models.RenewalTaskStatus.FAILED
        task.new_serial = result.get("serial_number")
        task.notes = str({k: v for k, v in result.items() if k not in ("certificate", "stdout", "stderr")})
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
