from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Boolean, Float
from database import Base

class Solution(Base):
    __tablename__ = "solutions"
    id = Column(String(36), primary_key=True)
    source_session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, index=True)
    problem_fingerprint = Column(String(64), nullable=True, index=True)
    symptoms = Column(Text, nullable=False)
    environment = Column(Text, nullable=False, default="{}")
    fix_steps = Column(Text, nullable=False)
    failed_attempts = Column(Text, nullable=False, default="[]")
    verified = Column(Boolean, nullable=False, default=False, index=True)
    occurrence_count = Column(Integer, nullable=False, default=1)
    embedding = Column(Text, nullable=True)
    last_confirmed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
