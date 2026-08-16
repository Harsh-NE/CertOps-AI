"""
Azure Key Vault certificate discovery + renewal connector.
Uses azure-identity's ClientSecretCredential when AZURE_* env vars are set,
otherwise falls back to DefaultAzureCredential (managed identity, az cli, etc).
"""
import json
from typing import List, Dict, Any
from app.config import settings


def _credential():
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    if settings.AZURE_TENANT_ID and settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET:
        return ClientSecretCredential(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
        )
    return DefaultAzureCredential()


def discover_certificates() -> List[Dict[str, Any]]:
    if not settings.AZURE_KEY_VAULT_URL:
        return []
    try:
        from azure.keyvault.certificates import CertificateClient
        client = CertificateClient(vault_url=settings.AZURE_KEY_VAULT_URL, credential=_credential())
    except Exception as e:
        return [{"error": f"Azure Key Vault client init failed: {e}"}]

    results = []
    try:
        for prop in client.list_properties_of_certificates():
            cert = client.get_certificate(prop.name)
            cert_bytes = cert.cer  # DER bytes of the public cert
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                parsed = x509.load_der_x509_certificate(cert_bytes, default_backend())
                cn = parsed.subject.rfc4514_string()
                not_before, not_after = parsed.not_valid_before_utc, parsed.not_valid_after_utc
                sig_algo = parsed.signature_algorithm_oid._name
            except Exception:
                cn, not_before, not_after, sig_algo = prop.name, None, prop.expires_on, None

            results.append({
                "common_name": cn,
                "san_list": json.dumps([]),
                "issuer": cert.policy.issuer_name if cert.policy else "unknown",
                "subject": cn,
                "not_before": not_before,
                "not_after": not_after,
                "signature_algorithm": sig_algo,
                "source": "azure_key_vault",
                "source_ref": f"{settings.AZURE_KEY_VAULT_URL}/certificates/{prop.name}",
            })
    except Exception as e:
        results.append({"error": f"Azure Key Vault discovery error: {e}"})
    return results


def request_renewal(certificate_name: str) -> Dict[str, Any]:
    try:
        from azure.keyvault.certificates import CertificateClient
        client = CertificateClient(vault_url=settings.AZURE_KEY_VAULT_URL, credential=_credential())
        poller = client.begin_create_certificate(certificate_name, policy=None)  # reuses existing policy
        return {"success": True, "operation_id": poller.polling_method()._operation.id if hasattr(poller, "polling_method") else None}
    except Exception as e:
        return {"success": False, "error": str(e)}
