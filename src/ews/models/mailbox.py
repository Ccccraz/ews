from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ews.contracts import ContractModel


class ReadState(StrEnum):
    """Supported read-state filters."""

    ANY = "any"
    READ = "read"
    UNREAD = "unread"


class Importance(StrEnum):
    """Normalized EWS message importance."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Folder(ContractModel):
    """A mail-capable folder."""

    id: str
    parent_id: str | None
    name: str
    well_known_name: str | None = None
    total_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)


class MailboxAddress(ContractModel):
    """An EWS mailbox identifier and optional display name."""

    name: str | None = None
    address: str = Field(min_length=1)


class MessageSummary(ContractModel):
    """Fields returned by message list."""

    id: str
    change_key: str
    parent_folder_id: str
    subject: str | None
    from_address: str | None
    received_at: AwareDatetime
    is_read: bool
    has_attachments: bool
    importance: Importance

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: datetime) -> datetime:
        return _to_utc(value)


class MessageBody(ContractModel):
    """A message body with its EWS content type."""

    content_type: Literal["text", "html"]
    content: str


class InternetHeader(ContractModel):
    """One Internet header; duplicates remain separate entries."""

    name: str
    value: str


class AttachmentMetadata(ContractModel):
    """Attachment metadata without attachment content."""

    id: str
    kind: Literal["file", "item"]
    name: str
    content_type: str | None
    size: int = Field(ge=0)
    is_inline: bool
    content_id: str | None


class MessageDetail(MessageSummary):
    """A message summary plus detailed EWS fields."""

    sender: MailboxAddress | None
    to: list[MailboxAddress]
    cc: list[MailboxAddress]
    bcc: list[MailboxAddress]
    reply_to: list[MailboxAddress]
    sent_at: AwareDatetime | None
    created_at: AwareDatetime | None
    internet_message_id: str | None
    in_reply_to: str | None
    body: MessageBody
    internet_headers: list[InternetHeader]
    attachments: list[AttachmentMetadata]

    @field_validator("sent_at", "created_at")
    @classmethod
    def normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _to_utc(value)


class MessageListQuery(ContractModel):
    """Validated filters and pagination for one message list request."""

    folder: str = "inbox"
    read_state: ReadState = ReadState.ANY
    sender: str | None = Field(default=None, min_length=1)
    subject_contains: str | None = None
    body_contains: str | None = None
    received_from: AwareDatetime | None = None
    received_before: AwareDatetime | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("received_from", "received_before")
    @classmethod
    def normalize_query_datetime(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _to_utc(value)

    @model_validator(mode="after")
    def validate_received_range(self) -> Self:
        if (
            self.received_from is not None
            and self.received_before is not None
            and self.received_from >= self.received_before
        ):
            raise ValueError("received_from must be earlier than received_before")
        return self


class Pagination(ContractModel):
    """Offset pagination metadata."""

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    has_more: bool
    next_offset: int | None


class FolderListResult(ContractModel):
    """Mail folders returned for the selected profile."""

    user: str
    folders: list[Folder]


class MessageListResult(ContractModel):
    """One page of message summaries."""

    user: str
    messages: list[MessageSummary]
    pagination: Pagination


class MessageGetResult(ContractModel):
    """One detailed message returned for the selected profile."""

    user: str
    message: MessageDetail


def _to_utc(value: datetime) -> datetime:
    """Convert datetime subclasses without calling their astimezone override."""
    plain = datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=value.tzinfo,
        fold=value.fold,
    )
    return plain.astimezone(UTC)
