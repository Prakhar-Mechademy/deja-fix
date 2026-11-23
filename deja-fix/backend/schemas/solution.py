from pydantic import BaseModel
from typing import Any

class SolutionOut(BaseModel):
    id: str
    source_session_id: str
    symptoms: str
    environment: Any
    fix_steps: Any
    failed_attempts: Any
    verified: bool
    occurrence_count: int
    class Config: from_attributes = True
