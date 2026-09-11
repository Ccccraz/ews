"""SQLModel persistence for cached mailbox data."""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import URL, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, col, create_engine, delete, select

from ews.models import (
    AttachmentMetadata,
    Folder,
    InternetHeader,
    MailboxAddress,
    MessageDetail,
)

_SCHEMA_VERSION = "1"
_SCHEMA_VERSION_KEY = "schema_version"

_ADDRESS_ADAPTER = TypeAdapter[MailboxAddress | None](MailboxAddress | None)
_ADDRESS_LIST_ADAPTER = TypeAdapter(list[MailboxAddress])
_HEADER_LIST_ADAPTER = TypeAdapter(list[InternetHeader])
_ATTACHMENT_LIST_ADAPTER = TypeAdapter(list[AttachmentMetadata])


class CacheMetadataRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "cache_metadata"  # pyright: ignore[reportIncompatibleVariableOverride]

    key: str = Field(primary_key=True)
    value: str


class FolderRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "folders"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    id: str = Field(primary_key=True)
    position: int
    parent_id: str | None = None
    name: str
    well_known_name: str | None = None
    total_count: int
    unread_count: int


class MessageRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "messages"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    id: str = Field(primary_key=True)
    change_key: str
    parent_folder_id: str
    subject: str | None = None
    from_address: str | None = None
    received_at: str
    is_read: bool
    has_attachments: bool
    importance: str
    sender_json: str
    to_json: str
    cc_json: str
    bcc_json: str
    reply_to_json: str
    sent_at: str | None = None
    created_at: str | None = None
    internet_message_id: str | None = None
    in_reply_to: str | None = None
    body_content_type: str
    body_content: str
    internet_headers_json: str
    attachments_json: str


class MailboxStoreError(Exception):
    """Raised when the local mailbox store cannot complete an operation."""


class UnsupportedCacheSchemaVersionError(MailboxStoreError):
    """Raised when a cache uses an unsupported schema version."""


def default_cache_path(home: Path | None = None) -> Path:
    """Return the fixed path for the local mailbox cache."""
    root = Path.home() if home is None else home
    return root / ".config" / "taskseed" / "ews" / "cache.db"


class SqliteMailboxStore:
    """Persist complete mailbox models in a local SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_cache_path() if path is None else path
        database_url = URL.create("sqlite", database=str(self.path))
        self._engine = create_engine(database_url, poolclass=NullPool)

    def initialize(self) -> None:
        """Create the version-one schema without changing existing data."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._reject_unsupported_existing_schema()
            SQLModel.metadata.create_all(self._engine)
            with Session(self._engine) as session:
                metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
                if metadata is None:
                    session.add(CacheMetadataRecord(key=_SCHEMA_VERSION_KEY, value=_SCHEMA_VERSION))
                    session.commit()
        except UnsupportedCacheSchemaVersionError:
            raise
        except (OSError, SQLAlchemyError) as error:
            raise MailboxStoreError(f"Unable to initialize mailbox cache: {self.path}") from error

    def replace_folders(self, mailbox: str, folders: Sequence[Folder]) -> None:
        """Atomically replace one mailbox's complete folder snapshot."""
        mailbox_key = _mailbox_key(mailbox)
        rows = [
            _folder_row(mailbox_key, position, folder) for position, folder in enumerate(folders)
        ]
        try:
            with self._session() as session:
                session.exec(delete(FolderRecord).where(col(FolderRecord.mailbox) == mailbox_key))
                session.add_all(rows)
                session.commit()
        except UnsupportedCacheSchemaVersionError:
            raise
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to replace cached folders") from error

    def list_folders(self, mailbox: str) -> list[Folder]:
        """Return cached folders in the order supplied by replace_folders."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                statement = (
                    select(FolderRecord)
                    .where(col(FolderRecord.mailbox) == mailbox_key)
                    .order_by(col(FolderRecord.position))
                )
                rows = session.exec(statement).all()
                return [_folder_from_row(row) for row in rows]
        except UnsupportedCacheSchemaVersionError:
            raise
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached folder data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached folders") from error

    def upsert_messages(self, mailbox: str, messages: Sequence[MessageDetail]) -> None:
        """Atomically insert or completely update cached messages."""
        mailbox_key = _mailbox_key(mailbox)
        rows = [_message_row(mailbox_key, message) for message in messages]
        try:
            with self._session() as session:
                for row in rows:
                    session.merge(row)
                session.commit()
        except UnsupportedCacheSchemaVersionError:
            raise
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to update cached messages") from error

    def get_message(self, mailbox: str, message_id: str) -> MessageDetail | None:
        """Return one complete cached message, or None on a cache miss."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                row = session.get(MessageRecord, (mailbox_key, message_id))
                return None if row is None else _message_from_row(row)
        except UnsupportedCacheSchemaVersionError:
            raise
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached message data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached message") from error

    def _reject_unsupported_existing_schema(self) -> None:
        if not self.path.is_file() or not inspect(self._engine).has_table("cache_metadata"):
            return

        with Session(self._engine) as session:
            metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
            if metadata is not None and metadata.value != _SCHEMA_VERSION:
                raise UnsupportedCacheSchemaVersionError(
                    f"Unsupported cache schema version: {metadata.value}"
                )

    @contextmanager
    def _session(self) -> Generator[Session]:
        if not self.path.is_file():
            raise MailboxStoreError(f"Mailbox cache is not initialized: {self.path}")

        with Session(self._engine) as session:
            metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
            if metadata is None:
                raise MailboxStoreError("Mailbox cache schema version is missing")
            if metadata.value != _SCHEMA_VERSION:
                raise UnsupportedCacheSchemaVersionError(
                    f"Unsupported cache schema version: {metadata.value}"
                )
            yield session


def _mailbox_key(mailbox: str) -> str:
    value = mailbox.strip().casefold()
    if not value:
        raise ValueError("mailbox must not be empty")
    return value


def _folder_row(mailbox: str, position: int, folder: Folder) -> FolderRecord:
    return FolderRecord(
        mailbox=mailbox,
        id=folder.id,
        position=position,
        parent_id=folder.parent_id,
        name=folder.name,
        well_known_name=folder.well_known_name,
        total_count=folder.total_count,
        unread_count=folder.unread_count,
    )


def _folder_from_row(row: FolderRecord) -> Folder:
    return Folder(
        id=row.id,
        parent_id=row.parent_id,
        name=row.name,
        well_known_name=row.well_known_name,
        total_count=row.total_count,
        unread_count=row.unread_count,
    )


def _message_row(mailbox: str, message: MessageDetail) -> MessageRecord:
    return MessageRecord(
        mailbox=mailbox,
        id=message.id,
        change_key=message.change_key,
        parent_folder_id=message.parent_folder_id,
        subject=message.subject,
        from_address=str(message.from_address) if message.from_address is not None else None,
        received_at=message.received_at.isoformat(),
        is_read=message.is_read,
        has_attachments=message.has_attachments,
        importance=message.importance.value,
        sender_json=_ADDRESS_ADAPTER.dump_json(message.sender).decode(),
        to_json=_ADDRESS_LIST_ADAPTER.dump_json(message.to).decode(),
        cc_json=_ADDRESS_LIST_ADAPTER.dump_json(message.cc).decode(),
        bcc_json=_ADDRESS_LIST_ADAPTER.dump_json(message.bcc).decode(),
        reply_to_json=_ADDRESS_LIST_ADAPTER.dump_json(message.reply_to).decode(),
        sent_at=message.sent_at.isoformat() if message.sent_at is not None else None,
        created_at=message.created_at.isoformat() if message.created_at is not None else None,
        internet_message_id=message.internet_message_id,
        in_reply_to=message.in_reply_to,
        body_content_type=message.body.content_type,
        body_content=message.body.content,
        internet_headers_json=_HEADER_LIST_ADAPTER.dump_json(message.internet_headers).decode(),
        attachments_json=_ATTACHMENT_LIST_ADAPTER.dump_json(message.attachments).decode(),
    )


def _message_from_row(row: MessageRecord) -> MessageDetail:
    return MessageDetail.model_validate(
        {
            "id": row.id,
            "change_key": row.change_key,
            "parent_folder_id": row.parent_folder_id,
            "subject": row.subject,
            "from_address": row.from_address,
            "received_at": row.received_at,
            "is_read": row.is_read,
            "has_attachments": row.has_attachments,
            "importance": row.importance,
            "sender": _ADDRESS_ADAPTER.validate_json(row.sender_json),
            "to": _ADDRESS_LIST_ADAPTER.validate_json(row.to_json),
            "cc": _ADDRESS_LIST_ADAPTER.validate_json(row.cc_json),
            "bcc": _ADDRESS_LIST_ADAPTER.validate_json(row.bcc_json),
            "reply_to": _ADDRESS_LIST_ADAPTER.validate_json(row.reply_to_json),
            "sent_at": row.sent_at,
            "created_at": row.created_at,
            "internet_message_id": row.internet_message_id,
            "in_reply_to": row.in_reply_to,
            "body": {
                "content_type": row.body_content_type,
                "content": row.body_content,
            },
            "internet_headers": _HEADER_LIST_ADAPTER.validate_json(row.internet_headers_json),
            "attachments": _ATTACHMENT_LIST_ADAPTER.validate_json(row.attachments_json),
        }
    )
