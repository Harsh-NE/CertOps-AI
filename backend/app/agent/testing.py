"""
Deterministic stand-ins for the LLM, used by tests and the offline eval. They implement LangChain's chat-model
interface so the REAL agent graph, middleware, tools and guardrails run unchanged.

IMPORTANT: these are not language models. `KeywordPolicyModel` picks tools with keyword rules I wrote, so an eval run
with it measures the tool layer, guardrails and answer plumbing, NOT how well Gemini would choose tools.
`ScriptedModel` replays a fixed list of tool calls (used to simulate a compromised or misbehaving model).
`ReplayModel` replays tool calls recorded from a real model run (see eval.py --record).
"""
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

Step = Dict[str, Any]  # {"name": str, "args": dict}  (args values may be "{prev.plan_id}" placeholders)


class PolicyChatModel(BaseChatModel):
    """Chat model whose replies come from `policy(messages) -> AIMessage`."""

    policy: Callable[[List[BaseMessage]], AIMessage]

    @property
    def _llm_type(self) -> str:
        return "policy-stub"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):  # create_agent calls this; tools are ignored
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.policy(messages))])


def _call(name: str, args: Dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])


def _since_last_human(messages: List[BaseMessage]) -> List[BaseMessage]:
    idx = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return messages[idx + 1:]


def _tool_results(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    out = []
    for m in _since_last_human(messages):
        if isinstance(m, ToolMessage):
            try:
                out.append(json.loads(m.content))
            except (TypeError, ValueError):
                out.append({"raw": str(m.content)})
    return out


_REF = re.compile(r"^\{(prev|r(\d+))\.(\w+)\}$")


def _fill(args: Dict[str, Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve '{prev.field}' / '{rN.field}' placeholders from earlier tool results."""
    def resolve(v):
        m = _REF.match(v) if isinstance(v, str) else None
        if not m:
            return v
        idx = len(results) - 1 if m.group(1) == "prev" else int(m.group(2))
        return results[idx].get(m.group(3)) if 0 <= idx < len(results) else None
    return {k: resolve(v) for k, v in args.items()}


def compose_answer(results: List[Dict[str, Any]]) -> str:
    """Plain-text answer built only from tool results (what a faithful model would say)."""
    if not results:
        return "I could not find an answer with the available tools."
    parts = []
    for r in results:
        if "error" in r:
            parts.append(f"{r.get('error')}: {r.get('message', '')}".strip())
            if r.get("candidates"):
                parts.append("Candidates: " + ", ".join(c["common_name"] for c in r["candidates"]))
        elif "certificates" in r:
            names = ", ".join(c["common_name"] for c in r["certificates"]) or "none"
            parts.append(f"{r['total_matches']} matching certificate(s): {names}")
        elif "plan_id" in r:
            parts.append(f"Plan {r['plan_id']} is {r['status']}; executable={r['executable']}. "
                         + " ".join(r.get("steps", [])[:2]) + (f" Not executable: {r['not_executable_reason']}"
                                                             if r.get("not_executable_reason") else ""))
        elif r.get("executed"):
            parts.append(f"Renewal executed: {r['status']} (new serial {r.get('new_serial')}).")
        elif "evidence" in r:
            parts.append(f"{r['certificate']} [{r['status']}]: " + "; ".join(e["fact"] for e in r["evidence"][:4])
                         + (" Likely cause: " + r["likely_causes"][0]["cause"] if r["likely_causes"] else ""))
        elif "findings" in r:
            parts.append(f"{r['certificate']}: {r['overall']}; " + "; ".join(f["message"] for f in r["findings"]))
        elif "probability_of_problem_30d" in r:
            parts.append(f"{r['certificate']}: {r['tier']} risk, p={r['probability_of_problem_30d']} "
                         f"({r['data_source']} model). {r.get('floor_reason') or ''}".strip())
        elif "history" in r:
            parts.append(f"{r['certificate']}: " + "; ".join(f"{h['status']} via {h['provider']} {h['notes'][:80]}"
                                                            for h in r["history"][:3]))
        elif "plans" in r:
            parts.append("Plans: " + ", ".join(f"{p['plan_id']} ({p['status']})" for p in r["plans"]))
        elif "common_name" in r:
            parts.append(f"{r['common_name']}: status {r['status']}, expires in {r['days_to_expiry']} days, "
                         f"env {r['environment']}, issuer {r.get('issuer')}")
        else:
            parts.append(json.dumps(r)[:200])
    return " | ".join(parts)


# ---- keyword policy: question -> planned tool steps -------------------------------------------------------

_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def plan_steps(question: str) -> List[Step]:
    q = question.lower()
    hosts = [h for h in _HOST.findall(question) if not h.lower().endswith((".", "e.g"))]
    host = hosts[0] if hosts else None
    plan_id = (_UUID.findall(question) or [None])[0]
    days = re.search(r"(\d+)\s*days?", q)
    env = next((e for e in ("prod", "staging", "dev") if re.search(rf"\b{e}\b", q)), None)

    if plan_id and re.search(r"execut|run plan|go ahead", q):
        return [{"name": "execute_renewal", "args": {"plan_id": plan_id}}]
    if re.search(r"list.*plans|pending plans|approval status", q):
        return [{"name": "list_renewal_plans", "args": {}}]
    if host and re.search(r"renew", q) and re.search(r"execut|now|go ahead|do it", q) and not plan_id:
        return [{"name": "plan_renewal", "args": {"certificate": host}},
                {"name": "execute_renewal", "args": {"plan_id": "{prev.plan_id}"}}]
    if host and re.search(r"plan.*renew|dry.?run|renew", q) and "history" not in q and "fail" not in q:
        return [{"name": "plan_renewal", "args": {"certificate": host}}]
    if host and re.search(r"renewal history|renewals? (of|for)|past renewals", q):
        return [{"name": "get_renewal_history", "args": {"certificate": host}}]
    if host and re.search(r"why|root cause|diagnos|problem|wrong|failing|fail", q):
        return [{"name": "diagnose_certificate", "args": {"certificate": host}}]
    if host and re.search(r"re-?run validation|validate|revalidate", q):
        return [{"name": "run_validation", "args": {"certificate": host}}]
    if host and re.search(r"risk|risky", q):
        return [{"name": "explain_risk", "args": {"certificate": host}}]
    if host and re.search(r"detail|show|tell me about|info", q):
        return [{"name": "get_certificate", "args": {"certificate": host}}]
    if re.search(r"weak", q):
        return [{"name": "search_certificates", "args": {"status": "weak_crypto"}}]
    if re.search(r"critical|high risk|high or critical", q):
        return [{"name": "search_certificates", "args": {"risk_tier": "critical"}},
                {"name": "search_certificates", "args": {"risk_tier": "high"}}]
    if re.search(r"let'?s encrypt|issuer|issued by", q):
        return [{"name": "search_certificates", "args": {"query": "Let's Encrypt"}}]
    if re.search(r"\bexpired\b", q) and not days:
        return [{"name": "search_certificates", "args": {"status": "expired"}}]
    if days and re.search(r"expir", q):
        args = {"expires_within_days": int(days.group(1))}
        if env:
            args["environment"] = env
        return [{"name": "search_certificates", "args": args}]
    if env:
        return [{"name": "search_certificates", "args": {"environment": env}}]
    if host:
        return [{"name": "get_certificate", "args": {"certificate": host}}]
    return []


def keyword_policy(messages: List[BaseMessage]) -> AIMessage:
    question = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    steps = plan_steps(str(question))
    results = _tool_results(messages)
    if len(results) < len(steps):
        step = steps[len(results)]
        # stop early if a previous step failed and this one depends on it
        if any(isinstance(v, str) and v.startswith("{prev.") for v in step["args"].values()) and results and "error" in results[-1]:
            return AIMessage(content=compose_answer(results))
        return _call(step["name"], _fill(step["args"], results))
    return AIMessage(content=compose_answer(results))


def KeywordPolicyModel() -> PolicyChatModel:  # noqa: N802 (factory that reads like a class)
    return PolicyChatModel(policy=keyword_policy)


def ScriptedModel(steps: List[Step], final: Optional[str] = None) -> PolicyChatModel:  # noqa: N802
    """Issues exactly `steps` as tool calls, in order, regardless of the question or results (a misbehaving model)."""

    def policy(messages: List[BaseMessage]) -> AIMessage:
        results = _tool_results(messages)
        if len(results) < len(steps):
            s = steps[len(results)]
            return _call(s["name"], _fill(s["args"], results))
        return AIMessage(content=final if final is not None else compose_answer(results))

    return PolicyChatModel(policy=policy)


def ReplayModel(recorded: Dict[str, List[Step]], final_by_question: Optional[Dict[str, str]] = None) -> PolicyChatModel:  # noqa: N802
    """Replays tool calls recorded from a real model (question -> steps). Unknown questions get no tool calls."""

    def policy(messages: List[BaseMessage]) -> AIMessage:
        question = str(next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""))
        steps = recorded.get(question, [])
        results = _tool_results(messages)
        if len(results) < len(steps):
            s = steps[len(results)]
            return _call(s["name"], _fill(s["args"], results))
        return AIMessage(content=(final_by_question or {}).get(question) or compose_answer(results))

    return PolicyChatModel(policy=policy)
