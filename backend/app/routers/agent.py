import json
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.agent import llm
from app.agent.tools import READ_ONLY_TOOLS
from app.auth import get_current_user, require_role
from app.database import get_db
from app.renewal.service import Actor

router = APIRouter(prefix="/api/agent", tags=["ai agent"])


@router.post("/query", response_model=schemas.AgentQueryResponse)
def query_agent(payload: schemas.AgentQueryRequest, user=Depends(get_current_user)):
    """
    Natural-language question. The agent acts with the *caller's* privileges at most: viewers get read-only tools;
    renewals still need a human to approve the plan.
    """
    actor = Actor(kind="agent", name=user.username, role=user.role, on_behalf_of=user.username)
    allowed = None if user.role in ("operator", "admin") else READ_ONLY_TOOLS
    model = llm.get_chat_model()
    if model is None:  # no key: answer immediately, without loading LangChain
        return schemas.AgentQueryResponse(
            answer=("The AI agent is not configured: set GEMINI_API_KEY in .env to enable natural-language queries. "
                    "The structured API endpoints work without it."), configured=False)
    from app.agent.agent import run_agent_query  # imported lazily: LangChain is slow to load

    result = run_agent_query(payload.question, actor, model=model, allowed_tools=allowed)
    result.pop("tool_results", None)  # full payloads stay server-side; the audit table keeps summaries
    return schemas.AgentQueryResponse(**result)


@router.get("/tool-calls", response_model=List[schemas.AgentToolCallOut])
def tool_call_audit(limit: int = 100, db: Session = Depends(get_db), user=Depends(require_role("operator"))):
    """Audit trail of everything the agent attempted, including denied calls."""
    rows = db.query(models.AgentToolCall).order_by(models.AgentToolCall.created_at.desc()).limit(min(limit, 500)).all()
    return [schemas.AgentToolCallOut(id=r.id, request_id=r.request_id, actor=r.actor, tool=r.tool,
                                     args=json.loads(r.args) if r.args else None, status=r.status,
                                     duration_ms=r.duration_ms, created_at=r.created_at) for r in rows]
