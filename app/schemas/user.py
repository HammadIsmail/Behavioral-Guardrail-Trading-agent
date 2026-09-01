from pydantic import BaseModel, Field
from typing import Optional
import uuid

class UserSettings(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    agent_interval_seconds: int = 900
    agent_enabled: bool = True

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class UserInDB(UserBase):
    id: str
    hashed_password: str
    settings: UserSettings

    class Config:
        from_attributes = True

class UserPublic(UserBase):
    id: str
    settings: UserSettings