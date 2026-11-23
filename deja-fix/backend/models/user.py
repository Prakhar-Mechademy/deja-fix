from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)
    external_id = Column(String(128), unique=True, nullable=False, index=True)
    display_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
