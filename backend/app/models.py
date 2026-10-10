import enum
import uuid

from sqlalchemy import Boolean, Column, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base
from app.dbtypes import UtcDateTime
from app.timeutil import utcnow


def gen_uuid():
    return str(uuid.uuid4())


class SourceType(str, enum.Enum):
    NETWORK_SCAN = "network_scan"
    AWS_ACM = "aws_acm"
    AZURE_KEY_VAULT = "azure_key_vault"
    GCP_CERT_MANAGER = "gcp_cert_manager"
    KUBERNETES_SECRET = "kubernetes_secret"
    MANUAL = "manual"


class CertStatus(str, enum.Enum):
    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INVALID_CHAIN = "invalid_chain"
    HOSTNAME_MISMATCH = "hostname_mismatch"
    WEAK_CRYPTO = "weak_crypto"
    UNKNOWN = "unknown"


class RiskTier(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Owner(Base):
    __tablename__ = "owners"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    team = Column(String, nullable=True)
    email = Column(String, nullable=True)
    slack_channel = Column(String, nullable=True)
    source = Column(String, nullable=True)  # git_codeowners | k8s_annotation | service_metadata | manual

    certificates = relationship("Certificate", back_populates="owner")


class Certificate(Base):
    __tablename__ = "certificates"
    id = Column(String, primary_key=True, default=gen_uuid)

    # Identity: SHA-256 of the DER bytes when known; source+source_ref locate it in its provider.
    fingerprint_sha256 = Column(String, index=True, nullable=True)
    common_name = Column(String, index=True, nullable=False)
    san_list = Column(Text, nullable=True)  # JSON-encoded list
    issuer = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    serial_number = Column(String, nullable=True)
    signature_algorithm = Column(String, nullable=True)
    key_algorithm = Column(String, nullable=True)  # RSA | EC | Ed25519 ...
    key_size = Column(Integer, nullable=True)
    is_wildcard = Column(Boolean, default=False)
    chain_depth = Column(Integer, nullable=True)  # certs presented/validated, leaf included
    expected_hostname = Column(String, nullable=True)  # name the endpoint should serve (network targets)
    chain_pem = Column(Text, nullable=True)  # presented chain, leaf first (public certs only)
    last_validated_at = Column(UtcDateTime, nullable=True)
    environment = Column(String, index=True, nullable=True)  # prod | staging | dev ... (from tags/labels)

    not_before = Column(UtcDateTime, nullable=True)
    not_after = Column(UtcDateTime, nullable=True)

    source = Column(Enum(SourceType), default=SourceType.NETWORK_SCAN)
    source_ref = Column(String, index=True, nullable=True)  # ARN / vault URL / secret name / host:port

    chain_valid = Column(Boolean, default=None, nullable=True)  # None = not validated (yet)
    revoked = Column(Boolean, default=None, nullable=True)
    revocation_check_method = Column(String, nullable=True)  # OCSP | CRL | none

    status = Column(Enum(CertStatus), default=CertStatus.UNKNOWN)

    risk_score = Column(Float, nullable=True)      # 0-100
    risk_tier = Column(Enum(RiskTier), nullable=True)
    predicted_renewal_delay_days = Column(Float, nullable=True)
    predicted_outage_impact = Column(String, nullable=True)  # low/medium/high

    auto_renew_enabled = Column(Boolean, default=False)
    renewal_provider = Column(String, nullable=True)  # letsencrypt | vault | aws_acm

    owner_id = Column(String, ForeignKey("owners.id"), nullable=True)
    owner = relationship("Owner", back_populates="certificates")

    created_at = Column(UtcDateTime, default=utcnow)
    updated_at = Column(UtcDateTime, default=utcnow, onupdate=utcnow)
    last_scanned_at = Column(UtcDateTime, nullable=True)

    renewal_tasks = relationship("RenewalTask", back_populates="certificate")
    scan_logs = relationship("ScanLog", back_populates="certificate")
    validations = relationship("ValidationResult", back_populates="certificate", order_by="ValidationResult.checked_at.desc()")


class RenewalTaskStatus(str, enum.Enum):
    PENDING = "pending"
    JIRA_CREATED = "jira_created"
    IN_PROGRESS = "in_progress"
    AUTO_RENEWED = "auto_renewed"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # nothing was done (e.g. provider cannot force renewal)
    DRY_RUN = "dry_run"  # simulated only, no change made


class RenewalTask(Base):
    __tablename__ = "renewal_tasks"
    id = Column(String, primary_key=True, default=gen_uuid)
    certificate_id = Column(String, ForeignKey("certificates.id"))
    certificate = relationship("Certificate", back_populates="renewal_tasks")

    status = Column(Enum(RenewalTaskStatus), default=RenewalTaskStatus.PENDING)
    jira_ticket_key = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    triggered_by = Column(String, default="scheduler")  # scheduler | agent | manual
    provider = Column(String, nullable=True)  # letsencrypt | vault | aws_acm | jira
    dry_run = Column(Boolean, default=False)
    previous_serial = Column(String, nullable=True)
    new_serial = Column(String, nullable=True)
    created_at = Column(UtcDateTime, default=utcnow)
    completed_at = Column(UtcDateTime, nullable=True)


class ScanLog(Base):
    __tablename__ = "scan_logs"
    id = Column(String, primary_key=True, default=gen_uuid)
    certificate_id = Column(String, ForeignKey("certificates.id"), nullable=True)
    certificate = relationship("Certificate", back_populates="scan_logs")

    target = Column(String, nullable=False)
    success = Column(Boolean, default=True)
    message = Column(Text, nullable=True)
    created_at = Column(UtcDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer")  # admin | operator | viewer
    created_at = Column(UtcDateTime, default=utcnow)


class ScanTarget(Base):
    """A TLS endpoint the network provider monitors."""
    __tablename__ = "scan_targets"
    id = Column(String, primary_key=True, default=gen_uuid)
    host = Column(String, nullable=False)
    port = Column(Integer, nullable=False, default=443)
    server_name = Column(String, nullable=True)  # SNI + hostname to validate; defaults to host
    environment = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(UtcDateTime, default=utcnow)
    last_scanned_at = Column(UtcDateTime, nullable=True)
    last_error = Column(Text, nullable=True)


class JobRunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # some certificates/targets failed
    FAILED = "failed"
    SKIPPED = "skipped"  # provider not configured


class JobRun(Base):
    """Persisted record of every discovery/monitoring run, including retries."""
    __tablename__ = "job_runs"
    id = Column(String, primary_key=True, default=gen_uuid)
    job = Column(String, nullable=False, default="discovery")
    provider = Column(String, nullable=False, index=True)
    status = Column(Enum(JobRunStatus), default=JobRunStatus.RUNNING)
    attempts = Column(Integer, default=0)
    discovered = Column(Integer, default=0)
    errors = Column(Text, nullable=True)  # JSON list
    triggered_by = Column(String, default="scheduler")
    started_at = Column(UtcDateTime, default=utcnow)
    finished_at = Column(UtcDateTime, nullable=True)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id = Column(String, primary_key=True, default=gen_uuid)
    certificate_id = Column(String, ForeignKey("certificates.id"), index=True, nullable=False)
    certificate = relationship("Certificate", back_populates="validations")
    checked_at = Column(UtcDateTime, default=utcnow, index=True)
    overall = Column(String, nullable=False)  # pass | warn | fail
    chain_valid = Column(Boolean, nullable=True)
    revoked = Column(Boolean, nullable=True)
    findings = Column(Text, nullable=False)  # JSON list of {check, severity, message, details}
