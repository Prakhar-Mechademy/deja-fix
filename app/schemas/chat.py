"""Chat request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    created_at: datetime
    is_active: bool


class SessionCreate(BaseModel):
    user_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=255)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(pattern=r"^(user|assistant|system)$")
    content: str = Field(min_length=1)


class ChatReplyResponse(BaseModel):
    reply: str
    source: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    user_id: str
    role: str
    content: str
    created_at: datetime
