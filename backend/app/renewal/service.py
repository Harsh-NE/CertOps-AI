"""
RenewalService: the ONLY place a renewal is planned, approved or executed (API, agent and any scheduler call it).

Safety model
  * plan()    = dry run. Persists a RenewalPlan (steps, warnings) and a DRY_RUN history row. Changes nothing else.
  * approve() = needs a *human* actor (a logged-in user with operator/admin role). Agents and system actors cannot
                approve, so an LLM (or prompt injection) cannot confirm its own action.
  * execute() = needs an APPROVED, unexpired, never-used plan whose hash still matches the certificate. The
                APPROVED -> EXECUTING transition is a compare-and-swap, so a plan can run at most once even
                under concurrent requests. Every outcome is written to renewal history.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.renewal.base import ExecResult, RenewalExecutor
from app.timeutil import ensure_utc, utcnow

HUMAN_ROLES = {"operator", "admin"}


class RenewalError(Exception):
    """Business-rule failure; `code` is machine-readable (used by the API and the agent)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Actor:
    kind: str  # user | agent | system
    name: str
    role: str = "viewer"
    on_behalf_of: Optional[str] = None  # for agents: the logged-in user who asked

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}"


def default_executors() -> Dict[str, RenewalExecutor]:
    from app.renewal.acme_executor import AcmeExecutor
    from app.renewal.aws_executor import AwsAcmExecutor
    return {"letsencrypt": AcmeExecutor(), "aws_acm": AwsAcmExecutor()}


def compute_hash(cert: models.Certificate, provider: str, params: dict) -> str:
    body = json.dumps({"cert": cert.id, "fingerprint": cert.fingerprint_sha256, "provider": provider,
                       "params": params}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class RenewalService:
    def __init__(self, db: Session, executors: Optional[Dict[str, RenewalExecutor]] = None):
        self.db = db
        self.executors = executors if executors is not None else default_executors()

    # ---- plan (dry run) ----------------------------------------------------------------------------
    def plan(self, cert: models.Certificate, actor: Actor) -> models.RenewalPlan:
        if not cert.renewal_provider:
            raise RenewalError("no_provider", "No renewal provider is configured for this certificate")
        executor = self.executors.get(cert.renewal_provider)
        if executor is None:
            raise RenewalError("unknown_provider", f"Unknown renewal provider '{cert.renewal_provider}'")
        spec = executor.plan(cert)
        digest = compute_hash(cert, cert.renewal_provider, spec.params)
        open_states = (models.RenewalPlanStatus.PENDING_APPROVAL, models.RenewalPlanStatus.APPROVED)
        existing = (self.db.query(models.RenewalPlan)
                    .filter(models.RenewalPlan.certificate_id == cert.id, models.RenewalPlan.plan_hash == digest,
                            models.RenewalPlan.status.in_(open_states), models.RenewalPlan.expires_at > utcnow())
                    .first())
        if existing is not None:  # idempotent: repeated requests (or a looping agent) reuse the open plan
            return existing
        pending_by_actor = (self.db.query(models.RenewalPlan)
                            .filter(models.RenewalPlan.created_by == actor.label,
                                    models.RenewalPlan.status == models.RenewalPlanStatus.PENDING_APPROVAL,
                                    models.RenewalPlan.expires_at > utcnow()).count())
        if pending_by_actor >= settings.RENEWAL_MAX_OPEN_PLANS_PER_ACTOR:
            raise RenewalError("too_many_open_plans", f"{actor.label} already has {pending_by_actor} plans awaiting "
                               "approval; an operator must approve or reject some first")
        plan = models.RenewalPlan(
            certificate_id=cert.id, provider=cert.renewal_provider, params=json.dumps(spec.params),
            plan_hash=digest, steps=json.dumps(spec.steps),
            warnings=json.dumps(spec.warnings), executable=spec.executable,
            not_executable_reason=spec.not_executable_reason, created_by=actor.label,
            expires_at=utcnow() + timedelta(minutes=settings.RENEWAL_PLAN_TTL_MINUTES))
        self.db.add(plan)
        self.db.flush()
        self.db.add(models.RenewalTask(
            certificate_id=cert.id, status=models.RenewalTaskStatus.DRY_RUN, provider=cert.renewal_provider,
            dry_run=True, triggered_by=actor.label, previous_serial=cert.serial_number,
            notes=f"DRY RUN plan {plan.id}: " + ("; ".join(spec.steps[:3]) or "no steps")))
        self.db.commit()
        self.db.refresh(plan)
        return plan

    # ---- approve / reject (humans only) -------------------------------------------------------------
    def approve(self, plan_id: str, actor: Actor) -> models.RenewalPlan:
        if actor.kind != "user" or actor.role not in HUMAN_ROLES:
            raise RenewalError("approval_requires_human", "Only a signed-in operator or admin can approve a renewal")
        plan = self._get(plan_id)
        self._require_open(plan)
        if not plan.executable:
            raise RenewalError("not_executable", plan.not_executable_reason or "This plan cannot be executed")
        plan.status, plan.approved_by, plan.approved_at = models.RenewalPlanStatus.APPROVED, actor.label, utcnow()
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def reject(self, plan_id: str, actor: Actor) -> models.RenewalPlan:
        if actor.kind != "user" or actor.role not in HUMAN_ROLES:
            raise RenewalError("approval_requires_human", "Only a signed-in operator or admin can reject a renewal")
        plan = self._get(plan_id)
        self._require_open(plan)
        plan.status = models.RenewalPlanStatus.REJECTED
        self.db.commit()
        self.db.refresh(plan)
        return plan

    # ---- execute -------------------------------------------------------------------------------------
    def execute(self, plan_id: str, actor: Actor) -> models.RenewalTask:
        plan = self._get(plan_id)
        if plan.status == models.RenewalPlanStatus.PENDING_APPROVAL:
            raise RenewalError("approval_required", "This plan has not been approved by a human yet")
        if plan.status != models.RenewalPlanStatus.APPROVED:
            raise RenewalError("plan_not_approved", f"Plan is {plan.status.value}; it cannot be executed")
        if ensure_utc(plan.expires_at) < utcnow():
            plan.status = models.RenewalPlanStatus.EXPIRED
            self.db.commit()
            raise RenewalError("plan_expired", "The approval expired; create and approve a new plan")
        cert = self.db.get(models.Certificate, plan.certificate_id)
        params = json.loads(plan.params)
        if cert is None or compute_hash(cert, plan.provider, params) != plan.plan_hash:
            raise RenewalError("plan_stale", "The certificate changed since the plan was made; create a new plan")
        executor = self.executors.get(plan.provider)
        if executor is None:
            raise RenewalError("unknown_provider", f"Unknown renewal provider '{plan.provider}'")

        # single-use: only one caller can move APPROVED -> EXECUTING
        claimed = self.db.execute(
            update(models.RenewalPlan)
            .where(models.RenewalPlan.id == plan.id, models.RenewalPlan.status == models.RenewalPlanStatus.APPROVED)
            .values(status=models.RenewalPlanStatus.EXECUTING)).rowcount
        self.db.commit()
        if claimed != 1:
            raise RenewalError("plan_in_use", "This plan is already being executed or was executed")

        try:
            result = executor.execute(cert, params)
        except Exception as e:  # executors should not raise, but a crash must still be recorded
            result = ExecResult(success=False, error=f"{type(e).__name__}: {e}")
        return self._record(plan, cert, actor, result)

    def _record(self, plan: models.RenewalPlan, cert: models.Certificate, actor: Actor,
                result: ExecResult) -> models.RenewalTask:
        S = models.RenewalTaskStatus
        status = S.AUTO_RENEWED if result.success else (S.SKIPPED if not result.action_taken else S.FAILED)
        detail = {"secret_ref": result.secret_ref, "error": result.error, **result.detail}
        task = models.RenewalTask(
            certificate_id=cert.id, status=status, provider=plan.provider, dry_run=False,
            triggered_by=f"{actor.label} (approved by {plan.approved_by})", previous_serial=cert.serial_number,
            new_serial=result.serial_number, completed_at=utcnow(),
            notes=json.dumps({k: v for k, v in detail.items() if v is not None}))
        self.db.add(task)
        self.db.flush()
        plan.status = models.RenewalPlanStatus.EXECUTED if result.success else models.RenewalPlanStatus.FAILED
        plan.executed_at, plan.renewal_task_id = utcnow(), task.id
        plan.result = json.dumps({"success": result.success, "new_serial": result.serial_number,
                                  "secret_ref": result.secret_ref, "error": result.error})
        self.db.commit()
        self.db.refresh(task)
        return task

    # ---- helpers -------------------------------------------------------------------------------------
    def _get(self, plan_id: str) -> models.RenewalPlan:
        plan = self.db.get(models.RenewalPlan, plan_id)
        if plan is None:
            raise RenewalError("plan_not_found", "No such renewal plan")
        return plan

    def _require_open(self, plan: models.RenewalPlan) -> None:
        if plan.status != models.RenewalPlanStatus.PENDING_APPROVAL:
            raise RenewalError("plan_not_pending", f"Plan is already {plan.status.value}")
        if ensure_utc(plan.expires_at) < utcnow():
            plan.status = models.RenewalPlanStatus.EXPIRED
            self.db.commit()
            raise RenewalError("plan_expired", "The plan expired; create a new one")
