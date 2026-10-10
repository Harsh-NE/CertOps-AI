"""
Provider adapter interface. Every source of certificates (network, AWS ACM, Azure Key Vault, GCP,
Kubernetes) implements `CertificateProvider.discover()` and returns `NormalizedCertificate`s, so the
validation, persistence, risk and agent layers never see provider-specific shapes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


class ProviderError(Exception):
    """Whole-provider failure. `kind` drives retry policy."""

    kind = "permanent"


class TransientProviderError(ProviderError):
    kind = "transient"  # timeouts, throttling, 5xx: retried with backoff


class ProviderAuthError(ProviderError):
    kind = "auth"  # bad/missing credentials, access denied: not retried


class ProviderNotConfigured(ProviderError):
    kind = "config"  # nothing to do: job is recorded as skipped


@dataclass
class NormalizedCertificate:
    provider: str                       # models.SourceType value, e.g. "aws_acm"
    source_ref: str                     # ARN / ns/name / vault URL / host:port
    common_name: str
    subject: Optional[str] = None
    issuer: Optional[str] = None
    serial_number: Optional[str] = None
    fingerprint_sha256: Optional[str] = None
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    signature_algorithm: Optional[str] = None
    signature_hash: Optional[str] = None      # sha256, sha1, md5, None (Ed25519)
    key_algorithm: Optional[str] = None
    key_size: Optional[int] = None
    san_dns: List[str] = field(default_factory=list)
    san_ips: List[str] = field(default_factory=list)
    is_wildcard: bool = False
    environment: Optional[str] = None
    expected_hostname: Optional[str] = None   # hostname the endpoint is supposed to serve (network targets)
    chain_der: List[bytes] = field(default_factory=list)  # presented chain, leaf first
    extras: Dict[str, Any] = field(default_factory=dict)  # provider-specific (k8s annotations, ACM status ...)


@dataclass
class DiscoveryResult:
    certificates: List[NormalizedCertificate] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)  # per-item problems; the run continues


class CertificateProvider(ABC):
    name: str  # == models.SourceType value

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def discover(self) -> DiscoveryResult:
        """Return every certificate visible to this provider. Raise ProviderError for whole-run failures."""
