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
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"  # comma-separated

    # --- Network safety / validation ---
    ALLOW_PRIVATE_TARGETS: bool = False  # scanning/OCSP to loopback/private IPs (lab only)
    TRUST_STORE_EXTRA_PEM: Optional[str] = None  # extra root CAs (PEM bundle path), e.g. the lab CA
    OCSP_ENABLED: bool = True
    NETWORK_TIMEOUT_SECONDS: float = 5.0
    JOB_MAX_ATTEMPTS: int = 3
    JOB_BACKOFF_SECONDS: float = 2.0

    # --- Scheduler ---
    SCHEDULER_ENABLED: bool = True
    SCAN_INTERVAL_HOURS: int = 24
    EXPIRY_WARNING_DAYS: int = 30
    EXPIRY_CRITICAL_DAYS: int = 7

    # --- AI agent (Gemini via langchain-google-genai) ---
    # Set GEMINI_API_KEY in .env. Run `python -m app.agent.llm --list-models` to see which models your key can use.
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AGENT_TIMEOUT_SECONDS: float = 60.0  # whole request
    AGENT_TOOL_TIMEOUT_SECONDS: float = 15.0  # per tool call
    AGENT_MAX_TOOL_CALLS: int = 8
    AI_AGENT_ENABLED: bool = False  # derived: true when GEMINI_API_KEY is set

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
    RENEWAL_PLAN_TTL_MINUTES: int = 15
    RENEWAL_MAX_OPEN_PLANS_PER_ACTOR: int = 20  # caps the approval queue a (possibly looping) agent can create
    LETSENCRYPT_EMAIL: Optional[str] = None
    # Default is Let's Encrypt STAGING. For local tests use Pebble: https://localhost:14000/dir
    ACME_DIRECTORY_URL: str = "https://acme-staging-v02.api.letsencrypt.org/directory"
    ACME_ALLOW_PRODUCTION: bool = False  # the production directory is refused unless this is true
    ACME_VERIFY_TLS: bool = True  # false only for Pebble's self-signed API certificate
    ACME_CHALLENGE: str = "webroot"  # webroot | challtestsrv
    ACME_WEBROOT_PATH: Optional[str] = None  # directory served at /.well-known/acme-challenge/
    ACME_CHALLTESTSRV_URL: Optional[str] = None  # e.g. http://localhost:8055 (Pebble's challenge mock)
    VAULT_ADDR: Optional[str] = None  # secret store for issued keys/certs; required to execute a renewal
    VAULT_TOKEN: Optional[str] = None
    VAULT_KV_MOUNT: str = "secret"
    VAULT_PATH_PREFIX: str = "certops"

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

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD", "GEMINI_API_KEY", mode="before")
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
if settings.GEMINI_API_KEY:
    settings.AI_AGENT_ENABLED = True
