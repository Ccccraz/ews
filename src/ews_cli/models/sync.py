from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from ews_cli.contracts import ContractModel
from ews_cli.models.mailbox import Folder


class FolderKind(StrEnum):
    """The cached role of a folder."""

    MAIL = "mail"
    CONTACTS = "contacts"


class FolderChangeKind(StrEnum):
    """Folder hierarchy change kinds returned by EWS."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class MessageChangeKind(StrEnum):
    """Message change kinds returned by EWS."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    READ_FLAG_CHANGE = "read_flag_change"


class ContactChangeKind(StrEnum):
    """Contact item change kinds returned by EWS."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class FolderChange(ContractModel):
    """One strictly typed folder hierarchy change."""

    kind: FolderChangeKind
    folder_id: str
    change_key: str | None = None
    folder: Folder | None = None
    folder_kind: FolderKind | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.kind is FolderChangeKind.DELETE and self.folder is not None:
            raise ValueError("delete folder changes must not include a folder")
        if self.kind is not FolderChangeKind.DELETE and self.folder is None:
            raise ValueError("create and update folder changes require a folder")
        if self.kind is not FolderChangeKind.DELETE and self.folder_kind is None:
            raise ValueError("create and update folder changes require a folder kind")
        return self


class MessageChange(ContractModel):
    """One strictly typed message synchronization change."""

    kind: MessageChangeKind
    message_id: str
    change_key: str | None = None
    is_read: bool | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.kind in {MessageChangeKind.CREATE, MessageChangeKind.UPDATE}:
            if self.change_key is None:
                raise ValueError("create and update message changes require a change key")
        elif self.kind is MessageChangeKind.READ_FLAG_CHANGE:
            if self.is_read is None:
                raise ValueError("read flag changes require is_read")
        return self


class ContactChange(ContractModel):
    """One strictly typed contact synchronization change."""

    kind: ContactChangeKind
    contact_id: str
    change_key: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.kind in {ContactChangeKind.CREATE, ContactChangeKind.UPDATE}:
            if self.change_key is None:
                raise ValueError("create and update contact changes require a change key")
        return self


class ContactSyncResult(ContractModel):
    """A fully consumed contact-item synchronization response."""

    changes: list[ContactChange]
    sync_state: str


class FolderSyncResult(ContractModel):
    """A fully consumed folder hierarchy synchronization response."""

    changes: list[FolderChange]
    sync_state: str
    well_known_folder_ids: dict[str, str] = Field(default_factory=dict)


class MessageSyncResult(ContractModel):
    """A fully consumed folder-item synchronization response."""

    changes: list[MessageChange]
    sync_state: str


class FolderSyncCounts(ContractModel):
    """Applied visible folder changes."""

    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)


class MessageSyncCounts(ContractModel):
    """Applied cached message changes."""

    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)
    read_state_changed: int = Field(default=0, ge=0)


class ContactSyncCounts(ContractModel):
    """Applied cached contact changes."""

    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)


class MailboxSyncResult(ContractModel):
    """Public summary for one completed mailbox synchronization."""

    user: str
    folders: FolderSyncCounts
    messages: MessageSyncCounts
    contacts: ContactSyncCounts
