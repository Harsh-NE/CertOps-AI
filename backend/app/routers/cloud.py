from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas
from app.auth import require_role
from app.database import get_db
from app.jobs import run_provider_job
from app.providers.registry import PROVIDER_NAMES, build_providers

router = APIRouter(prefix="/api/cloud", tags=["cloud discovery"])


@router.post("/discover")
def discover(payload: schemas.CloudScanRequest, db: Session = Depends(get_db),
             user=Depends(require_role("operator"))):
    """Run discovery now for one or more providers (retries + persisted run state apply)."""
    providers = build_providers(db)
    summary = {}
    for name in payload.providers:
        provider = providers.get(name)
        if provider is None:
            summary[name] = {"status": "failed", "discovered": 0,
                             "errors": [f"unknown provider; choose from {PROVIDER_NAMES}"]}
            continue
        summary[name] = run_provider_job(provider, triggered_by=f"user:{user.username}")
    return summary
