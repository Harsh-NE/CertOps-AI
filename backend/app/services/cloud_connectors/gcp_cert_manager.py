"""
Google Certificate Manager discovery connector.
Requires GCP_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS pointing at a
service-account JSON key (standard ADC pattern).
"""
import json
from typing import Any, Dict, List

from app.config import settings


def discover_certificates() -> List[Dict[str, Any]]:
    if not settings.GCP_PROJECT_ID:
        return []
    try:
        from google.cloud import certificate_manager_v1
        client = certificate_manager_v1.CertificateManagerClient()
        parent = f"projects/{settings.GCP_PROJECT_ID}/locations/global"
    except Exception as e:
        return [{"error": f"GCP Certificate Manager client init failed: {e}"}]

    results = []
    try:
        for cert in client.list_certificates(parent=parent):
            san = list(cert.san_dnsnames) if cert.san_dnsnames else []
            results.append({
                "common_name": san[0] if san else cert.name,
                "san_list": json.dumps(san),
                "issuer": "Google Trust Services" if cert.managed else "self-managed",
                "subject": san[0] if san else cert.name,
                "not_before": None,
                "not_after": cert.expire_time.ToDatetime() if cert.expire_time else None,
                "signature_algorithm": None,
                "source": "gcp_cert_manager",
                "source_ref": cert.name,
                "managed": bool(cert.managed),
            })
    except Exception as e:
        results.append({"error": f"GCP Certificate Manager discovery error: {e}"})
    return results
