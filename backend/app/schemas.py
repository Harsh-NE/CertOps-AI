from datetime import datetime
from typing import Any, Dict, List, Optional, Union

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
    expected_hostname: Optional[str] = None


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
    last_validated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TargetIn(BaseModel):
    host: str
    port: int = 443
    server_name: Optional[str] = None
    environment: Optional[str] = None


class ScanTargetRequest(BaseModel):
    # "host", "host:port", "host:port#server_name", or a structured target
    targets: List[Union[str, TargetIn]]


class ScanTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    host: str
    port: int
    server_name: Optional[str] = None
    environment: Optional[str] = None
    enabled: bool
    last_scanned_at: Optional[datetime] = None
    last_error: Optional[str] = None


class JobRunOut(BaseModel):
    id: str
    provider: str
    status: str
    attempts: int
    discovered: int
    errors: List[str]
    triggered_by: str
    started_at: datetime
    finished_at: Optional[datetime] = None


class ValidationOut(BaseModel):
    id: str
    checked_at: datetime
    overall: str
    chain_valid: Optional[bool] = None
    revoked: Optional[bool] = None
    findings: List[Dict[str, Any]]


class CloudScanRequest(BaseModel):
    providers: List[str]  # subset of ["aws_acm","azure_key_vault","gcp_cert_manager","kubernetes_secret"]


class RenewalTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    certificate_id: str
    certificate_name: Optional[str] = None
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
    tool_calls: List[Dict[str, Any]] = []
    pending_plans: int = 0
    configured: bool = True
    request_id: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


class AgentToolCallOut(BaseModel):
    id: str
    request_id: str
    actor: str
    tool: str
    args: Optional[Any] = None
    status: str
    duration_ms: int
    created_at: datetime


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


class RiskPredictIn(BaseModel):
    """What-if input for POST /api/risk/predict. Only days_to_expiry is required."""
    days_to_expiry: float
    validity_days: Optional[float] = 90
    key_size: Optional[int] = 2048
    key_algorithm: Optional[str] = "RSA"
    chain_depth: Optional[int] = 3
    san_count: Optional[int] = 1
    is_wildcard: bool = False
    auto_renew: bool = False
    has_owner: bool = True
    is_prod: bool = False
    weak_signature: bool = False
    chain_invalid: bool = False
    hostname_mismatch: bool = False
    issuer: Optional[str] = None
    source: Optional[str] = "network_scan"
    prior_attempts: int = 0
    prior_failures: int = 0


class RiskFactor(BaseModel):
    feature: str
    value: Any
    typical: Any
    effect: float


class RiskPredictionOut(BaseModel):
    probability: float
    tier: str
    model_used: str
    model_version: Optional[str] = None
    data_source: str
    factors: List[RiskFactor] = []
    risk_score: Optional[float] = None
    floor_reason: Optional[str] = None
    predicted_outage_impact: Optional[str] = None


class RenewalPlanOut(BaseModel):
    id: str
    certificate_id: str
    provider: str
    status: str
    executable: bool
    not_executable_reason: Optional[str] = None
    steps: List[str]
    warnings: List[str]
    created_by: str
    created_at: datetime
    expires_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
