import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db
from app.renewal.service import Actor, RenewalError, RenewalService
from app.services.notifications.jira import create_renewal_ticket
from app.timeutil import days_until

router = APIRouter(prefix="/api/renewals", tags=["renewals"])

_STATUS = {"plan_not_found": 404, "approval_requires_human": 403, "no_provider": 400, "unknown_provider": 400}


def get_service(db: Session = Depends(get_db)) -> RenewalService:
    return RenewalService(db)  # tests override this dependency to inject fake executors


def _actor(user: models.User) -> Actor:
    return Actor(kind="user", name=user.username, role=user.role)


def _plan_out(p: models.RenewalPlan) -> schemas.RenewalPlanOut:
    return schemas.RenewalPlanOut(
        id=p.id, certificate_id=p.certificate_id, provider=p.provider, status=p.status.value, executable=bool(p.executable),
        not_executable_reason=p.not_executable_reason, steps=json.loads(p.steps), warnings=json.loads(p.warnings or "[]"),
        created_by=p.created_by, created_at=p.created_at, expires_at=p.expires_at, approved_by=p.approved_by,
        approved_at=p.approved_at, executed_at=p.executed_at, result=json.loads(p.result) if p.result else None)


def _fail(e: RenewalError):
    raise HTTPException(_STATUS.get(e.code, 409), detail={"code": e.code, "message": str(e)}) from e


@router.get("", response_model=List[schemas.RenewalTaskOut])
def list_renewal_tasks(
    certificate_id: Optional[str] = None,
    status: Optional[str] = None,
    include_dry_runs: bool = True,
    limit: int = 200,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Renewal history: dry runs, executions, tickets."""
    q = db.query(models.RenewalTask)
    if certificate_id:
        q = q.filter(models.RenewalTask.certificate_id == certificate_id)
    if status:
        try:
            q = q.filter(models.RenewalTask.status == models.RenewalTaskStatus(status))
        except ValueError as e:
            raise HTTPException(422, f"invalid status: {e}") from e
    if not include_dry_runs:
        q = q.filter(models.RenewalTask.dry_run.isnot(True))
    return q.order_by(models.RenewalTask.created_at.desc()).limit(min(limit, 500)).all()


@router.post("/{cert_id}/create-ticket", response_model=schemas.RenewalTaskOut)
def create_ticket(cert_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    d = days_until(cert.not_after)
    jira_result = create_renewal_ticket(cert.common_name, d if d is not None else -1, cert.owner.team if cert.owner else None)
    task = models.RenewalTask(
        certificate_id=cert.id,
        status=models.RenewalTaskStatus.JIRA_CREATED if jira_result.get("created") else models.RenewalTaskStatus.PENDING,
        jira_ticket_key=jira_result.get("key"), notes=None if jira_result.get("created") else jira_result.get("reason"),
        triggered_by=f"user:{user.username}", provider="jira")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{cert_id}/plan", response_model=schemas.RenewalPlanOut)
def create_plan(cert_id: str, db: Session = Depends(get_db), svc: RenewalService = Depends(get_service),
                user=Depends(require_role("operator"))):
    """Dry run. Creates a plan that must be approved before it can be executed."""
    cert = db.get(models.Certificate, cert_id)
    if not cert:
        raise HTTPException(404, "Certificate not found")
    try:
        return _plan_out(svc.plan(cert, _actor(user)))
    except RenewalError as e:
        _fail(e)


@router.get("/plans", response_model=List[schemas.RenewalPlanOut])
def list_plans(certificate_id: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    q = db.query(models.RenewalPlan)
    if certificate_id:
        q = q.filter_by(certificate_id=certificate_id)
    if status:
        try:
            q = q.filter_by(status=models.RenewalPlanStatus(status))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    return [_plan_out(p) for p in q.order_by(models.RenewalPlan.created_at.desc()).limit(100).all()]


@router.get("/plans/{plan_id}", response_model=schemas.RenewalPlanOut)
def get_plan(plan_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    plan = db.get(models.RenewalPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return _plan_out(plan)


@router.post("/plans/{plan_id}/approve", response_model=schemas.RenewalPlanOut)
def approve_plan(plan_id: str, svc: RenewalService = Depends(get_service), user=Depends(require_role("operator"))):
    """Human approval (requires a signed-in operator/admin). The agent has no equivalent tool."""
    try:
        return _plan_out(svc.approve(plan_id, _actor(user)))
    except RenewalError as e:
        _fail(e)


@router.post("/plans/{plan_id}/reject", response_model=schemas.RenewalPlanOut)
def reject_plan(plan_id: str, svc: RenewalService = Depends(get_service), user=Depends(require_role("operator"))):
    try:
        return _plan_out(svc.reject(plan_id, _actor(user)))
    except RenewalError as e:
        _fail(e)


@router.post("/plans/{plan_id}/execute", response_model=schemas.RenewalTaskOut)
def execute_plan(plan_id: str, svc: RenewalService = Depends(get_service), user=Depends(require_role("operator"))):
    """Executes an approved, unexpired, unused plan."""
    try:
        return svc.execute(plan_id, _actor(user))
    except RenewalError as e:
        _fail(e)
