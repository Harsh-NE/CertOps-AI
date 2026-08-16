from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.auth import verify_password, create_access_token, hash_password
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(
        {"sub": user.username, "role": user.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token}


@router.post("/register", response_model=schemas.Token)
def register(payload: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    Open registration for demo/lab purposes. In production, gate this behind
    an admin-only endpoint or your SSO provider.
    """
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    is_first_user = db.query(models.User).count() == 0
    user = models.User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role="admin" if is_first_user else "viewer",
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.username, "role": user.role})
    return {"access_token": token}
