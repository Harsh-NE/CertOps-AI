"""
Agent evaluation.

    cd backend
    python -m app.agent.eval                                    # backend=scripted (offline, deterministic)
    python -m app.agent.eval --backend live --record cassette.json --sleep 6   # real Gemini (needs GEMINI_API_KEY)
    python -m app.agent.eval --backend replay --cassette cassette.json         # replay recorded tool calls offline
    add --docs ../docs/results to write agent_eval.json / agent_eval.md

WHAT EACH BACKEND'S NUMBER MEANS
  scripted : a keyword stand-in chooses the tools. The rate measures the harness, tool layer, guardrails (approval
             gate, role restrictions, allow-list) and answer plumbing. It says NOTHING about Gemini's tool choice.
             Cases tagged tests_model_behaviour are excluded from this denominator.
  replay   : tool calls recorded from a real model run, replayed offline; the same checks then verify what that
             model actually chose.
  live     : the real model, end to end. This is the only number that measures model quality.

Each case runs on a fresh database seeded by eval_fixtures.build_fixture and a fake renewal executor that counts
executions, so "nothing was renewed" is verified, not assumed.
"""
import argparse
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.agent.agent import run_agent_query
from app.agent.eval_fixtures import build_fixture
from app.agent.testing import KeywordPolicyModel, ReplayModel
from app.agent.tools import READ_ONLY_TOOLS
from app.database import Base
from app.renewal.service import Actor

EVAL_SET = Path(__file__).with_name("eval_set.json")
FIXTURE_PLANS = 2  # plans created by the fixture itself


def dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def parse_results(trace_results: List[str]) -> List[Dict[str, Any]]:
    out = []
    for r in trace_results:
        try:
            out.append(json.loads(r))
        except ValueError:
            out.append({"raw": r})
    return out


def check_one(check: Dict[str, Any], results: List[Dict[str, Any]]) -> str | None:
    """Returns a failure message, or None if the check passes."""
    i = check["i"]
    if i >= len(results):
        return f"no result #{i}"
    res = results[i]
    if "names_exact" in check or "names_include" in check:
        names = {c["common_name"] for c in res.get("certificates", [])}
        if "names_exact" in check and names != set(check["names_exact"]):
            return f"names {sorted(names)} != {sorted(check['names_exact'])}"
        if "names_include" in check and not set(check["names_include"]) <= names:
            return f"names {sorted(names)} missing {sorted(set(check['names_include']) - names)}"
        return None
    value = dig(res, check["path"])
    if "eq" in check and value != check["eq"]:
        return f"{check['path']}={value!r}, expected {check['eq']!r}"
    if "contains" in check:
        hay = value if isinstance(value, str) else json.dumps(value)
        if check["contains"] not in (hay or ""):
            return f"{check['path']} does not contain {check['contains']!r}"
    if "len" in check and (not isinstance(value, list) or len(value) != check["len"]):
        return f"len({check['path']}) != {check['len']}"
    if "len_min" in check and (not isinstance(value, list) or len(value) < check["len_min"]):
        return f"len({check['path']}) < {check['len_min']}"
    return None


def abstract_steps(trace: List[Dict[str, Any]], results: List[Dict[str, Any]], fx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Make recorded tool calls portable: concrete ids become references so the cassette replays on any database."""
    tokens = {fx["plan_pending"]: "{plan_pending}", fx["plan_approved"]: "{plan_approved}"}
    steps = []
    for i, t in enumerate(trace):
        args = {}
        for k, v in (t["args"] or {}).items():
            ref = tokens.get(v) if isinstance(v, str) else None
            if ref is None and isinstance(v, str):
                for j in range(i):
                    hit = next((key for key, val in (results[j].items() if j < len(results) else []) if val == v), None)
                    if hit:
                        ref = f"{{r{j}.{hit}}}"
                        break
            args[k] = ref or v
        steps.append({"name": t["tool"], "args": args})
    return steps


def make_model(backend: str, case: Dict[str, Any], cassette: Dict[str, Any], fx: Dict[str, Any], question: str):
    if backend == "scripted":
        return KeywordPolicyModel()
    if backend == "replay":
        rec = cassette.get(case["question"], {"steps": []})  # keyed by the question TEMPLATE
        fill = {"{plan_pending}": fx["plan_pending"], "{plan_approved}": fx["plan_approved"]}
        steps = [{"name": s["name"], "args": {k: fill.get(v, v) if isinstance(v, str) else v for k, v in s["args"].items()}}
                 for s in rec["steps"]]
        return ReplayModel({question: steps}, {question: rec.get("final", "")})
    from app.agent.llm import get_chat_model
    model = get_chat_model()
    if model is None:
        raise SystemExit("backend=live needs GEMINI_API_KEY in .env")
    return model


def run_case(case: Dict[str, Any], backend: str, cassette: Dict[str, Any]) -> Dict[str, Any]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    db = factory()
    fx = build_fixture(db)
    db.close()
    question = case["question"].format(plan_pending=fx["plan_pending"], plan_approved=fx["plan_approved"])
    role = case.get("role", "operator")
    actor = Actor("agent", "eval-user", role=role, on_behalf_of="eval-user")
    allowed = None if role in ("operator", "admin") else READ_ONLY_TOOLS
    out = run_agent_query(question, actor, model=make_model(backend, case, cassette, fx, question), session_factory=factory,
                          executors=fx["executors"], allowed_tools=allowed)
    s = factory()
    plans_created = s.query(models.RenewalPlan).count() - FIXTURE_PLANS
    s.close()
    results = parse_results(out.get("tool_results", []))
    ex = case["expect"]
    failures: List[str] = []
    tools = [t["tool"] for t in out["tool_calls"]]
    if tools != ex["tools"]:
        failures.append(f"tools {tools} != expected {ex['tools']}")
    if "statuses" in ex and [t["status"] for t in out["tool_calls"]] != ex["statuses"]:
        failures.append(f"statuses {[t['status'] for t in out['tool_calls']]} != {ex['statuses']}")
    for chk in ex.get("checks", []):
        msg = check_one(chk, results)
        if msg:
            failures.append(msg)
    for needle in ex.get("answer_contains", []):
        if needle.lower() not in out["answer"].lower():
            failures.append(f"answer missing {needle!r}")
    for t in ex.get("forbid_tools", []):
        if t in tools:
            failures.append(f"forbidden tool {t} was called")
    if "executions" in ex and fx["executors"]["letsencrypt"].exec_calls != ex["executions"]:
        failures.append(f"renewals executed={fx['executors']['letsencrypt'].exec_calls}, expected {ex['executions']}")
    if "plans_created" in ex and plans_created != ex["plans_created"]:
        failures.append(f"plans created={plans_created}, expected {ex['plans_created']}")
    if out.get("error"):
        failures.append(f"agent error: {out['error']}")
    return {"id": case["id"], "category": case["category"], "passed": not failures, "failures": failures,
            "tools": tools, "question": question, "answer": out["answer"][:300],
            "steps": abstract_steps(out["tool_calls"], results, fx)}


def run_eval(backend: str = "scripted", cassette_path: str | None = None, record: str | None = None,
             sleep: float = 0.0, only: List[str] | None = None) -> Dict[str, Any]:
    spec = json.loads(EVAL_SET.read_text())
    cassette = json.loads(Path(cassette_path).read_text()) if cassette_path else {}
    results, recorded = [], {}
    for case in spec["cases"]:
        if only and case["id"] not in only:
            continue
        r = run_case(case, backend, cassette)
        r["counted"] = not (backend == "scripted" and case.get("tests_model_behaviour"))
        results.append(r)
        recorded[case["question"]] = {"steps": r["steps"], "final": r["answer"]}
        if sleep:
            time.sleep(sleep)
    if record:
        Path(record).write_text(json.dumps(recorded, indent=1))
    counted = [r for r in results if r["counted"]]
    by_cat: Dict[str, List[bool]] = defaultdict(list)
    for r in counted:
        by_cat[r["category"]].append(r["passed"])
    return {
        "backend": backend,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": None if backend == "scripted" else ("recorded" if backend == "replay" else "gemini (see GEMINI_MODEL)"),
        "cases_total": len(results), "cases_counted": len(counted),
        "cases_excluded": [r["id"] for r in results if not r["counted"]],
        "passed": sum(r["passed"] for r in counted),
        "success_rate": round(sum(r["passed"] for r in counted) / max(1, len(counted)), 3),
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in by_cat.items()},
        "failures": [{"id": r["id"], "why": r["failures"]} for r in counted if not r["passed"]],
        "results": results,
    }


MEANING = {
    "scripted": "Offline run with a KEYWORD STAND-IN model. This measures the tool layer, guardrails and answer plumbing; "
                "it does NOT measure Gemini's tool selection or language quality.",
    "replay": "Offline replay of tool calls recorded from a real model; measures what that model actually chose.",
    "live": "Real model, end to end. The only number here that reflects model quality.",
}


def to_markdown(r: Dict[str, Any]) -> str:
    lines = [f"> {MEANING[r['backend']]}", "",
             f"Backend: **{r['backend']}**; cases counted {r['cases_counted']}/{r['cases_total']}; "
             f"passed {r['passed']}; success rate **{r['success_rate']:.1%}**.", ""]
    if r["cases_excluded"]:
        lines.append(f"Excluded from this denominator (need a real or recorded model to mean anything): {', '.join(r['cases_excluded'])}.\n")
    lines += ["| category | passed |", "|---|---|"] + [f"| {k} | {v} |" for k, v in r["by_category"].items()]
    if r["failures"]:
        lines += ["", "Failures:"] + [f"- {f['id']}: {'; '.join(f['why'])}" for f in r["failures"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["scripted", "replay", "live"], default="scripted")
    ap.add_argument("--cassette")
    ap.add_argument("--record")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between live cases (free-tier rate limits)")
    ap.add_argument("--docs", default=None)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    r = run_eval(a.backend, a.cassette, a.record, a.sleep, a.only)
    print(to_markdown(r))
    if a.docs:
        d = Path(a.docs)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"agent_eval_{a.backend}.json").write_text(json.dumps(r, indent=1))
        (d / f"agent_eval_{a.backend}.md").write_text(to_markdown(r))


if __name__ == "__main__":
    main()
