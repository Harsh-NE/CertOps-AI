"""
Orchestration pipeline: given a discovered certificate (from network scan or
a cloud connector), persist/update it, run chain validation, resolve
ownership, score risk, update status, and fire notifications/renewal tasks
when thresholds are crossed. This is the function both the API endpoints
and the scheduler call so behavior stays identical whether a scan is
triggered manually or automatically.
"""
import json
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services.cert_scanner import scan_target, days_until_expiry, CertScanError
from app.services.chain_validator import validate_chain, evaluate_weak_crypto
from app.services.ownership_discovery import resolve_owner
from app.services.risk_ml import score_certificate
from app.services.notifications.slack import send_slack_alert
from app.services.notifications.teams import send_teams_alert
from app.services.notifications.jira import create_renewal_ticket


def _get_or_create_owner(db: Session, owner_info: Dict[str, Any]) -> models.Owner:
    existing = db.query(models.Owner).filter(models.Owner.name == owner_info["name"]).first()
    if existing:
        return existing
    owner = models.Owner(**owner_info)
    db.add(owner)
    db.flush()
    return owner


def _upsert_certificate(db: Session, data: Dict[str, Any]) -> models.Certificate:
    existing = db.query(models.Certificate).filter(
        models.Certificate.source_ref == data.get("source_ref")
    ).first()

    if existing:
        for k, v in data.items():
            if hasattr(existing, k) and k not in ("id",):
                setattr(existing, k, v)
        existing.last_scanned_at = datetime.utcnow()
        cert = existing
    else:
        allowed = {c.name for c in models.Certificate.__table__.columns}
        clean = {k: v for k, v in data.items() if k in allowed}
        clean["last_scanned_at"] = datetime.utcnow()
        cert = models.Certificate(**clean)
        db.add(cert)

    db.flush()
    return cert


def process_discovered_certificate(
    db: Session,
    raw: Dict[str, Any],
    k8s_annotations: Optional[Dict[str, str]] = None,
    repo_path: Optional[str] = None,
) -> models.Certificate:
    """Full pipeline for one discovered certificate dict (from any source)."""
    cert = _upsert_certificate(db, raw)

    # --- Ownership resolution ---
    owner_info = resolve_owner(
        k8s_annotations=k8s_annotations or raw.get("k8s_annotations"),
        repo_path=repo_path,
        file_glob_hint=cert.common_name,
    )
    if owner_info:
        owner = _get_or_create_owner(db, owner_info)
        cert.owner_id = owner.id

    # --- Status + risk scoring ---
    _rescore(db, cert)

    db.commit()
    db.refresh(cert)
    return cert


def scan_and_process_network_target(db: Session, target: str) -> models.Certificate:
    raw = scan_target(target)  # raises CertScanError on failure - caller handles
    host, _, port = target.partition(":")
    port = int(port) if port else 443

    chain_info = validate_chain(host, port)
    raw["chain_valid"] = chain_info.get("chain_valid")
    if chain_info.get("revocation"):
        raw["revoked"] = chain_info["revocation"].get("revoked")
        raw["revocation_check_method"] = "OCSP" if chain_info["revocation"].get("checked") else "none"

    raw["source"] = models.SourceType.NETWORK_SCAN

    log = models.ScanLog(target=target, success=True, message=json.dumps(chain_info, default=str))
    db.add(log)

    return process_discovered_certificate(db, raw)


def _rescore(db: Session, cert: models.Certificate):
    """Recompute status + ML risk score/tier for a certificate and persist it."""
    now = datetime.utcnow()
    d = days_until_expiry(cert.not_after) if cert.not_after else None

    if cert.revoked:
        cert.status = models.CertStatus.REVOKED
    elif cert.chain_valid is False:
        cert.status = models.CertStatus.INVALID_CHAIN
    elif d is not None and d < 0:
        cert.status = models.CertStatus.EXPIRED
    elif d is not None and d <= settings.EXPIRY_WARNING_DAYS:
        cert.status = models.CertStatus.EXPIRING_SOON
    elif d is not None:
        cert.status = models.CertStatus.VALID
    else:
        cert.status = models.CertStatus.UNKNOWN

    score = score_certificate(cert)
    cert.risk_score = score["risk_score"]
    cert.risk_tier = models.RiskTier(score["risk_tier"])
    cert.predicted_renewal_delay_days = score["predicted_renewal_delay_days"]
    cert.predicted_outage_impact = score["predicted_outage_impact"]

    # --- Alerting on critical thresholds ---
    if d is not None and d <= settings.EXPIRY_CRITICAL_DAYS:
        msg = (f":rotating_light: Certificate *{cert.common_name}* expires in {d} day(s) "
               f"(risk tier: {cert.risk_tier.value}). Source: {cert.source_ref}")
        send_slack_alert(msg, channel=cert.owner.slack_channel if cert.owner else None)
        send_teams_alert(msg)


def rescore_certificate(db: Session, cert: models.Certificate):
    _rescore(db, cert)
    db.commit()
    db.refresh(cert)
    return cert
