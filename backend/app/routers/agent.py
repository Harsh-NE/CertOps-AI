from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas
from app.auth import get_current_user
from app.database import get_db
from app.services.ai_agent.agent import run_agent_query

router = APIRouter(prefix="/api/agent", tags=["ai agent"])


@router.post("/query", response_model=schemas.AgentQueryResponse)
def query_agent(payload: schemas.AgentQueryRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    result = run_agent_query(payload.question, db)
    return schemas.AgentQueryResponse(**result)
