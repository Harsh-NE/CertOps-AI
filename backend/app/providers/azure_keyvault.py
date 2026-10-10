"""
Azure Key Vault provider. Uses the real `azure-keyvault-certificates` client shape; tests inject a fake client
built from generated certificates, so this adapter is SIMULATED in this project (never run against Azure).
"""
from typing import Any, Optional

from app.config import settings
from app.providers.base import (
    CertificateProvider,
    DiscoveryResult,
    ProviderAuthError,
    ProviderError,
    TransientProviderError,
)
from app.providers.normalize import environment_from, load_chain, normalize_x509


def _credential():
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    if settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET:
        return ClientSecretCredential(settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET)
    return DefaultAzureCredential()


def map_azure_error(e: Exception) -> ProviderError:
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    if name == "ClientAuthenticationError" or status in (401, 403):
        return ProviderAuthError(f"{name}: {e}")
    if name in ("ServiceRequestError", "ServiceResponseError") or status in (429, 500, 502, 503, 504):
        return TransientProviderError(f"{name}: {e}")
    return ProviderError(f"{name}: {e}")


class AzureKeyVaultProvider(CertificateProvider):
    name = "azure_key_vault"

    def __init__(self, client: Any = None, vault_url: Optional[str] = None):
        self._client = client
        self.vault_url = (vault_url or settings.AZURE_KEY_VAULT_URL or "").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.vault_url)

    def _get_client(self):
        if self._client is None:
            from azure.keyvault.certificates import CertificateClient
            self._client = CertificateClient(vault_url=self.vault_url, credential=_credential())
        return self._client

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            client = self._get_client()
            props = list(client.list_properties_of_certificates())
        except Exception as e:
            raise map_azure_error(e) from e
        for prop in props:
            ref = f"{self.vault_url}/certificates/{prop.name}"
            try:
                cert = client.get_certificate(prop.name)
                chain = load_chain(bytes(cert.cer))
                tags = dict(getattr(prop, "tags", None) or {})
                policy = getattr(cert, "policy", None)
                result.certificates.append(normalize_x509(
                    chain[0], provider=self.name, source_ref=ref, chain=chain, environment=environment_from(tags),
                    extras={"enabled": getattr(prop, "enabled", None), "tags": tags,
                            "vault_issuer_name": getattr(policy, "issuer_name", None) if policy else None}))
            except Exception as e:
                result.errors.append(f"{ref}: {type(e).__name__}: {e}")
        return result
