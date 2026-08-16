from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import schemas
from app.auth import get_current_user
from app.services.pipeline import process_discovered_certificate
from app.services.cloud_connectors import aws_acm, azure_keyvault, gcp_cert_manager, k8s_secrets

router = APIRouter(prefix="/api/cloud", tags=["cloud discovery"])

CONNECTORS = {
    "aws_acm": aws_acm.discover_certificates,
    "azure_key_vault": azure_keyvault.discover_certificates,
    "gcp_cert_manager": gcp_cert_manager.discover_certificates,
    "kubernetes_secret": k8s_secrets.discover_certificates,
}


@router.post("/discover")
def discover(payload: schemas.CloudScanRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Run discovery against one or more configured cloud providers."""
    summary = {}
    for provider in payload.providers:
        fn = CONNECTORS.get(provider)
        if not fn:
            summary[provider] = {"error": "unknown provider"}
            continue
        raw_certs = fn()
        processed, errors = [], []
        for raw in raw_certs:
            if "error" in raw:
                errors.append(raw["error"])
                continue
            k8s_annotations = raw.pop("k8s_annotations", None)
            cert = process_discovered_certificate(db, raw, k8s_annotations=k8s_annotations)
            processed.append(cert.id)
        summary[provider] = {"discovered": len(processed), "errors": errors}
    return summary
