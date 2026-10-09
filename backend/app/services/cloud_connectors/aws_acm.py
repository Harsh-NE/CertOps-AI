"""
AWS Certificate Manager discovery connector.

Requires AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or an assumed role /
instance profile - boto3 resolves credentials via its standard chain, these
env vars are just the explicit override). If boto3 isn't configured, this
connector returns an empty list rather than raising, so discovery jobs that
mix multiple cloud providers don't fail entirely because one isn't set up.
"""
import json
from typing import Any, Dict, List

from app.config import settings


def _client():
    import boto3
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.AWS_ENDPOINT_URL
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("acm", **kwargs)


def discover_certificates() -> List[Dict[str, Any]]:
    if not settings.AWS_DISCOVERY_ENABLED:
        return []  # opt-in: boto3 would otherwise pick up ambient ~/.aws credentials
    try:
        client = _client()
    except Exception as e:
        return [{"error": f"AWS ACM client init failed: {e}"}]

    results = []
    try:
        paginator = client.get_paginator("list_certificates")
        for page in paginator.paginate(CertificateStatuses=["ISSUED", "EXPIRED", "PENDING_VALIDATION"]):
            for summary in page.get("CertificateSummaryList", []):
                arn = summary["CertificateArn"]
                detail = client.describe_certificate(CertificateArn=arn)["Certificate"]
                results.append({
                    "common_name": detail.get("DomainName"),
                    "san_list": json.dumps(detail.get("SubjectAlternativeNames", [])),
                    "issuer": detail.get("Issuer", "Amazon"),
                    "subject": detail.get("DomainName"),
                    "not_before": detail.get("IssuedAt"),
                    "not_after": detail.get("NotAfter"),
                    "signature_algorithm": detail.get("SignatureAlgorithm"),
                    "source": "aws_acm",
                    "source_ref": arn,
                    "auto_renew_eligible": detail.get("RenewalEligibility") == "ELIGIBLE",
                    "status_raw": detail.get("Status"),
                })
    except Exception as e:
        results.append({"error": f"AWS ACM discovery error: {e}"})
    return results


def request_renewal(certificate_arn: str) -> Dict[str, Any]:
    """
    ACM renews eligible (DNS-validated, in-use) certificates by itself and has no
    "renew now" API, so this never performs a renewal. It reports eligibility only
    and returns success=False so callers do not record a renewal that didn't happen.
    """
    try:
        client = _client()
        detail = client.describe_certificate(CertificateArn=certificate_arn)["Certificate"]
        return {
            "success": False,
            "action_taken": False,
            "error": "ACM manages renewal itself; the platform cannot force it.",
            "renewal_eligible": detail.get("RenewalEligibility") == "ELIGIBLE",
            "renewal_summary": detail.get("RenewalSummary"),
        }
    except Exception as e:
        return {"success": False, "action_taken": False, "error": str(e)}
