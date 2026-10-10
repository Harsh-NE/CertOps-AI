"""
Persistence pipeline: take a NormalizedCertificate from any provider, validate it, upsert it, resolve its owner,
derive status + risk, and alert on critical expiry. The job runner, API and scheduler all go through here so
behaviour is identical however a scan is triggered.
"""
import json
from typing import Dict, Optional

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.ml.scoring import flags_from_findings, score_certificate
from app.providers.base import NormalizedCertificate
from app.providers.normalize import normalize_x509
from app.services.notifications.slack import send_slack_alert
from app.services.notifications.teams import send_teams_alert
from app.services.ownership_discovery import resolve_owner
from app.timeutil import days_until, utcnow
from app.validation.engine import ValidationReport, validate
from app.validation.trust import load_roots


def validate_normalized(n: NormalizedCertificate, *, online: bool = True) -> ValidationReport:
    return validate(
        n, load_roots(settings.TRUST_STORE_EXTRA_PEM),
        warn_days=settings.EXPIRY_WARNING_DAYS, critical_days=settings.EXPIRY_CRITICAL_DAYS,
        check_revocation_online=online and settings.OCSP_ENABLED, ocsp_timeout=settings.NETWORK_TIMEOUT_SECONDS,
    )


def _get_or_create_owner(db: Session, owner_info: Dict) -> models.Owner:
    existing = db.query(models.Owner).filter(models.Owner.name == owner_info["name"]).first()
    if existing:
        return existing
    owner = models.Owner(**owner_info)
    db.add(owner)
    db.flush()
    return owner


def _chain_pem(n: NormalizedCertificate) -> Optional[str]:
    if not n.chain_der:
        return None
    return b"".join(x509.load_der_x509_certificate(d).public_bytes(serialization.Encoding.PEM) for d in n.chain_der).decode()


def _upsert(db: Session, n: NormalizedCertificate) -> models.Certificate:
    source = models.SourceType(n.provider)
    cert = (db.query(models.Certificate)
            .filter(models.Certificate.source == source, models.Certificate.source_ref == n.source_ref).first())
    if cert is None:
        cert = models.Certificate(source=source, source_ref=n.source_ref, common_name=n.common_name)
        db.add(cert)
    cert.common_name = n.common_name
    cert.fingerprint_sha256 = n.fingerprint_sha256
    cert.subject, cert.issuer, cert.serial_number = n.subject, n.issuer, n.serial_number
    cert.signature_algorithm = n.signature_algorithm
    cert.key_algorithm, cert.key_size = n.key_algorithm, n.key_size
    cert.not_before, cert.not_after = n.not_before, n.not_after
    cert.san_list = json.dumps(n.san_dns + n.san_ips)
    cert.is_wildcard = n.is_wildcard
    cert.expected_hostname = n.expected_hostname
    cert.chain_pem = _chain_pem(n) or cert.chain_pem
    if n.environment:
        cert.environment = n.environment
    cert.last_scanned_at = utcnow()
    db.flush()
    return cert


def apply_report(db: Session, cert: models.Certificate, report: ValidationReport) -> None:
    """Store the validation outcome on the certificate and as a history row; derive status + risk."""
    cert.chain_valid = report.chain_valid
    cert.chain_depth = report.chain_depth or cert.chain_depth
    if report.revoked is not None:  # keep the last known answer when revocation could not be re-checked
        cert.revoked = report.revoked
        cert.revocation_check_method = report.revocation_method
    cert.last_validated_at = utcnow()
    findings = json.dumps(report.findings_as_dicts())
    latest = (db.query(models.ValidationResult).filter_by(certificate_id=cert.id)
              .order_by(models.ValidationResult.checked_at.desc()).first())
    if latest is None or latest.findings != findings or latest.overall != report.overall:  # history = changes only
        db.add(models.ValidationResult(
            certificate_id=cert.id, overall=report.overall, chain_valid=report.chain_valid, revoked=report.revoked,
            findings=findings))
    _set_status_and_risk(db, cert, report)


def _set_status_and_risk(db: Session, cert: models.Certificate, report: Optional[ValidationReport] = None) -> None:
    d = days_until(cert.not_after)
    has = (lambda c: report.has(c)) if report else (lambda c: False)
    if cert.revoked:
        cert.status = models.CertStatus.REVOKED
    elif d is not None and d < 0:
        cert.status = models.CertStatus.EXPIRED
    elif cert.chain_valid is False:
        cert.status = models.CertStatus.INVALID_CHAIN
    elif has("hostname_mismatch"):
        cert.status = models.CertStatus.HOSTNAME_MISMATCH
    elif has("weak_key") or has("weak_signature"):
        cert.status = models.CertStatus.WEAK_CRYPTO
    elif d is not None and d <= settings.EXPIRY_WARNING_DAYS:
        cert.status = models.CertStatus.EXPIRING_SOON
    elif d is not None:
        cert.status = models.CertStatus.VALID
    else:
        cert.status = models.CertStatus.UNKNOWN

    flags = None
    if report is not None:
        flags = flags_from_findings(report.findings_as_dicts())
    score = score_certificate(db, cert, flags)
    cert.risk_score = score["risk_score"]
    cert.risk_tier = models.RiskTier(score["tier"])
    cert.predicted_renewal_delay_days = None  # the old "renewal delay" regressor was trained on a made-up target; removed
    cert.predicted_outage_impact = score["predicted_outage_impact"]


def _alert_if_critical(cert: models.Certificate) -> None:
    """Alerts only from scan/discovery runs, not from the hourly re-validation (avoids hourly spam)."""
    d = days_until(cert.not_after)
    if d is not None and d <= settings.EXPIRY_CRITICAL_DAYS:
        msg = (f":rotating_light: Certificate *{cert.common_name}* expires in {d} day(s) "
               f"(risk tier: {cert.risk_tier.value}). Source: {cert.source_ref}")
        send_slack_alert(msg, channel=cert.owner.slack_channel if cert.owner else None)
        send_teams_alert(msg)


def process_normalized(db: Session, n: NormalizedCertificate) -> models.Certificate:
    """Validate + persist one discovered certificate. Commits."""
    report = validate_normalized(n)
    cert = _upsert(db, n)
    owner_info = resolve_owner(k8s_annotations=n.extras.get("k8s_annotations"), file_glob_hint=cert.common_name)
    if owner_info:
        cert.owner_id = _get_or_create_owner(db, owner_info).id
    apply_report(db, cert, report)
    _alert_if_critical(cert)
    db.commit()
    db.refresh(cert)
    return cert


def normalized_from_row(cert: models.Certificate) -> NormalizedCertificate:
    """Rebuild a NormalizedCertificate from stored data so validation can be re-run without network access."""
    if cert.chain_pem:
        chain = x509.load_pem_x509_certificates(cert.chain_pem.encode())
        n = normalize_x509(chain[0], provider=cert.source.value, source_ref=cert.source_ref or "", chain=chain,
                           environment=cert.environment, expected_hostname=cert.expected_hostname)
        return n
    sans = json.loads(cert.san_list) if cert.san_list else []
    return NormalizedCertificate(
        provider=cert.source.value, source_ref=cert.source_ref or "", common_name=cert.common_name,
        not_before=cert.not_before, not_after=cert.not_after, key_algorithm=cert.key_algorithm,
        key_size=cert.key_size, san_dns=sans, environment=cert.environment, expected_hostname=cert.expected_hostname)


def revalidate_certificate(db: Session, cert: models.Certificate, *, online: bool = False) -> models.Certificate:
    """Re-run validation from stored data (offline by default, so no OCSP traffic and no alerts)."""
    report = validate_normalized(normalized_from_row(cert), online=online)
    apply_report(db, cert, report)
    db.commit()
    db.refresh(cert)
    return cert


def rescore_certificate(db: Session, cert: models.Certificate) -> models.Certificate:
    return revalidate_certificate(db, cert)
