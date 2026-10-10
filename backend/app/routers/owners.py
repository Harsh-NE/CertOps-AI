from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user, require_role
from app.database import get_db

router = APIRouter(prefix="/api/owners", tags=["owners"])


@router.get("", response_model=List[schemas.OwnerOut])
def list_owners(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.query(models.Owner).all()


@router.post("", response_model=schemas.OwnerOut)
def create_owner(owner: schemas.OwnerCreate, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    row = models.Owner(name=owner.name, team=owner.team, email=owner.email,
                        slack_channel=owner.slack_channel, source="manual")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/{owner_id}/assign/{cert_id}", response_model=schemas.CertificateOut)
def assign_owner(owner_id: str, cert_id: str, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    cert = db.query(models.Certificate).filter(models.Certificate.id == cert_id).first()
    owner = db.query(models.Owner).filter(models.Owner.id == owner_id).first()
    if not cert or not owner:
        raise HTTPException(404, "Certificate or owner not found")
    cert.owner_id = owner.id
    db.commit()
    db.refresh(cert)
    return cert
