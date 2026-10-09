from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    team: Optional[str] = None
    email: Optional[str] = None
    slack_channel: Optional[str] = None
    source: Optional[str] = None


class OwnerCreate(BaseModel):
    name: str
    team: Optional[str] = None
    email: Optional[str] = None
    slack_channel: Optional[str] = None


class CertificateBase(BaseModel):
    common_name: str
    issuer: Optional[str] = None
    subject: Optional[str] = None
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    signature_algorithm: Optional[str] = None
    key_algorithm: Optional[str] = None
    key_size: Optional[int] = None
    is_wildcard: Optional[bool] = None
    chain_depth: Optional[int] = None
    environment: Optional[str] = None
    fingerprint_sha256: Optional[str] = None


class CertificateOut(CertificateBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    san_list: Optional[str] = None
    source: str
    source_ref: Optional[str] = None
    chain_valid: Optional[bool] = None
    revoked: Optional[bool] = None
    status: str
    risk_score: Optional[float] = None
    risk_tier: Optional[str] = None
    predicted_renewal_delay_days: Optional[float] = None
    predicted_outage_impact: Optional[str] = None
    auto_renew_enabled: bool
    renewal_provider: Optional[str] = None
    owner: Optional[OwnerOut] = None
    last_scanned_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ScanTargetRequest(BaseModel):
    targets: List[str]  # e.g. ["api.example.com:443", "example.com"]


class CloudScanRequest(BaseModel):
    providers: List[str]  # subset of ["aws_acm","azure_key_vault","gcp_cert_manager","kubernetes_secret"]


class RenewalTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    certificate_id: str
    status: str
    jira_ticket_key: Optional[str] = None
    notes: Optional[str] = None
    triggered_by: str
    provider: Optional[str] = None
    dry_run: Optional[bool] = None
    previous_serial: Optional[str] = None
    new_serial: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class AgentQueryRequest(BaseModel):
    question: str


class AgentQueryResponse(BaseModel):
    answer: str
    used_tools: List[str] = []


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserLogin(BaseModel):
    username: str
    password: str


class DashboardStats(BaseModel):
    total_certificates: int
    expiring_30d: int
    expiring_7d: int
    expired: int
    invalid_chain: int
    high_risk: int
    auto_renew_enabled: int
    security_score: float
