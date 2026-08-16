"""
Auto-Renewal Engine.

Three providers, each independent and best-effort:
  - Let's Encrypt (ACME protocol) for publicly-issued certs
  - HashiCorp Vault PKI secrets engine for internal/private CAs
  - AWS ACM (see cloud_connectors/aws_acm.py::request_renewal - ACM renews
    eligible certs automatically; this module just triggers/confirms it)

All renewal attempts are recorded as RenewalTask rows by the caller
(routers/renewals.py) so there's always an audit trail, whether renewal
succeeded, failed, or was never attempted (e.g. missing credentials).
"""
from typing import Dict, Any
from app.config import settings


def renew_via_letsencrypt(domain: str, webroot_path: str = "/var/www/html") -> Dict[str, Any]:
    """
    Requests/renews a certificate via ACME HTTP-01 challenge using the
    `acme` library. Requires LETSENCRYPT_EMAIL to be set and the webroot to
    be served by the same web server fronting `domain` on port 80.
    Uses the Let's Encrypt STAGING endpoint unless LETSENCRYPT_STAGING=false,
    to avoid hitting production rate limits during testing.
    """
    if not settings.LETSENCRYPT_EMAIL:
        return {"success": False, "error": "LETSENCRYPT_EMAIL not configured"}

    directory_url = (
        "https://acme-staging-v02.api.letsencrypt.org/directory"
        if settings.LETSENCRYPT_STAGING
        else "https://acme-v02.api.letsencrypt.org/directory"
    )
    try:
        # The full ACME handshake (account registration, HTTP-01 challenge
        # fulfillment, CSR submission) is intentionally delegated to certbot
        # in production use - it already implements this correctly and is
        # far more robust than a hand-rolled ACME client. This function
        # shells out to it so the platform can trigger renewals
        # programmatically while still producing a real, valid certificate.
        import subprocess
        cmd = [
            "certbot", "certonly", "--webroot", "-w", webroot_path,
            "-d", domain, "--email", settings.LETSENCRYPT_EMAIL,
            "--agree-tos", "--non-interactive",
            "--server", directory_url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "success": proc.returncode == 0,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
            "staging": settings.LETSENCRYPT_STAGING,
        }
    except FileNotFoundError:
        return {"success": False, "error": "certbot not installed on this host - see SETUP.md"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def renew_via_vault(pki_role: str, common_name: str, ttl: str = "8760h") -> Dict[str, Any]:
    """Issues a fresh certificate from a Vault PKI secrets engine role."""
    if not (settings.VAULT_ADDR and settings.VAULT_TOKEN):
        return {"success": False, "error": "VAULT_ADDR / VAULT_TOKEN not configured"}
    try:
        import hvac
        client = hvac.Client(url=settings.VAULT_ADDR, token=settings.VAULT_TOKEN)
        if not client.is_authenticated():
            return {"success": False, "error": "Vault authentication failed"}

        result = client.secrets.pki.generate_certificate(
            name=pki_role,
            common_name=common_name,
            extra_params={"ttl": ttl},
        )
        data = result.get("data", {})
        return {
            "success": True,
            "certificate": data.get("certificate"),
            "issuing_ca": data.get("issuing_ca"),
            "serial_number": data.get("serial_number"),
            "expiration": data.get("expiration"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def renew_via_aws_acm(certificate_arn: str) -> Dict[str, Any]:
    from app.services.cloud_connectors.aws_acm import request_renewal
    return request_renewal(certificate_arn)


PROVIDER_DISPATCH = {
    "letsencrypt": renew_via_letsencrypt,
    "vault": renew_via_vault,
    "aws_acm": renew_via_aws_acm,
}


def trigger_renewal(provider: str, **kwargs) -> Dict[str, Any]:
    fn = PROVIDER_DISPATCH.get(provider)
    if not fn:
        return {"success": False, "error": f"Unknown renewal provider: {provider}"}
    return fn(**kwargs)
