import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.scheduler import start_scheduler
from app.routers import auth, certificates, owners, renewals, risk, agent, cloud

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.APP_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production to your frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(certificates.router)
app.include_router(owners.router)
app.include_router(renewals.router)
app.include_router(risk.router)
app.include_router(agent.router)
app.include_router(cloud.router)


@app.on_event("startup")
def on_startup():
    start_scheduler()


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "ai_agent_enabled": settings.AI_AGENT_ENABLED}
