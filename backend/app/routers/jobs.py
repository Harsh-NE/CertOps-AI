import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db

router = APIRouter(prefix="/api", tags=["jobs and targets"])


@router.get("/jobs", response_model=List[schemas.JobRunOut])
def list_job_runs(provider: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    q = db.query(models.JobRun)
    if provider:
        q = q.filter(models.JobRun.provider == provider)
    rows = q.order_by(models.JobRun.started_at.desc()).limit(min(limit, 200)).all()
    return [schemas.JobRunOut(id=r.id, provider=r.provider, status=r.status.value, attempts=r.attempts,
                              discovered=r.discovered, errors=json.loads(r.errors or "[]"),
                              triggered_by=r.triggered_by, started_at=r.started_at, finished_at=r.finished_at)
            for r in rows]


@router.get("/targets", response_model=List[schemas.ScanTargetOut])
def list_targets(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.query(models.ScanTarget).order_by(models.ScanTarget.created_at).all()


@router.post("/targets", response_model=schemas.ScanTargetOut)
def create_target(payload: schemas.TargetIn, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    row = models.ScanTarget(host=payload.host, port=payload.port, server_name=payload.server_name,
                            environment=(payload.environment or "").lower() or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/targets/{target_id}", status_code=204)
def delete_target(target_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    row = db.get(models.ScanTarget, target_id)
    if not row:
        raise HTTPException(404, "Target not found")
    db.delete(row)
    db.commit()
