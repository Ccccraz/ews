from typing import Self

from pydantic import EmailStr, model_validator

from ews_cli.contracts import ContractModel
from ews_cli.models.mailbox import MessageBody


class OutgoingMessage(ContractModel):
    """A new message requested by the caller."""

    to: list[EmailStr] = []
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    subject: str = ""
    body: MessageBody

    @model_validator(mode="after")
    def validate_recipients(self) -> Self:
        if not (self.to or self.cc or self.bcc):
            raise ValueError("At least one of to, cc or bcc is required")
        return self


class DraftMessage(ContractModel):
    """A new draft that may omit recipients until it is ready to send."""

    to: list[EmailStr] = []
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    subject: str = ""
    body: MessageBody


class OutgoingReply(ContractModel):
    """A reply requested by the caller; omitting the subject derives the standard one."""

    subject: str | None = None
    body: MessageBody
