from pydantic import Field

from ews.contracts import ContractModel
from ews.models.mailbox import (
    AttachmentMetadata,
    MailboxAddress,
    MessageBody,
    MessageSummary,
    Pagination,
)


class MessageThreadQuery(ContractModel):
    """Validated pagination for one message thread request."""

    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=200)


class ThreadMessage(MessageSummary):
    """One cached message of a conversation, with its folder and body."""

    folder_name: str
    to: list[MailboxAddress]
    cc: list[MailboxAddress]
    attachments: list[AttachmentMetadata]
    body: MessageBody
    text_body: str | None = None


class MessageThreadResult(ContractModel):
    """Every cached message of one conversation, in reading order."""

    user: str
    conversation_id: str | None
    conversation_topic: str | None
    message_count: int = Field(ge=0)
    messages: list[ThreadMessage]
    pagination: Pagination
