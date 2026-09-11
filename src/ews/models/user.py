from pydantic import BaseModel, ConfigDict, EmailStr, Field


class User(BaseModel):
    """An Exchange mailbox identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    mailbox: EmailStr
    username: str = Field(min_length=1)
