"""SQLAlchemy database models."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_string() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(back_populates="session")
    solution_sources: Mapped[list["SolutionSource"]] = relationship(back_populates="session")
    llm_usage: Mapped[list["LLMUsage"]] = relationship(back_populates="session")

    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed')", name="ck_sessions_status"),
        Index("ix_sessions_user_created", "user_id", "created_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped["Session"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_messages_role"),
        Index("ix_messages_session_created", "session_id", "created_at"),
        Index("ix_messages_user_session", "user_id", "session_id"),
    )


class Solution(Base):
    __tablename__ = "solutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_title: Mapped[str | None] = mapped_column(String(255))
    problem_description: Mapped[str | None] = mapped_column(Text)
    symptoms: Mapped[list | None] = mapped_column(JSON)
    device: Mapped[str | None] = mapped_column(String(255))
    operating_system: Mapped[str | None] = mapped_column(String(255))
    application_name: Mapped[str | None] = mapped_column(String(255))
    app_version: Mapped[str | None] = mapped_column(String(255))
    fix_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    failed_attempts: Mapped[list | None] = mapped_column(JSON)
    error_codes: Mapped[list | None] = mapped_column(JSON)
    embedding: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    sources: Mapped[list["SolutionSource"]] = relationship(back_populates="solution")

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    @property
    def confirmation_count(self) -> int:
        return len(self.sources)

    __table_args__ = (
        CheckConstraint("status IN ('UNVERIFIED', 'VERIFIED')", name="ck_solutions_status"),
        Index("ix_solutions_status", "status"),
    )


class SolutionSource(Base):
    __tablename__ = "solution_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    solution_id: Mapped[str] = mapped_column(String(36), ForeignKey("solutions.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    confirmation_message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    solution: Mapped["Solution"] = relationship(back_populates="sources")
    session: Mapped["Session"] = relationship(back_populates="solution_sources")

    __table_args__ = (
        UniqueConstraint("solution_id", "session_id", name="uq_solution_source"),
        Index("ix_solution_sources_solution", "solution_id"),
        Index("ix_solution_sources_session", "session_id"),
    )


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_string)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped["Session"] = relationship(back_populates="llm_usage")

    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="ck_llm_usage_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_llm_usage_output_tokens"),
        CheckConstraint("total_tokens >= 0", name="ck_llm_usage_total_tokens"),
        Index("ix_llm_usage_session_created", "session_id", "created_at"),
    )
