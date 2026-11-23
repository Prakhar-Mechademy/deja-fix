"""User API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.models import User
from app.schemas.chat import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_200_OK)
def create_or_get_user(payload: UserCreate, db: DBSession = Depends(get_db)):
    user = db.query(User).filter(User.external_id == payload.external_id).first()

    if user:
        return user

    user = User(external_id=payload.external_id)
    db.add(user)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not create customer")

    db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: str, db: DBSession = Depends(get_db)):
    user = db.get(User, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Customer is inactive")

    return user
