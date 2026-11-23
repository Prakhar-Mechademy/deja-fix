from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
from database import get_db
from models.user import User
from schemas.user import UserCreate, UserOut
from services.memory_service import latest_user_problem

router = APIRouter(prefix="/users", tags=["users"])

@router.post("", response_model=UserOut)
def create_or_get_user(payload: UserCreate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.external_id == payload.external_id).first()
    if user:
        if payload.display_name: user.display_name = payload.display_name
        db.commit(); db.refresh(user)
        return user
    user = User(id=str(uuid.uuid4()), external_id=payload.external_id, display_name=payload.display_name)
    db.add(user); db.commit(); db.refresh(user)
    return user

@router.get("/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="Customer not found.")
    last_problem = latest_user_problem(db, user_id)
    greeting = f"Welcome back. Last time you contacted support about: {last_problem[:160]}" if last_problem else "Welcome to Déjà Fix. How can I help?"
    return {"id": user.id, "external_id": user.external_id, "display_name": user.display_name, "greeting": greeting}
