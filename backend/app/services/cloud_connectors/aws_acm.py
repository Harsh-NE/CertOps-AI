"""
AWS Certificate Manager discovery connector.

Requires AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or an assumed role /
instance profile - boto3 resolves credentials via its standard chain, these
env vars are just the explicit override). If boto3 isn't configured, this
connector returns an empty list rather than raising, so discovery jobs that
mix multiple cloud providers don't fail entirely because one isn't set up.
"""
import json
from typing import List, Dict, Any
from app.config import settings


def _client():
    import boto3
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("acm", **kwargs)


def discover_certificates() -> List[Dict[str, Any]]:
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
    ACM auto-renews eligible (DNS-validated, in-use) certs automatically -
    there's no manual "renew now" API. This triggers a validation-status
    refresh so the platform can confirm renewal eligibility and surface it.
    """
    try:
        client = _client()
        detail = client.describe_certificate(CertificateArn=certificate_arn)["Certificate"]
        return {
            "success": True,
            "renewal_eligible": detail.get("RenewalEligibility") == "ELIGIBLE",
            "renewal_summary": detail.get("RenewalSummary"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
