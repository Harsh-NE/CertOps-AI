"""
Central configuration. All values are overridable via environment variables / .env file.
Nothing here requires cloud credentials to run the app in "local/demo" mode.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "Certificate Lifecycle Management Platform"
    ENV: str = "development"

    # --- Database ---
    # Defaults to a local SQLite file so the project runs with zero external deps.
    # Set DATABASE_URL to a Postgres DSN for production, e.g.
    # postgresql+psycopg2://certadmin:certpass@localhost:5432/certplatform
    DATABASE_URL: str = "sqlite:///./certplatform.db"

    # --- Auth ---
    SECRET_KEY: str = "CHANGE_ME_super_secret_key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12
    JWT_ALGORITHM: str = "HS256"

    # --- Scheduler ---
    SCAN_INTERVAL_HOURS: int = 24
    EXPIRY_WARNING_DAYS: int = 30
    EXPIRY_CRITICAL_DAYS: int = 7

    # --- AI Agent (OpenAI-compatible) ---
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None  # set to point at Azure OpenAI / Ollama / vLLM etc.
    AGENT_MODEL: str = "gpt-4o-mini"
    AI_AGENT_ENABLED: bool = False  # auto-flips true if OPENAI_API_KEY is set

    # --- Cloud connectors (all optional; connector no-ops if creds absent) ---
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"

    AZURE_TENANT_ID: Optional[str] = None
    AZURE_CLIENT_ID: Optional[str] = None
    AZURE_CLIENT_SECRET: Optional[str] = None
    AZURE_KEY_VAULT_URL: Optional[str] = None

    GCP_PROJECT_ID: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None  # path to service-account json

    KUBE_CONFIG_PATH: Optional[str] = None  # None = use in-cluster config if available

    # --- Auto-renewal ---
    LETSENCRYPT_EMAIL: Optional[str] = None
    LETSENCRYPT_STAGING: bool = True  # keep True until you're ready for production ACME

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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
if settings.OPENAI_API_KEY:
    settings.AI_AGENT_ENABLED = True
