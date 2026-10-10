"""
Agent tools and the guardrail runner.

Tools are plain functions over a ToolContext so they can be unit-tested without an LLM; `agent.py` adapts them to
LangChain. Every call goes through ToolRunner, which enforces:
  * allow-list (unknown tool names are denied and logged)
  * a per-request call budget
  * a per-call timeout (each call uses its own DB session, so an abandoned call cannot corrupt the request's)
  * output sanitising + truncation (certificate fields are untrusted data and can contain injected text)
  * an audit row (`agent_tool_calls`) and a JSON log line for every attempt, including denied ones
Write actions are never confirmed by the model: `execute_renewal` succeeds only for a plan a *human* approved
(see RenewalService), and the agent has no approve tool.
"""
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models
from app.agent import rca
from app.config import settings
from app.database import SessionLocal
from app.ml.scoring import score_certificate
from app.renewal.service import Actor, RenewalError, RenewalService
from app.services.pipeline import revalidate_certificate
from app.timeutil import days_until, utcnow

logger = logging.getLogger("agent.tools")

MAX_RESULT_CHARS = 8000
MAX_LIST = 25
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize(value: Any, limit: int = 200) -> Any:
    """Certificate-derived strings are untrusted: strip control characters and cap length."""
    if isinstance(value, str):
        v = _CONTROL.sub("", value)
        return v if len(v) <= limit else v[:limit] + "...[truncated]"
    if isinstance(value, dict):
        return {k: sanitize(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, limit) for v in value]
    return value


@dataclass
class ToolContext:
    actor: Actor
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_factory: Callable[[], Session] = SessionLocal
    executors: Optional[dict] = None  # injected renewal executors (tests); None -> defaults


# ---- helpers ---------------------------------------------------------------------------------------------

def _summary(c: models.Certificate) -> Dict[str, Any]:
    return {
        "id": c.id, "common_name": c.common_name, "hostname": c.expected_hostname, "environment": c.environment,
        "status": c.status.value if c.status else None, "days_to_expiry": days_until(c.not_after),
        "risk_tier": c.risk_tier.value if c.risk_tier else None, "risk_score": c.risk_score,
        "source": c.source.value if c.source else None, "owner": c.owner.name if c.owner else None,
        "auto_renew": bool(c.auto_renew_enabled),
    }


def resolve(db: Session, ref: str) -> Dict[str, Any]:
    """Find one certificate by id, exact name, or unique substring. Returns {'cert': c} or {'error': ...}."""
    ref = (ref or "").strip()
    if not ref:
        return {"error": "missing_reference", "message": "Provide a certificate id or name."}
    c = db.get(models.Certificate, ref)
    if c:
        return {"cert": c}
    q = db.query(models.Certificate)
    exact = q.filter(or_(models.Certificate.common_name.ilike(ref), models.Certificate.expected_hostname.ilike(ref))).all()
    matches = exact or q.filter(or_(models.Certificate.common_name.ilike(f"%{ref}%"),
                                    models.Certificate.expected_hostname.ilike(f"%{ref}%"),
                                    models.Certificate.source_ref.ilike(f"%{ref}%"))).all()
    if len(matches) == 1:
        return {"cert": matches[0]}
    if not matches:
        return {"error": "not_found", "message": f"No certificate matches '{ref}'."}
    return {"error": "ambiguous", "message": f"'{ref}' matches {len(matches)} certificates; ask the user which one.",
            "candidates": [_summary(m) for m in matches[:MAX_LIST]]}


# ---- argument schemas (what the LLM sees) ----------------------------------------------------------------

class SearchArgs(BaseModel):
    query: Optional[str] = Field(None, description="Text to match in common name, hostname, issuer or source reference")
    status: Optional[str] = Field(None, description="valid | expiring_soon | expired | revoked | invalid_chain | hostname_mismatch | weak_crypto | unknown")
    environment: Optional[str] = Field(None, description="e.g. prod, staging, dev")
    risk_tier: Optional[str] = Field(None, description="low | medium | high | critical")
    source: Optional[str] = Field(None, description="network_scan | aws_acm | kubernetes_secret | azure_key_vault | gcp_cert_manager")
    expires_within_days: Optional[int] = Field(None, description="Only certificates expiring within this many days (includes already expired)")
    limit: int = Field(MAX_LIST, description="Max results (capped at 25)")


class CertRef(BaseModel):
    certificate: str = Field(..., description="Certificate id, common name, hostname, or a unique part of one")


class PlanIdArg(BaseModel):
    plan_id: str = Field(..., description="Renewal plan id returned by plan_renewal")


class PlanListArgs(BaseModel):
    certificate: Optional[str] = Field(None, description="Limit to one certificate")
    status: Optional[str] = Field(None, description="pending_approval | approved | executed | failed | rejected | expired")


# ---- tool implementations --------------------------------------------------------------------------------

def search_certificates(ctx: ToolContext, db: Session, a: SearchArgs) -> Dict[str, Any]:
    q = db.query(models.Certificate)
    try:
        if a.status:
            q = q.filter(models.Certificate.status == models.CertStatus(a.status))
        if a.risk_tier:
            q = q.filter(models.Certificate.risk_tier == models.RiskTier(a.risk_tier))
        if a.source:
            q = q.filter(models.Certificate.source == models.SourceType(a.source))
    except ValueError as e:
        return {"error": "invalid_filter", "message": str(e)}
    if a.environment:
        q = q.filter(models.Certificate.environment == a.environment.lower())
    if a.expires_within_days is not None:
        q = q.filter(models.Certificate.not_after <= utcnow() + timedelta(days=a.expires_within_days))
    if a.query:
        like = f"%{a.query}%"
        q = q.filter(or_(models.Certificate.common_name.ilike(like), models.Certificate.expected_hostname.ilike(like),
                         models.Certificate.issuer.ilike(like), models.Certificate.source_ref.ilike(like)))
    total = q.count()
    rows = q.order_by(models.Certificate.not_after.asc().nulls_last()).limit(min(a.limit, MAX_LIST)).all()
    return {"total_matches": total, "returned": len(rows), "certificates": [_summary(c) for c in rows]}


def get_certificate(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    if "cert" not in r:
        return r
    c = r["cert"]
    latest = (db.query(models.ValidationResult).filter_by(certificate_id=c.id)
              .order_by(models.ValidationResult.checked_at.desc()).first())
    return {**_summary(c), "issuer": c.issuer, "subject": c.subject, "serial_number": c.serial_number,
            "not_before": c.not_before.isoformat() if c.not_before else None,
            "not_after": c.not_after.isoformat() if c.not_after else None,
            "key": f"{c.key_algorithm} {c.key_size}", "signature_algorithm": c.signature_algorithm,
            "san": json.loads(c.san_list) if c.san_list else [], "wildcard": bool(c.is_wildcard),
            "chain_depth": c.chain_depth, "chain_valid": c.chain_valid, "revoked": c.revoked,
            "source_ref": c.source_ref, "renewal_provider": c.renewal_provider,
            "last_validation": latest.overall if latest else None,
            "last_validated_at": c.last_validated_at.isoformat() if c.last_validated_at else None}


def run_validation(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    if "cert" not in r:
        return r
    c = revalidate_certificate(db, r["cert"], online=False)  # offline: stored chain, no network access
    latest = (db.query(models.ValidationResult).filter_by(certificate_id=c.id)
              .order_by(models.ValidationResult.checked_at.desc()).first())
    return {"certificate": c.common_name, "status": c.status.value, "overall": latest.overall if latest else None,
            "chain_valid": c.chain_valid, "revoked": c.revoked,
            "findings": json.loads(latest.findings) if latest else [],
            "note": "Re-run from stored data without network access; revocation is not re-checked."}


def explain_risk(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    if "cert" not in r:
        return r
    c = r["cert"]
    s = score_certificate(db, c)
    return {"certificate": c.common_name, "probability_of_problem_30d": s["probability"], "tier": s["tier"],
            "model_used": s["model_used"], "data_source": s["data_source"], "floor_reason": s["floor_reason"],
            "top_factors": s["factors"], "outage_impact_rule": s["predicted_outage_impact"],
            "caveat": "The model is trained on SYNTHETIC data; treat the probability as a relative ranking, not a forecast."}


def diagnose_certificate(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    return rca.diagnose(db, r["cert"]) if "cert" in r else r


def get_renewal_history(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    if "cert" not in r:
        return r
    c = r["cert"]
    tasks = (db.query(models.RenewalTask).filter_by(certificate_id=c.id)
             .order_by(models.RenewalTask.created_at.desc()).limit(10).all())
    return {"certificate": c.common_name, "history": [
        {"when": t.created_at.isoformat(), "status": t.status.value, "provider": t.provider, "dry_run": t.dry_run,
         "triggered_by": t.triggered_by, "new_serial": t.new_serial, "notes": (t.notes or "")[:300]} for t in tasks]}


def _plan_view(p: models.RenewalPlan) -> Dict[str, Any]:
    return {"plan_id": p.id, "certificate_id": p.certificate_id, "provider": p.provider, "status": p.status.value,
            "executable": p.executable, "not_executable_reason": p.not_executable_reason,
            "steps": json.loads(p.steps), "warnings": json.loads(p.warnings or "[]"),
            "expires_at": p.expires_at.isoformat(), "approved_by": p.approved_by}


def plan_renewal(ctx: ToolContext, db: Session, a: CertRef) -> Dict[str, Any]:
    r = resolve(db, a.certificate)
    if "cert" not in r:
        return r
    try:
        plan = RenewalService(db, ctx.executors).plan(r["cert"], ctx.actor)
    except RenewalError as e:
        return {"error": e.code, "message": str(e)}
    return {**_plan_view(plan), "next_step": ("A human operator must approve this plan in the platform (UI or API) "
                                              "before it can be executed. Tell the user the plan_id.")
            if plan.executable else "This plan cannot be executed; explain the reason to the user."}


def list_renewal_plans(ctx: ToolContext, db: Session, a: PlanListArgs) -> Dict[str, Any]:
    q = db.query(models.RenewalPlan)
    if a.certificate:
        r = resolve(db, a.certificate)
        if "cert" not in r:
            return r
        q = q.filter_by(certificate_id=r["cert"].id)
    if a.status:
        try:
            q = q.filter_by(status=models.RenewalPlanStatus(a.status))
        except ValueError as e:
            return {"error": "invalid_filter", "message": str(e)}
    return {"plans": [_plan_view(p) for p in q.order_by(models.RenewalPlan.created_at.desc()).limit(10).all()]}


def execute_renewal(ctx: ToolContext, db: Session, a: PlanIdArg) -> Dict[str, Any]:
    try:
        task = RenewalService(db, ctx.executors).execute(a.plan_id, ctx.actor)
    except RenewalError as e:
        return {"error": e.code, "message": str(e)}
    return {"executed": True, "status": task.status.value, "provider": task.provider, "new_serial": task.new_serial,
            "details": (task.notes or "")[:300]}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: Type[BaseModel]
    func: Callable[[ToolContext, Session, Any], Dict[str, Any]]
    kind: str  # read | action | write
    timeout: Optional[float] = None


TOOLS: Dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("search_certificates", "Search/filter the certificate inventory. Use for questions like 'which prod certificates expire in 14 days'. Returns summaries.", SearchArgs, search_certificates, "read"),
    ToolSpec("get_certificate", "Get full details of one certificate by id, name or hostname.", CertRef, get_certificate, "read"),
    ToolSpec("run_validation", "Re-run validation checks on one certificate from stored data (offline) and return the findings.", CertRef, run_validation, "action"),
    ToolSpec("explain_risk", "Explain the risk score of one certificate: probability, tier, and the factors raising it.", CertRef, explain_risk, "read"),
    ToolSpec("diagnose_certificate", "Root-cause analysis: gather evidence (validation findings, renewal history, job errors) and candidate causes/fixes for why a certificate is failing.", CertRef, diagnose_certificate, "read"),
    ToolSpec("get_renewal_history", "Show recent renewal attempts for one certificate, including failures.", CertRef, get_renewal_history, "read"),
    ToolSpec("list_renewal_plans", "List renewal plans and their approval status.", PlanListArgs, list_renewal_plans, "read"),
    ToolSpec("plan_renewal", "DRY RUN a renewal: shows what would happen and creates a plan that a human must approve. Changes nothing else.", CertRef, plan_renewal, "action"),
    ToolSpec("execute_renewal", "Execute an APPROVED renewal plan. Fails unless a human operator approved the plan; you cannot approve it.", PlanIdArg, execute_renewal, "write", timeout=180.0),
]}
ALLOWED_TOOLS = frozenset(TOOLS)
READ_ONLY_TOOLS = frozenset(n for n, t in TOOLS.items() if t.kind == "read")


# ---- guardrail runner -------------------------------------------------------------------------------------

class ToolRunner:
    def __init__(self, ctx: ToolContext, tools: Optional[Dict[str, ToolSpec]] = None,
                 allowed: Optional[frozenset] = None, max_calls: Optional[int] = None):
        self.ctx, self.tools = ctx, tools or TOOLS
        self.allowed = ALLOWED_TOOLS if allowed is None else allowed
        self.max_calls = max_calls or settings.AGENT_MAX_TOOL_CALLS
        self.trace: List[Dict[str, Any]] = []
        self.results: List[str] = []  # full (sanitised) result JSON per call, in order
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-tool")

    def run(self, name: str, args: Dict[str, Any]) -> str:
        started = time.monotonic()
        status, payload = "ok", {}
        if name not in self.allowed or name not in self.tools:
            status, payload = "denied", {"error": "tool_not_allowed", "message": f"'{name}' is not an available tool."}
        elif len(self.trace) >= self.max_calls:
            status, payload = "limit", {"error": "call_limit", "message": "Tool call budget for this request is used up."}
        else:
            spec = self.tools[name]
            try:
                parsed = spec.args(**(args or {}))
            except Exception as e:
                status, payload = "error", {"error": "invalid_arguments", "message": str(e)[:300]}
            else:
                status, payload = self._execute(spec, parsed)
        ms = int((time.monotonic() - started) * 1000)
        result = self._finalize(payload)
        self._audit(name, args, status, ms, result)
        self.trace.append({"tool": name, "args": args, "status": status, "ms": ms})
        self.results.append(result)
        return result

    def _execute(self, spec: ToolSpec, parsed: BaseModel) -> tuple[str, Dict[str, Any]]:
        def call():
            db = self.ctx.session_factory()
            try:
                return spec.func(self.ctx, db, parsed)
            finally:
                db.close()

        timeout = spec.timeout or settings.AGENT_TOOL_TIMEOUT_SECONDS
        future = self._pool.submit(call)
        try:
            out = future.result(timeout=timeout)
        except FutureTimeout:
            return "timeout", {"error": "timeout", "message": f"Tool '{spec.name}' exceeded {timeout:.0f}s."}
        except Exception as e:
            logger.exception("tool %s crashed", spec.name)
            return "error", {"error": "tool_error", "message": f"{type(e).__name__} while running the tool."}
        status = "denied" if out.get("error") in ("approval_required", "plan_not_approved", "plan_expired",
                                                  "plan_stale", "plan_in_use", "not_executable") else (
            "error" if "error" in out else "ok")
        return status, out

    @staticmethod
    def _finalize(payload: Dict[str, Any]) -> str:
        text = json.dumps(sanitize(payload), default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = json.dumps({"error": "result_truncated", "message": "Result too large; narrow the query.",
                               "preview": text[:1500]})
        return text

    def _audit(self, name: str, args: Any, status: str, ms: int, result: str) -> None:
        line = {"event": "agent_tool_call", "request_id": self.ctx.request_id, "actor": self.ctx.actor.label,
                "tool": name, "status": status, "duration_ms": ms}
        logger.info(json.dumps(line))
        db = self.ctx.session_factory()
        try:
            db.add(models.AgentToolCall(request_id=self.ctx.request_id, actor=self.ctx.actor.label, tool=name[:80],
                                        args=json.dumps(sanitize(args), default=str)[:2000], status=status,
                                        duration_ms=ms, summary=result[:500]))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("failed to write tool-call audit row")
        finally:
            db.close()

    def close(self) -> None:
        self._pool.shutdown(wait=False)
