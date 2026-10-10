"""
In-process APScheduler jobs:
  * discovery sweep: every SCAN_INTERVAL_HOURS, run each *configured* provider through the job runner
    (retries + persisted run state in `job_runs`)
  * re-validation: hourly, re-run the offline checks on stored certificates so expiry countdowns and
    statuses stay accurate between sweeps (no network traffic, no alerts)
Single-process by design; run one API worker (see backend/Dockerfile) or swap in a distributed queue.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app import models
from app.config import settings
from app.database import SessionLocal
from app.jobs import run_provider_job
from app.providers.registry import build_providers
from app.services.pipeline import revalidate_certificate

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()


def discovery_sweep():
    db = SessionLocal()
    try:
        providers = build_providers(db)
    finally:
        db.close()
    for name, provider in providers.items():
        if not provider.is_configured():
            continue
        try:
            summary = run_provider_job(provider, triggered_by="scheduler")
            logger.info("sweep %s: %s", name, {k: summary[k] for k in ("status", "discovered", "attempts")})
        except Exception:
            logger.exception("sweep %s crashed", name)


def revalidate_all():
    db = SessionLocal()
    try:
        for cert in db.query(models.Certificate).all():
            try:
                revalidate_certificate(db, cert, online=False)
            except Exception:
                db.rollback()
                logger.exception("revalidation failed for %s", cert.id)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(discovery_sweep, "interval", hours=settings.SCAN_INTERVAL_HOURS, id="discovery_sweep",
                      replace_existing=True)
    scheduler.add_job(revalidate_all, "interval", hours=1, id="revalidate_all", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started.")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
