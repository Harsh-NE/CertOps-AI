import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import SessionLocal
from app.routers import agent, auth, certificates, cloud, owners, renewals, risk
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("certops")


def bootstrap_admin() -> None:
    """Create the initial admin from ADMIN_USERNAME/ADMIN_PASSWORD if there are no users yet."""
    from app import models
    from app.auth import hash_password

    if not (settings.ADMIN_USERNAME and settings.ADMIN_PASSWORD):
        return
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            db.add(models.User(username=settings.ADMIN_USERNAME,
                               hashed_password=hash_password(settings.ADMIN_PASSWORD), role="admin"))
            db.commit()
            logger.info("Bootstrapped admin user '%s'", settings.ADMIN_USERNAME)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.resolved_secret_key()  # fail fast on a missing/placeholder key outside development
    bootstrap_admin()
    if settings.SCHEDULER_ENABLED:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=settings.APP_NAME, version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, certificates, owners, renewals, risk, agent, cloud):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "ai_agent_enabled": settings.AI_AGENT_ENABLED,
            "registration_enabled": settings.ALLOW_REGISTRATION}
