from pydantic import BaseModel, Field
from typing import Optional, Literal

class ChatMessageRequest(BaseModel):
    user_id: str
    session_id: str
    message: str = Field(min_length=1, max_length=8000)

class ChatMessageResponse(BaseModel):
    session_id: str
    reply: str
    source: Literal["knowledge_hub", "llm", "clarification", "system"]
    solution_id: Optional[str] = None
    requires_confirmation: bool = False
