import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.db.database import get_db
from app.db.models import LLMUsage


def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    configured_token = settings.ADMIN_TOKEN

    if not configured_token:
        raise HTTPException(status_code=401, detail="Admin access is not configured")

    if x_admin_token is None:
        raise HTTPException(status_code=401, detail="Admin token is required")

    if not secrets.compare_digest(x_admin_token, configured_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("/usage")
def get_usage(db: DBSession = Depends(get_db)):
    total_calls = db.query(func.count(LLMUsage.id)).scalar() or 0
    input_tokens = db.query(func.coalesce(func.sum(LLMUsage.input_tokens), 0)).scalar()
    output_tokens = db.query(func.coalesce(func.sum(LLMUsage.output_tokens), 0)).scalar()
    total_tokens = db.query(func.coalesce(func.sum(LLMUsage.total_tokens), 0)).scalar()

    by_purpose = (
        db.query(LLMUsage.purpose, func.count(LLMUsage.id), func.coalesce(func.sum(LLMUsage.total_tokens), 0))
        .group_by(LLMUsage.purpose)
        .all()
    )

    return {
        "call_count": total_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "by_purpose": {
            purpose: {"call_count": count, "total_tokens": tokens}
            for purpose, count, tokens in by_purpose
        },
    }
