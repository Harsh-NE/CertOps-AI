# CertOps AI: certificate lifecycle management platform

An agentic-AI platform for enterprise SSL/TLS certificate management:
**discovery → chain/revocation validation → ownership mapping → ML-based
risk prediction → AI agent (root-cause + Q&A) → auto-renewal → dashboard.**

Built for a security-automation / platform-engineering / SRE portfolio
project — but structured like a real production service, not a toy script.

> **Project status (honest):** this is a work in progress, being completed in phases. The table
> below describes the *target*; see **Current status** for what is verified today.

## Current status

| Area | Status |
|---|---|
| Network TLS scan, expiry, key size/algorithm, signature algorithm | Working (tested against local TLS servers) |
| Chain / trust validation, revocation | **Not implemented yet** (`chain_valid` is reported as unknown, never as valid). Phase 2 |
| AWS ACM / Azure Key Vault / GCP / Kubernetes discovery | Code exists, **untested** end to end. Phase 2 |
| Risk model | Trains on **synthetic** data; no real-world accuracy claim. Phase 3 |
| LangChain agent | Tools work; the LLM loop is untested. Phase 4 |
| Renewal | Dry-run by default; a real renewal needs `confirm=true`. Let's Encrypt/Vault flows are unverified. Phase 4 |

## Quick start

```bash
cp .env.example .env        # then set SECRET_KEY, ADMIN_PASSWORD and POSTGRES_PASSWORD
docker compose up --build   # UI http://localhost:3000, API http://localhost:8000/docs
```

Without Docker (Windows): `powershell -ExecutionPolicy Bypass -File scripts/dev.ps1`.
Tests: `cd backend && pip install -r requirements-dev.txt -c constraints.txt && pytest`.
Sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env`. Self-registration is off unless `ALLOW_REGISTRATION=true`
(and only creates read-only users).

## What it does

| Capability | How |
|---|---|
| **Certificate discovery** | Live TLS network scans (`host:port`) + AWS ACM, Azure Key Vault, Google Certificate Manager, Kubernetes Secrets |
| **Chain & revocation validation** | Full chain check, weak-algorithm detection (SHA-1/MD5, <2048-bit keys), OCSP revocation checks |
| **Ownership discovery** | Kubernetes annotations → Git `CODEOWNERS` → service catalog (`catalog-info.yaml`) metadata, in that priority order |
| **Predictive risk analysis** | Gradient-boosted ML models predict a 0–100 risk score, risk tier, expected renewal delay, and outage impact |
| **Agentic AI layer** | A LangChain tool-calling agent answers natural-language questions, explains *why* a cert is failing, and can open renewal tickets itself |
| **Auto-renewal** | Let's Encrypt (ACME/certbot), HashiCorp Vault PKI, AWS ACM |
| **Notifications** | Slack, Microsoft Teams, Email, Jira ticket creation |
| **Dashboard** | React + Recharts: security score, expiry timeline, risk distribution, per-cert drill-down |

## Architecture

```
Scheduler (APScheduler)
   -> Discovery (network scan | AWS ACM | Azure KV | GCP CM | K8s Secrets)
   -> Chain/Revocation Validator (cryptography, OCSP)
   -> Ownership Resolver (K8s annotations | Git CODEOWNERS | service metadata)
   -> Risk ML Engine (scikit-learn: risk tier + renewal-delay regression)
   -> Auto-Renewal Engine (Let's Encrypt | Vault | AWS ACM)
   -> AI Agent (LangChain, tool-calling over the platform's own services)
   -> Notifications (Slack | Teams | Email | Jira)
DB: PostgreSQL (or SQLite for local/demo use)
API: FastAPI, JWT auth
Frontend: React + Vite + Tailwind + Recharts
```

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, APScheduler
- **Security libs:** `cryptography`, `pyOpenSSL`, `certvalidator`
- **ML:** scikit-learn (GradientBoostingClassifier + GradientBoostingRegressor)
- **AI agent:** LangChain, OpenAI-compatible (OpenAI / Azure OpenAI / local Ollama)
- **Cloud SDKs:** boto3, azure-identity + azure-keyvault-certificates, google-cloud-certificate-manager, kubernetes
- **Auto-renewal:** certbot/ACME, `hvac` (Vault), boto3 (ACM)
- **DB:** PostgreSQL / SQLite
- **Frontend:** React 18, Vite, Tailwind CSS, Recharts, lucide-react
- **Deploy:** Docker, docker-compose, Kubernetes manifests

## Project layout

```
cert-platform/
├── backend/            FastAPI app, services, ML models, tests
├── frontend/            React dashboard
├── lab/                Self-signed CA + nginx test environment
├── k8s/                Kubernetes manifests
├── docker-compose.yml
├── .env.example
├── SETUP.md            <- step-by-step setup instructions
└── README.md           <- you are here
```

## Lab environment

`lab/` (openssl CA + nginx) is **currently out of sync**: `generate_certs.sh` creates `app1`-`app5` while the nginx
configs and `seed_lab_data.py` expect `legacy` and `api`. It is replaced by generated test fixtures in Phase 2.

## Using the AI agent

The agent works out of the box once you set `OPENAI_API_KEY` in
`backend/.env` (or point `OPENAI_BASE_URL` at a local model server). Example
questions:

- "Which certificates expire within 30 days?"
- "Why is api.lab.local failing validation?"
- "Show certificates issued by CertPlatform Lab expiring this quarter."
- "Recommend remediation for legacy.lab.local."

Without an API key, the rest of the platform (scanning, risk scoring,
dashboard, manual renewals) still works fully — only the natural-language
agent endpoint is disabled, and it says so clearly rather than failing.

## Retraining the risk model on real data

`backend/app/services/train_risk_model.py` currently trains on a synthetic
dataset that encodes the platform's own risk heuristics, so it's useful from
day one. Once you have real history (in the `scan_logs` / `renewal_tasks`
tables), swap `generate_synthetic_dataset()` for a query against those
tables and re-run the training script — the feature set (`FEATURE_ORDER` in
`risk_ml.py`) stays the same.
