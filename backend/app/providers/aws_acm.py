"""
AWS Certificate Manager provider (boto3). Tests run against moto; it has not been run against a real AWS
account. Discovery is opt-in (AWS_DISCOVERY_ENABLED) so ambient ~/.aws credentials are never used by accident.
"""
import re
from typing import Any, Callable, Dict, Optional

from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

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

STATUSES = ["ISSUED", "EXPIRED", "PENDING_VALIDATION", "INACTIVE", "VALIDATION_TIMED_OUT", "REVOKED", "FAILED"]
AUTH_CODES = {"AccessDeniedException", "UnrecognizedClientException", "InvalidClientTokenId", "ExpiredTokenException",
              "AuthFailure", "SignatureDoesNotMatch"}
TRANSIENT_CODES = {"ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded", "InternalFailure",
                   "ServiceUnavailable", "RequestTimeout"}
_KEY_ALGOS = {"RSA": "RSA", "EC": "EC"}


def _client():
    import boto3
    kwargs: Dict[str, Any] = {"region_name": settings.AWS_REGION}
    if settings.AWS_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.AWS_ENDPOINT_URL
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("acm", **kwargs)


def map_aws_error(e: Exception) -> ProviderError:
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in AUTH_CODES:
            return ProviderAuthError(f"{code}: {e}")
        if code in TRANSIENT_CODES:
            return TransientProviderError(f"{code}: {e}")
        return ProviderError(f"{code}: {e}")
    if isinstance(e, EndpointConnectionError | BotoCoreError):
        return TransientProviderError(str(e))
    return ProviderError(str(e))


def _parse_key_algorithm(value: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """ACM KeyAlgorithm looks like 'RSA-2048' or 'EC-prime256v1'."""
    if not value:
        return None, None
    m = re.match(r"(RSA)[-_](\d+)$", value)
    if m:
        return "RSA", int(m.group(2))
    m = re.match(r"EC[-_]\w*?(\d{3})", value)
    if m:
        return "EC", int(m.group(1))
    return value, None


class AwsAcmProvider(CertificateProvider):
    name = "aws_acm"

    def __init__(self, client_factory: Callable[[], Any] = _client, enabled: Optional[bool] = None):
        self._client_factory = client_factory
        self._enabled = settings.AWS_DISCOVERY_ENABLED if enabled is None else enabled

    def is_configured(self) -> bool:
        return self._enabled

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            client = self._client_factory()
            for page in client.get_paginator("list_certificates").paginate(CertificateStatuses=STATUSES):
                for summary in page.get("CertificateSummaryList", []):
                    arn = summary["CertificateArn"]
                    try:
                        result.certificates.append(self._one(client, arn))
                    except (ClientError, BotoCoreError, ValueError) as e:
                        result.errors.append(f"{arn}: {e}")
        except (ClientError, BotoCoreError) as e:
            raise map_aws_error(e) from e
        return result

    def _one(self, client, arn: str) -> NormalizedCertificate:
        detail = client.describe_certificate(CertificateArn=arn)["Certificate"]
        try:
            tags = {t["Key"]: t.get("Value", "") for t in client.list_tags_for_certificate(CertificateArn=arn).get("Tags", [])}
        except ClientError:
            tags = {}
        extras = {
            "acm_status": detail.get("Status"), "acm_type": detail.get("Type"),
            "renewal_eligibility": detail.get("RenewalEligibility"), "in_use_by": detail.get("InUseBy", []),
            "renewal_summary": detail.get("RenewalSummary"), "failure_reason": detail.get("FailureReason"),
            "tags": tags,
        }
        env = environment_from(tags)
        if detail.get("Status") == "ISSUED":
            body = client.get_certificate(CertificateArn=arn)
            pem = (body["Certificate"] + "\n" + body.get("CertificateChain", "")).encode()
            chain = load_chain(pem)
            return normalize_x509(chain[0], provider=self.name, source_ref=arn, chain=chain,
                                  environment=env, extras=extras)
        # Not issued yet (or no longer): only the metadata ACM exposes.
        algo, bits = _parse_key_algorithm(detail.get("KeyAlgorithm"))
        sans = detail.get("SubjectAlternativeNames") or [detail.get("DomainName")]
        sig = detail.get("SignatureAlgorithm") or ""
        return NormalizedCertificate(
            provider=self.name, source_ref=arn, common_name=detail.get("DomainName") or arn,
            issuer=detail.get("Issuer"), not_before=ensure_utc(detail.get("NotBefore")),
            not_after=ensure_utc(detail.get("NotAfter")), signature_algorithm=sig or None,
            signature_hash=("sha1" if "SHA1" in sig else "md5" if "MD5" in sig else None),
            key_algorithm=algo, key_size=bits, san_dns=[s for s in sans if s],
            is_wildcard=any((s or "").startswith("*.") for s in sans), environment=env, extras=extras)


def request_renewal(certificate_arn: str, client_factory: Callable[[], Any] = _client) -> Dict[str, Any]:
    """
    ACM renews eligible (DNS-validated, in-use) certificates by itself and has no "renew now" API, so this never
    performs a renewal: it reports eligibility only and returns success=False so callers never record a renewal
    that did not happen.
    """
    try:
        detail = client_factory().describe_certificate(CertificateArn=certificate_arn)["Certificate"]
        return {
            "success": False, "action_taken": False,
            "error": "ACM manages renewal itself; the platform cannot force it.",
            "renewal_eligible": detail.get("RenewalEligibility") == "ELIGIBLE",
            "renewal_summary": detail.get("RenewalSummary"),
        }
    except Exception as e:
        return {"success": False, "action_taken": False, "error": str(e)}
