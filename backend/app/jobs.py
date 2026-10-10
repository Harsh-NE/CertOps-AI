"""
Job runner: one discovery run for one provider, with retries and persisted run state (`job_runs`).

Policy:
  * transient provider errors (timeouts, throttling, 5xx)  -> retried with exponential backoff
  * auth / permanent errors                                 -> fail immediately, not retried
  * provider not configured                                 -> recorded as "skipped"
  * a bad individual certificate never aborts the run; it is recorded in the run's error list
"""
import json
import logging
import time
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.database import SessionLocal
from app.providers.base import CertificateProvider, DiscoveryResult, ProviderError, TransientProviderError
from app.providers.network import NetworkProvider
from app.services.pipeline import process_normalized
from app.timeutil import utcnow

logger = logging.getLogger("jobs")


def run_provider_job(
    provider: CertificateProvider,
    *,
    triggered_by: str = "scheduler",
    session_factory: Callable[[], Session] = SessionLocal,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: Optional[int] = None,
    backoff_seconds: Optional[float] = None,
) -> Dict:
    max_attempts = max_attempts or settings.JOB_MAX_ATTEMPTS
    backoff = settings.JOB_BACKOFF_SECONDS if backoff_seconds is None else backoff_seconds
    db = session_factory()
    try:
        run = models.JobRun(provider=provider.name, triggered_by=triggered_by, status=models.JobRunStatus.RUNNING)
        db.add(run)
        db.commit()
        errors: List[str] = []
        discovered = 0

        if not provider.is_configured():
            return _finish(db, run, models.JobRunStatus.SKIPPED, ["provider is not configured"], 0)

        result: Optional[DiscoveryResult] = None
        for attempt in range(1, max_attempts + 1):
            run.attempts = attempt
            db.commit()
            try:
                result = provider.discover()
                break
            except TransientProviderError as e:
                errors.append(f"attempt {attempt}/{max_attempts}: {e}")
                logger.warning("%s transient failure (attempt %d): %s", provider.name, attempt, e)
                if attempt < max_attempts:
                    sleep(backoff * 2 ** (attempt - 1))
            except ProviderError as e:
                errors.append(f"{e.kind}: {e}")
                return _finish(db, run, models.JobRunStatus.FAILED, errors, 0)
            except Exception as e:  # a provider bug must not kill the scheduler
                logger.exception("%s crashed", provider.name)
                errors.append(f"unexpected {type(e).__name__}: {e}")
                return _finish(db, run, models.JobRunStatus.FAILED, errors, 0)
        if result is None:
            return _finish(db, run, models.JobRunStatus.FAILED, errors, 0)

        errors = list(result.errors)  # earlier transient attempts succeeded eventually; keep only item errors
        for n in result.certificates:
            try:
                process_normalized(db, n)
                discovered += 1
            except Exception as e:
                db.rollback()
                logger.exception("failed to process %s", n.source_ref)
                errors.append(f"{n.source_ref}: processing failed: {type(e).__name__}: {e}")
        if isinstance(provider, NetworkProvider):
            _update_targets(db, provider, result)
        status = (models.JobRunStatus.SUCCEEDED if not errors
                  else models.JobRunStatus.PARTIAL if discovered else models.JobRunStatus.FAILED)
        return _finish(db, run, status, errors, discovered)
    finally:
        db.close()


def _update_targets(db: Session, provider: NetworkProvider, result: DiscoveryResult) -> None:
    ok_refs = {n.source_ref for n in result.certificates}
    for t in provider.targets:
        if not t.target_id:
            continue
        row = db.get(models.ScanTarget, t.target_id)
        if row:
            row.last_scanned_at = utcnow()
            row.last_error = None if t.ref in ok_refs else provider.target_errors.get(t.ref)
    db.commit()


def _finish(db: Session, run: models.JobRun, status: models.JobRunStatus, errors: List[str], discovered: int) -> Dict:
    run.status, run.discovered, run.errors = status, discovered, json.dumps(errors)
    run.finished_at = utcnow()
    db.commit()
    return {"job_id": run.id, "provider": run.provider, "status": status.value, "attempts": run.attempts,
            "discovered": discovered, "errors": errors}
