import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.solution import Solution

router = APIRouter(prefix="/solutions", tags=["knowledge hub"])
@router.get("")
def list_solutions(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(Solution).filter(Solution.verified.is_(True)).order_by(Solution.last_confirmed_at.desc()).limit(min(limit, 500)).all()
    return [{"id": x.id, "source_session_id": x.source_session_id, "symptoms": x.symptoms,
             "environment": json.loads(x.environment or "{}"), "fix_steps": x.fix_steps,
             "failed_attempts": json.loads(x.failed_attempts or "[]"), "verified": x.verified,
             "occurrence_count": x.occurrence_count} for x in rows]

@router.get("/{solution_id}")
def get_solution(solution_id: str, db: Session = Depends(get_db)):
    x = db.query(Solution).filter(Solution.id == solution_id, Solution.verified.is_(True)).first()
    if not x: raise HTTPException(404, "Verified solution not found.")
    return {"id": x.id, "source_session_id": x.source_session_id, "symptoms": x.symptoms,
            "environment": json.loads(x.environment or "{}"), "fix_steps": x.fix_steps,
            "failed_attempts": json.loads(x.failed_attempts or "[]"), "verified": x.verified,
            "occurrence_count": x.occurrence_count}
