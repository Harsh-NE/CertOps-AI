import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Integer, Float, Boolean, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship
from app.database import Base


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

    common_name = Column(String, index=True, nullable=False)
    san_list = Column(Text, nullable=True)  # JSON-encoded list
    issuer = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    serial_number = Column(String, nullable=True)
    signature_algorithm = Column(String, nullable=True)
    key_size = Column(Integer, nullable=True)

    not_before = Column(DateTime, nullable=True)
    not_after = Column(DateTime, nullable=True)

    source = Column(Enum(SourceType), default=SourceType.NETWORK_SCAN)
    source_ref = Column(String, nullable=True)  # ARN / vault URL / secret name / host:port

    chain_valid = Column(Boolean, default=None, nullable=True)
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

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_scanned_at = Column(DateTime, nullable=True)

    renewal_tasks = relationship("RenewalTask", back_populates="certificate")
    scan_logs = relationship("ScanLog", back_populates="certificate")


class RenewalTaskStatus(str, enum.Enum):
    PENDING = "pending"
    JIRA_CREATED = "jira_created"
    IN_PROGRESS = "in_progress"
    AUTO_RENEWED = "auto_renewed"
    COMPLETED = "completed"
    FAILED = "failed"


class RenewalTask(Base):
    __tablename__ = "renewal_tasks"
    id = Column(String, primary_key=True, default=gen_uuid)
    certificate_id = Column(String, ForeignKey("certificates.id"))
    certificate = relationship("Certificate", back_populates="renewal_tasks")

    status = Column(Enum(RenewalTaskStatus), default=RenewalTaskStatus.PENDING)
    jira_ticket_key = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    triggered_by = Column(String, default="scheduler")  # scheduler | agent | manual
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class ScanLog(Base):
    __tablename__ = "scan_logs"
    id = Column(String, primary_key=True, default=gen_uuid)
    certificate_id = Column(String, ForeignKey("certificates.id"), nullable=True)
    certificate = relationship("Certificate", back_populates="scan_logs")

    target = Column(String, nullable=False)
    success = Column(Boolean, default=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer")  # admin | operator | viewer
    created_at = Column(DateTime, default=datetime.utcnow)
