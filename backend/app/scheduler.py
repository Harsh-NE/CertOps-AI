"""
Background scheduler: periodic re-scan of all known targets + cloud
discovery sweep, so certificate status/risk stays fresh without manual
intervention. Uses APScheduler's in-process background scheduler - simple
and dependency-light; swap for Celery+Redis (see README "Scaling up") if
you need distributed workers.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app import models
from app.services.pipeline import scan_and_process_network_target, rescore_certificate
from app.services.cert_scanner import CertScanError

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()


def rescan_all_known_targets():
    db = SessionLocal()
    try:
        certs = db.query(models.Certificate).filter(
            models.Certificate.source == models.SourceType.NETWORK_SCAN
        ).all()
        for cert in certs:
            try:
                scan_and_process_network_target(db, cert.source_ref)
                logger.info(f"Rescanned {cert.source_ref}")
            except CertScanError as e:
                logger.warning(f"Rescan failed for {cert.source_ref}: {e}")
    finally:
        db.close()


def refresh_risk_scores():
    """Cheap periodic re-score (no network I/O) so expiry countdowns stay accurate hourly."""
    db = SessionLocal()
    try:
        for cert in db.query(models.Certificate).all():
            rescore_certificate(db, cert)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(rescan_all_known_targets, "interval", hours=settings.SCAN_INTERVAL_HOURS, id="rescan_targets")
    scheduler.add_job(refresh_risk_scores, "interval", hours=1, id="refresh_risk")
    scheduler.start()
    logger.info("Scheduler started.")
