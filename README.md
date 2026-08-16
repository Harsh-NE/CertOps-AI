# AI-Driven Certificate Lifecycle Management Platform

An agentic-AI platform for enterprise SSL/TLS certificate management:
**discovery → chain/revocation validation → ownership mapping → ML-based
risk prediction → AI agent (root-cause + Q&A) → auto-renewal → dashboard.**

Built for a security-automation / platform-engineering / SRE portfolio
project — but structured like a real production service, not a toy script.

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

## No enterprise certificates? Use the built-in lab

You don't need real infrastructure to try this out. `lab/` generates a
private Root CA → Intermediate CA → leaf certificate chain and serves five
test endpoints via nginx, each with a different risk profile (healthy,
expiring soon, expired, weak crypto, multi-SAN). See **SETUP.md → "Try it
without real infrastructure"**.

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

## Full setup instructions

See **[SETUP.md](./SETUP.md)** for step-by-step local, Docker, and lab-environment setup.
