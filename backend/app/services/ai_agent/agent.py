"""
Agentic layer: a LangChain tool-calling agent bound to the platform's own
services (see tools.py). Handles natural-language questions like:
  "Which certificates expire within 30 days?"
  "Why is api.company.com failing validation?"
  "Show certificates issued by DigiCert expiring this quarter."

Works with OpenAI, Azure OpenAI, or any OpenAI-compatible endpoint (set
OPENAI_BASE_URL to point at a local Ollama/vLLM server for a fully offline
setup). If AI_AGENT_ENABLED is False (no API key configured), the router
returns a clear "agent not configured" message instead of failing.
"""
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.config import settings
from app.services.ai_agent.tools import build_tools

SYSTEM_PROMPT = """You are the AI operations agent for a Certificate Lifecycle \
Management Platform. You help engineers understand certificate risk, \
diagnose validation failures, and drive renewals. Always use your tools to \
look up real data rather than guessing - never invent certificate names, \
dates, or statuses. Be concise and actionable. When you recommend an action \
that changes state (like creating a renewal ticket), confirm what you did.
"""


def run_agent_query(question: str, db: Session) -> Dict[str, Any]:
    if not settings.AI_AGENT_ENABLED:
        return {
            "answer": (
                "The AI agent isn't configured yet. Set OPENAI_API_KEY (and optionally "
                "OPENAI_BASE_URL / AGENT_MODEL) in your .env to enable natural-language "
                "queries and root-cause analysis. Structured endpoints (/api/certificates, "
                "/api/risk, /api/renewals) work without it."
            ),
            "used_tools": [],
        }

    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=settings.AGENT_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        temperature=0,
    )
    tools = build_tools(db)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=False, max_iterations=6)

    result = executor.invoke({"input": question})
    used_tools = [
        step[0].tool for step in result.get("intermediate_steps", [])
    ] if "intermediate_steps" in result else []

    return {"answer": result.get("output", ""), "used_tools": used_tools}
