# CertOps AI: certificate lifecycle management platform

An agentic-AI platform for enterprise SSL/TLS certificate management:
**discovery → chain/revocation validation → ownership mapping → ML-based
risk prediction → AI agent (root-cause + Q&A) → auto-renewal → dashboard.**

Built for a security-automation / platform-engineering / SRE portfolio
project — but structured like a real production service, not a toy script.

> **Project status (honest):** this is a work in progress, being completed in phases. The table
> below describes the *target*; see **Current status** for what is verified today.

## Current status

| Area | Status | How it is tested |
|---|---|---|
| Network TLS discovery (presented chain, SNI) | **Real** | Live local TLS servers |
| Validation: expiry, hostname, key size/algorithm, weak signature, self-signed, trust path, revocation (OCSP) | **Real** | 33 unit tests + lab scenarios; OCSP against a local responder |
| AWS ACM | **Real code, tested against moto** | moto mock; never run against a real AWS account |
| Kubernetes `kubernetes.io/tls` Secrets | **Real**, verified against a live cluster (Docker Desktop Kubernetes v1.36.1) | `KUBE_INTEGRATION=1 pytest tests/integration`; fake-client unit tests cover the rest |
| cert-manager `Certificate` status | **Simulated** | Fixtures only; cert-manager was not installed in the test cluster |
| Azure Key Vault, GCP Certificate Manager | **Simulated** | Fake clients shaped like the SDK responses; never run against Azure/GCP |
| Scheduled monitoring | **Real** | Retry/backoff and persisted run state (`job_runs`) are unit-tested; scheduler wiring itself is not |
| Risk model (scikit-learn gradient boosting) | **Real model, trained and evaluated on SYNTHETIC data only**; no real-world accuracy claim | `python -m app.ml.evaluate` |
| LangChain agent (Gemini) | **Real agent graph, guardrails and audit; live Gemini path NOT yet run** (no API key available during development) | 28-case eval; `python -m app.agent.eval` |
| Frontend (React) | **Working; verified manually in a browser** (login, role-based UI, dashboard charts, filtered inventory, plan -> approve -> real Pebble renewal, history, sources). Automated component tests: Phase 6 | `npm run build`; manual walkthrough |
| Renewal: ACME + Vault | **Real**, verified against a local Pebble CA and dev-mode Vault; human approval required; Let's Encrypt staging/production NOT exercised; deploying the new cert is not automated | `RENEWAL_INTEGRATION=1 pytest tests/integration` |
| Renewal: AWS ACM | **Not executable** by design (ACM cannot be force-renewed); plans say so | unit tests |

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

## Risk model results (synthetic data)

> **Synthetic data.** There is no labelled real-world incident data. These numbers show how well each model recovers
> the simulator's structure, not real-world accuracy. Details and limitations: [docs/risk_model.md](docs/risk_model.md).

Label: certificate causes a service problem within 30 days. Team-grouped split; thresholds chosen on validation; test set shown.
Reproduce: `cd backend && python -m app.ml.evaluate --docs ../docs/results` (seed 7, n=20000).

| model | precision | recall | f1 | roc_auc | pr_auc | brier |
|---|---|---|---|---|---|---|
| baseline_days_to_expiry | 0.580 | 0.438 | 0.499 | 0.694 | 0.440 | n/a |
| logistic_regression | 0.189 | 0.313 | 0.235 | 0.749 | 0.162 | 0.052 |
| gradient_boosting | 0.596 | 0.438 | 0.505 | 0.863 | 0.568 | 0.035 |

Gradient boosting minus baseline, bootstrap 95% CI: ROC-AUC +0.127 .. +0.214, PR-AUC +0.091 .. +0.165.
At the F1-optimal threshold the model is essentially tied with the days-to-expiry rule; the gain is in ranking.

## Agent evaluation

> **Read this before quoting any agent number.** The scripted result below is evidence of *tested workflow behavior*
> (tool layer, human-approval gate, role restrictions, allow-list, audit), **not real-model accuracy**. A keyword
> stand-in chose the tools, so it says nothing about how well Gemini selects tools or answers. Live-model results will
> be reported in their own row and never merged with the scripted one. As of now, **no live Gemini evaluation has been run.**

Reproduce: `cd backend && python -m app.agent.eval --docs ../docs/results`

| backend | cases counted | passed | success rate |
|---|---|---|---|
| scripted (keyword stand-in) | 27 of 28 | 27 | 100.0% |
| live Gemini | not run | not run | not measured |

The eval can fail: `tests/test_agent.py` removes the approval gate, breaks role checks and corrupts search results,
and asserts the eval then reports failures.

## Renewal lab

`docker compose -f docker-compose.renewal-lab.yml up -d` starts Pebble (a real ACME server), its challenge mock and
Vault in dev mode (test only). Then `RENEWAL_INTEGRATION=1 pytest tests/integration/test_acme_pebble.py`.

## Local certificate lab

`python -m app.devtools.lab` (from `backend/`) generates a private PKI and serves 11 TLS endpoints on
`127.0.0.1:9441-9451`, each with a different problem, plus a local OCSP responder. No Docker, hosts-file edits or
openssl are needed. The expected result for each is asserted by `tests/test_lab.py`.

| Endpoint (SNI) | Problem | Expected status |
|---|---|---|
| healthy.lab.test | none | valid |
| expiring.lab.test | expires in 3 days | expiring_soon |
| expired.lab.test | expired 10 days ago | expired |
| wrong-host.lab.test | certificate is for another name | hostname_mismatch |
| selfsigned.lab.test | self-signed | invalid_chain |
| nochain.lab.test | server omits the intermediate | invalid_chain |
| legacy.lab.test | RSA-1024 key | weak_crypto |
| rogue.lab.test | issued by an untrusted CA | invalid_chain |
| app.wild.lab.test | wildcard cert | valid |
| ocsp-good.lab.test / ocsp-revoked.lab.test | OCSP says good / revoked | valid / revoked |

```bash
# terminal 1: serve the lab and register the endpoints as scan targets
cd backend && python -m app.devtools.lab
# terminal 2: run the API trusting the lab CA, then POST /api/cloud/discover {"providers": ["network_scan"]}
ALLOW_PRIVATE_TARGETS=true TRUST_STORE_EXTRA_PEM=../lab/certs/ca.pem uvicorn app.main:app
```
For the Docker stack: run the lab with `--bind 0.0.0.0 --advertise-host host.docker.internal --out ../lab/certs
--no-register`, then `docker compose -f docker-compose.yml -f docker-compose.lab.yml up --build`.
`ALLOW_PRIVATE_TARGETS` turns off the SSRF guard (loopback/private addresses are refused by default); use it only in a lab.

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
