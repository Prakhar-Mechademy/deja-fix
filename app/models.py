import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("id", "user_id", name="uq_sessions_id_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["sessions.id", "sessions.user_id"],
            name="fk_messages_session_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Solution(Base):
    __tablename__ = "solutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    problem: Mapped[str] = mapped_column(Text)
    symptoms: Mapped[str] = mapped_column(Text, default="[]")
    environment: Mapped[str] = mapped_column(Text, default="{}")
    fix_steps: Mapped[str] = mapped_column(Text, default="[]")
    what_did_not_work: Mapped[str] = mapped_column(Text, default="[]")
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    source_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FixAttempt(Base):
    """One proposed fix awaiting the customer's explicit outcome."""

    __tablename__ = "fix_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["sessions.id", "sessions.user_id"],
            name="fk_fix_attempts_session_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    trigger_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    response_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    confirmation_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    solution_id: Mapped[str | None] = mapped_column(
        ForeignKey("solutions.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(30), default="gemini_escalation")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SolutionSource(Base):
    """Records every confirmed use/session that contributed to a solution."""

    __tablename__ = "solution_sources"
    __table_args__ = (
        UniqueConstraint("fix_attempt_id", name="uq_solution_sources_fix_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solutions.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    fix_attempt_id: Mapped[str] = mapped_column(ForeignKey("fix_attempts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateEvent(Base):
    """Persisted per-user request counters, shared by Streamlit and HTTP API."""
    __tablename__ = "rate_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UserCredential(Base):
    """Hash only; the bearer token is never stored as plaintext."""
    __tablename__ = "user_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
