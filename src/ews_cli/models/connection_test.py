from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ConnectionTestResult(BaseModel):
    """Observable result of an authenticated EWS request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: str
    mailbox: EmailStr
    server_version: str
    inbox_total_count: int | None = Field(ge=0)
    inbox_unread_count: int | None = Field(ge=0)
