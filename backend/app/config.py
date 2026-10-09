"""
Central configuration, loaded from environment variables and an optional
repo-root `.env` file (copy `.env.example` to `.env`). No secrets live in code.
"""
import logging
import secrets
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("config")

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    APP_NAME: str = "CertOps AI"
    ENV: str = "development"  # development | test | production

    # --- Database ---
    DATABASE_URL: str = "sqlite:///./certplatform.db"

    # --- Auth ---
    # In development an ephemeral key is generated if unset (tokens die on restart).
    # In any other ENV the app refuses to start without an explicit key.
    SECRET_KEY: Optional[str] = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12
    JWT_ALGORITHM: str = "HS256"
    ALLOW_REGISTRATION: bool = False  # open sign-up creates read-only "viewer" users only
    ADMIN_USERNAME: Optional[str] = None  # bootstrap admin, created at startup if no users exist
    ADMIN_PASSWORD: Optional[str] = None
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Scheduler ---
    SCHEDULER_ENABLED: bool = True
    SCAN_INTERVAL_HOURS: int = 24
    EXPIRY_WARNING_DAYS: int = 30
    EXPIRY_CRITICAL_DAYS: int = 7

    # --- AI agent (provider is replaced by Gemini in Phase 4) ---
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    AGENT_MODEL: str = "gpt-4o-mini"
    AI_AGENT_ENABLED: bool = False  # auto-enabled when a key is set

    # --- Cloud providers (all optional) ---
    # boto3 otherwise falls back to ~/.aws and instance profiles; the AWS connector
    # only runs when AWS_DISCOVERY_ENABLED=true so ambient credentials are never used by accident.
    AWS_DISCOVERY_ENABLED: bool = False
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"
    AWS_ENDPOINT_URL: Optional[str] = None  # e.g. a moto/localstack endpoint

    AZURE_TENANT_ID: Optional[str] = None
    AZURE_CLIENT_ID: Optional[str] = None
    AZURE_CLIENT_SECRET: Optional[str] = None
    AZURE_KEY_VAULT_URL: Optional[str] = None

    GCP_PROJECT_ID: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None

    KUBE_CONFIG_PATH: Optional[str] = None

    # --- Renewal ---
    LETSENCRYPT_EMAIL: Optional[str] = None
    LETSENCRYPT_STAGING: bool = True
    VAULT_ADDR: Optional[str] = None
    VAULT_TOKEN: Optional[str] = None

    # --- Notifications ---
    SLACK_BOT_TOKEN: Optional[str] = None
    SLACK_DEFAULT_CHANNEL: str = "#cert-alerts"
    TEAMS_WEBHOOK_URL: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_FROM: Optional[str] = None
    JIRA_BASE_URL: Optional[str] = None
    JIRA_EMAIL: Optional[str] = None
    JIRA_API_TOKEN: Optional[str] = None
    JIRA_PROJECT_KEY: str = "CERT"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    def resolved_secret_key(self) -> str:
        """Returns the JWT signing key; raises outside development if none is configured."""
        if self.SECRET_KEY:
            if self.SECRET_KEY.lower().startswith("change") and self.ENV == "production":
                raise RuntimeError("SECRET_KEY is still a placeholder; set a random value.")
            return self.SECRET_KEY
        if self.ENV == "production":
            raise RuntimeError("SECRET_KEY must be set when ENV=production.")
        logger.warning("SECRET_KEY not set: using an ephemeral key (tokens are invalidated on restart).")
        return _ephemeral_key()


_EPHEMERAL: Optional[str] = None


def _ephemeral_key() -> str:
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_urlsafe(48)
    return _EPHEMERAL


settings = Settings()
if settings.OPENAI_API_KEY:
    settings.AI_AGENT_ENABLED = True
