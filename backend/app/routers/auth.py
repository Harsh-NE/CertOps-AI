from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import get_db

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
    Self-service sign-up, disabled unless ALLOW_REGISTRATION=true. Self-registered
    users are always read-only "viewers"; admins come from ADMIN_USERNAME/ADMIN_PASSWORD
    (bootstrapped at startup) or are promoted by an existing admin.
    """
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = models.User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role="viewer",
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.username, "role": user.role})
    return {"access_token": token}


@router.get("/me")
def me(user: models.User = Depends(get_current_user)):
    """Who am I? The UI uses the role to hide actions the server would refuse anyway."""
    return {"username": user.username, "role": user.role}
