"""
Google Certificate Manager provider. Uses the real `google-cloud-certificate-manager` response shape; tests inject
a fake client, so this adapter is SIMULATED in this project. Google-managed certificates do not expose their PEM,
so for those only metadata (SANs, expiry, state) is available; self-managed ones carry a PEM and are fully parsed.
"""
from datetime import datetime
from typing import Any, Optional

from app.config import settings
from app.providers.base import (
    CertificateProvider,
    DiscoveryResult,
    NormalizedCertificate,
    ProviderAuthError,
    ProviderError,
    TransientProviderError,
)
from app.providers.normalize import environment_from, load_chain, normalize_x509
from app.timeutil import ensure_utc


def map_gcp_error(e: Exception) -> ProviderError:
    name = type(e).__name__
    if name in ("PermissionDenied", "Unauthenticated", "DefaultCredentialsError"):
        return ProviderAuthError(f"{name}: {e}")
    if name in ("ServiceUnavailable", "DeadlineExceeded", "TooManyRequests", "InternalServerError", "RetryError"):
        return TransientProviderError(f"{name}: {e}")
    return ProviderError(f"{name}: {e}")


def _to_dt(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if hasattr(ts, "ToDatetime"):
        return ensure_utc(ts.ToDatetime())
    return ensure_utc(ts) if isinstance(ts, datetime) else None


class GcpCertManagerProvider(CertificateProvider):
    name = "gcp_cert_manager"

    def __init__(self, client: Any = None, project: Optional[str] = None, location: str = "-"):
        self._client = client
        self.project = project or settings.GCP_PROJECT_ID
        self.location = location  # '-' lists all locations

    def is_configured(self) -> bool:
        return bool(self.project)

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            if self._client is None:
                from google.cloud import certificate_manager_v1
                self._client = certificate_manager_v1.CertificateManagerClient()
            items = list(self._client.list_certificates(parent=f"projects/{self.project}/locations/{self.location}"))
        except Exception as e:
            raise map_gcp_error(e) from e
        for c in items:
            try:
                labels = dict(getattr(c, "labels", None) or {})
                env = environment_from(labels)
                managed = bool(getattr(c, "managed", None))
                pem = getattr(getattr(c, "self_managed", None), "pem_certificate", "") or ""
                extras = {"managed": managed, "labels": labels,
                          "state": getattr(getattr(c, "managed", None), "state", None) and str(c.managed.state)}
                if pem:
                    chain = load_chain(pem.encode())
                    result.certificates.append(normalize_x509(chain[0], provider=self.name, source_ref=c.name,
                                                              chain=chain, environment=env, extras=extras))
                else:
                    sans = list(getattr(c, "san_dnsnames", []) or [])
                    result.certificates.append(NormalizedCertificate(
                        provider=self.name, source_ref=c.name, common_name=sans[0] if sans else c.name.rsplit("/", 1)[-1],
                        issuer="Google Trust Services (managed)" if managed else None,
                        not_after=_to_dt(getattr(c, "expire_time", None)), san_dns=sans,
                        is_wildcard=any(s.startswith("*.") for s in sans), environment=env, extras=extras))
            except Exception as e:
                result.errors.append(f"{getattr(c, 'name', '?')}: {type(e).__name__}: {e}")
        return result
