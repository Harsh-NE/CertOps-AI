# Agent and renewal design

## Components

* **LangChain 1.x agent** (`create_agent`, LangGraph runtime) with Gemini (`langchain-google-genai`) as the live model.
* **Tools** (`backend/app/agent/tools.py`), all grounded in the platform database:

| tool | kind | what it does |
|---|---|---|
| `search_certificates` | read | filter by text, status, environment, risk tier, source, expiry window |
| `get_certificate` | read | full details of one certificate (id / name / hostname; ambiguity is reported, never guessed) |
| `explain_risk` | read | risk probability, tier and top factors (model is trained on synthetic data, and the tool says so) |
| `diagnose_certificate` | read | root-cause analysis: validation findings + renewal history + job errors mapped to documented causes and fixes |
| `get_renewal_history` | read | recent attempts including failures |
| `list_renewal_plans` | read | plans and their approval status |
| `run_validation` | action | re-run validation from stored data (offline) |
| `plan_renewal` | action | **dry run**; creates a plan a human must approve |
| `execute_renewal` | write | executes a plan **only if a human approved it** |

There is no approve tool. A signed-in operator approves in the UI/API.

## Guardrails (all enforced in code, none left to the prompt)

* **Allow-list**: unknown tool names are denied and audited; viewers get read-only tools only.
* **Human confirmation by a different actor**: `RenewalService.approve` accepts only a signed-in user with the
  operator/admin role. Execution needs an APPROVED, unexpired (15 min), never-used plan whose hash still matches the
  certificate; APPROVED -> EXECUTING is a compare-and-swap so a plan runs at most once, even concurrently.
* **Budgets and timeouts**: max 8 tool calls per request, 15 s per read tool (180 s for `execute_renewal`).
* **Untrusted data**: certificate fields (CN, issuer, SANs, notes) can carry injected text. Tool output is sanitised,
  length-capped and the system prompt marks it as data. Even a model that obeys injected text cannot approve or
  bypass the gate (tests simulate such a model).
* **Audit**: every attempt, including denied and malformed calls, is written to `agent_tool_calls` and logged as JSON.

## Renewal

`plan` -> (human) `approve` -> `execute`. ACME (Let's Encrypt staging by default, or a local Pebble) with HTTP-01.
The issued private key and chain go to Vault KV and nowhere else (not the database, API responses or logs).
Issuance is refused if Vault is unavailable, the production CA is used without `ACME_ALLOW_PRODUCTION=true`, or the
certificate has wildcard names (needs DNS-01, not implemented). **Deploying the new certificate to the endpoint is not
automated.** AWS ACM cannot be force-renewed; its plans are explicitly non-executable.

## Evaluation: what the numbers mean

`python -m app.agent.eval` runs 28 cases (inventory queries, details, root cause, renewal safety, guardrails) on a fresh
seeded database with a renewal executor that counts executions, so "nothing was renewed" is verified.

* **scripted**: a keyword stand-in chooses the tools. It measures the tool layer, guardrails and answer plumbing,
  **not** Gemini. Cases that only mean something with a real model (prompt injection reading) are excluded from its
  denominator.
* **replay**: replays tool calls recorded from a real run. **live**: the real model; the only number that measures
  model quality. Both need `GEMINI_API_KEY` once to record.
* The harness is itself tested: deliberately removing the approval gate, breaking role restrictions or corrupting
  search results makes the eval fail (`tests/test_agent.py`).

## Limitations

* The live Gemini path has not been run by me (no key available in this environment).
* One-shot questions only; no conversation memory.
* The whole-request timeout is enforced through the model timeout and tool timeouts, not a hard wall clock.
* Real Let's Encrypt (staging or production) was not exercised; Pebble was.
