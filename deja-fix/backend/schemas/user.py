from pydantic import BaseModel, Field
from typing import Optional

class UserCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)

class UserOut(BaseModel):
    id: str
    external_id: str
    display_name: Optional[str] = None
    class Config: from_attributes = True
