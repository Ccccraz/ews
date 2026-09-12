"""SQLModel persistence for synchronized mailbox data."""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import URL, func, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, col, create_engine, delete, select

from ews.models import (
    AttachmentMetadata,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderSyncCounts,
    Importance,
    InternetHeader,
    MailboxAddress,
    MessageChange,
    MessageChangeKind,
    MessageDetail,
    MessageListQuery,
    MessageSummary,
    MessageSyncCounts,
    ReadState,
)

_SCHEMA_VERSION = "2"
_PREVIOUS_SCHEMA_VERSION = "1"
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


class HierarchySyncStateRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "hierarchy_sync_states"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    sync_state: str


class ItemSyncStateRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "item_sync_states"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    folder_id: str = Field(primary_key=True)
    sync_state: str


class MailboxStateRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "mailbox_states"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    ready: bool = False


class MailboxStoreError(Exception):
    """Raised when the local mailbox store cannot complete an operation."""


class MailboxCacheNotReadyError(Exception):
    """Raised when no complete synchronization exists for a mailbox."""


class UnsupportedCacheSchemaVersionError(MailboxStoreError):
    """Raised when a cache uses an unsupported schema version."""


def default_cache_path(home: Path | None = None) -> Path:
    """Return the fixed path for the local mailbox cache."""
    root = Path.home() if home is None else home
    return root / ".config" / "taskseed" / "ews" / "cache.db"


class SqliteMailboxStore:
    """Persist synchronized mailbox models in a local SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_cache_path() if path is None else path
        database_url = URL.create("sqlite", database=str(self.path))
        self._engine = create_engine(database_url, poolclass=NullPool)

    def initialize(self) -> None:
        """Create schema v2 or migrate a schema-v1 cache without marking it ready."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            version = self._existing_schema_version()
            if version not in {None, _PREVIOUS_SCHEMA_VERSION, _SCHEMA_VERSION}:
                raise UnsupportedCacheSchemaVersionError(
                    f"Unsupported cache schema version: {version}"
                )
            SQLModel.metadata.create_all(self._engine)
            with Session(self._engine) as session:
                metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
                if metadata is None:
                    session.add(CacheMetadataRecord(key=_SCHEMA_VERSION_KEY, value=_SCHEMA_VERSION))
                elif metadata.value == _PREVIOUS_SCHEMA_VERSION:
                    metadata.value = _SCHEMA_VERSION
                    session.add(metadata)
                session.commit()
        except UnsupportedCacheSchemaVersionError:
            raise
        except (OSError, SQLAlchemyError) as error:
            raise MailboxStoreError(f"Unable to initialize mailbox cache: {self.path}") from error

    def require_ready(self, mailbox: str) -> None:
        """Require a complete initial synchronization for the selected mailbox."""
        mailbox_key = _mailbox_key(mailbox)
        if not self.path.is_file():
            raise MailboxCacheNotReadyError("Mailbox cache is not ready")
        try:
            if self._existing_schema_version() == _PREVIOUS_SCHEMA_VERSION:
                raise MailboxCacheNotReadyError("Mailbox cache is not ready")
            with self._session() as session:
                state = session.get(MailboxStateRecord, mailbox_key)
                if state is None or not state.ready:
                    raise MailboxCacheNotReadyError("Mailbox cache is not ready")
        except MailboxCacheNotReadyError, UnsupportedCacheSchemaVersionError:
            raise
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read mailbox cache state") from error

    def mark_ready(self, mailbox: str) -> None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                session.merge(MailboxStateRecord(mailbox=mailbox_key, ready=True))
                session.commit()
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to update mailbox cache state") from error

    def get_hierarchy_sync_state(self, mailbox: str) -> str | None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                row = session.get(HierarchySyncStateRecord, mailbox_key)
                return None if row is None else row.sync_state
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read hierarchy sync state") from error

    def get_item_sync_state(self, mailbox: str, folder_id: str) -> str | None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                row = session.get(ItemSyncStateRecord, (mailbox_key, folder_id))
                return None if row is None else row.sync_state
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read item sync state") from error

    def apply_folder_changes(
        self,
        mailbox: str,
        changes: Sequence[FolderChange],
        sync_state: str,
        *,
        reset: bool,
        well_known_folder_ids: Mapping[str, str] | None = None,
    ) -> FolderSyncCounts:
        """Apply hierarchy changes and advance its state in one transaction."""
        mailbox_key = _mailbox_key(mailbox)
        created = updated = deleted_count = 0
        try:
            with self._session() as session:
                if reset:
                    visible_ids = {
                        change.folder_id
                        for change in changes
                        if change.kind is not FolderChangeKind.DELETE
                    }
                    existing_rows = session.exec(
                        select(FolderRecord).where(col(FolderRecord.mailbox) == mailbox_key)
                    ).all()
                    for row in existing_rows:
                        if row.id not in visible_ids:
                            self._delete_folder(session, mailbox_key, row.id)

                next_position = self._next_folder_position(session, mailbox_key)
                for change in changes:
                    existing = session.get(FolderRecord, (mailbox_key, change.folder_id))
                    if change.kind is FolderChangeKind.DELETE:
                        if existing is not None:
                            self._delete_folder(session, mailbox_key, change.folder_id)
                            deleted_count += 1
                        continue
                    folder = change.folder
                    if folder is None:
                        raise MailboxStoreError("Folder change payload is missing")
                    position = existing.position if existing is not None else next_position
                    if existing is None:
                        next_position += 1
                    if change.kind is FolderChangeKind.CREATE:
                        created += 1
                    else:
                        updated += 1
                    session.merge(_folder_row(mailbox_key, position, folder))

                for well_known_name, folder_id in (well_known_folder_ids or {}).items():
                    old_rows = session.exec(
                        select(FolderRecord).where(
                            col(FolderRecord.mailbox) == mailbox_key,
                            func.lower(col(FolderRecord.well_known_name))
                            == well_known_name.casefold(),
                        )
                    ).all()
                    for row in old_rows:
                        row.well_known_name = None
                        session.add(row)
                    folder_row = session.get(FolderRecord, (mailbox_key, folder_id))
                    if folder_row is not None:
                        folder_row.well_known_name = well_known_name
                        session.add(folder_row)

                session.merge(HierarchySyncStateRecord(mailbox=mailbox_key, sync_state=sync_state))
                session.commit()
        except MailboxStoreError:
            raise
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to apply folder synchronization") from error
        return FolderSyncCounts(created=created, updated=updated, deleted=deleted_count)

    def apply_message_changes(
        self,
        mailbox: str,
        folder_id: str,
        changes: Sequence[MessageChange],
        messages: Mapping[str, MessageDetail],
        sync_state: str,
        *,
        reset: bool,
    ) -> MessageSyncCounts:
        """Apply one folder's message changes and state in one transaction."""
        mailbox_key = _mailbox_key(mailbox)
        created = updated = deleted_count = read_state_changed = 0
        try:
            with self._session() as session:
                if reset:
                    session.exec(
                        delete(MessageRecord).where(
                            col(MessageRecord.mailbox) == mailbox_key,
                            col(MessageRecord.parent_folder_id) == folder_id,
                        )
                    )
                for change in changes:
                    existing = session.get(MessageRecord, (mailbox_key, change.message_id))
                    if change.kind is MessageChangeKind.DELETE:
                        if existing is not None:
                            session.delete(existing)
                            deleted_count += 1
                    elif change.kind is MessageChangeKind.READ_FLAG_CHANGE:
                        if existing is not None and existing.is_read != change.is_read:
                            existing.is_read = bool(change.is_read)
                            session.add(existing)
                            read_state_changed += 1
                    else:
                        message = messages.get(change.message_id)
                        if message is None:
                            raise MailboxStoreError(
                                f"Fetched message is missing: {change.message_id}"
                            )
                        session.merge(_message_row(mailbox_key, message))
                        if change.kind is MessageChangeKind.CREATE:
                            created += 1
                        else:
                            updated += 1
                session.merge(
                    ItemSyncStateRecord(
                        mailbox=mailbox_key, folder_id=folder_id, sync_state=sync_state
                    )
                )
                session.commit()
        except MailboxStoreError:
            raise
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to apply message synchronization") from error
        return MessageSyncCounts(
            created=created,
            updated=updated,
            deleted=deleted_count,
            read_state_changed=read_state_changed,
        )

    def replace_folders(self, mailbox: str, folders: Sequence[Folder]) -> None:
        """Atomically replace one mailbox's complete folder snapshot."""
        changes = [
            FolderChange(kind=FolderChangeKind.CREATE, folder_id=folder.id, folder=folder)
            for folder in folders
        ]
        self.apply_folder_changes(mailbox, changes, "snapshot", reset=True)

    def list_folders(self, mailbox: str) -> list[Folder]:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                rows = session.exec(
                    select(FolderRecord)
                    .where(col(FolderRecord.mailbox) == mailbox_key)
                    .order_by(col(FolderRecord.position))
                ).all()
                ids = {row.id for row in rows}
                return [_folder_from_row(row, ids) for row in rows]
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached folder data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached folders") from error

    def list_messages(
        self, mailbox: str, query: MessageListQuery
    ) -> tuple[list[MessageSummary], bool]:
        """Return a locally filtered reverse-chronological message page."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                folder_id = self._resolve_folder_id(session, mailbox_key, query.folder)
                if folder_id is None:
                    return [], False
                statement = select(MessageRecord).where(
                    col(MessageRecord.mailbox) == mailbox_key,
                    col(MessageRecord.parent_folder_id) == folder_id,
                )
                if query.read_state is ReadState.READ:
                    statement = statement.where(col(MessageRecord.is_read).is_(True))
                elif query.read_state is ReadState.UNREAD:
                    statement = statement.where(col(MessageRecord.is_read).is_(False))
                if query.sender is not None:
                    statement = statement.where(
                        func.lower(col(MessageRecord.from_address)) == str(query.sender).casefold()
                    )
                if query.subject_contains is not None:
                    statement = statement.where(
                        col(MessageRecord.subject).contains(query.subject_contains, autoescape=True)
                    )
                if query.body_contains is not None:
                    statement = statement.where(
                        col(MessageRecord.body_content).contains(
                            query.body_contains, autoescape=True
                        )
                    )
                if query.received_from is not None:
                    statement = statement.where(
                        col(MessageRecord.received_at) >= _utc_text(query.received_from)
                    )
                if query.received_before is not None:
                    statement = statement.where(
                        col(MessageRecord.received_at) < _utc_text(query.received_before)
                    )
                rows = session.exec(
                    statement.order_by(col(MessageRecord.received_at).desc(), col(MessageRecord.id))
                    .offset(query.offset)
                    .limit(query.limit + 1)
                ).all()
                return (
                    [_message_summary_from_row(row) for row in rows[: query.limit]],
                    len(rows) > query.limit,
                )
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached message data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to list cached messages") from error

    def folder_exists(self, mailbox: str, folder: str) -> bool:
        return self.resolve_folder_id(mailbox, folder) is not None

    def resolve_folder_id(self, mailbox: str, folder: str) -> str | None:
        """Resolve a folder selector to its cached EWS folder ID."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                return self._resolve_folder_id(session, mailbox_key, folder)
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to resolve cached folder") from error

    def upsert_messages(self, mailbox: str, messages: Sequence[MessageDetail]) -> None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                for message in messages:
                    session.merge(_message_row(mailbox_key, message))
                session.commit()
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to update cached messages") from error

    def get_message(self, mailbox: str, message_id: str) -> MessageDetail | None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                row = session.get(MessageRecord, (mailbox_key, message_id))
                return None if row is None else _message_from_row(row)
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached message data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached message") from error

    def _existing_schema_version(self) -> str | None:
        if not self.path.is_file():
            return None
        if not inspect(self._engine).has_table("cache_metadata"):
            raise MailboxStoreError("Mailbox cache schema version is missing")
        with Session(self._engine) as session:
            metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
            if metadata is None:
                raise MailboxStoreError("Mailbox cache schema version is missing")
            return metadata.value

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

    @staticmethod
    def _next_folder_position(session: Session, mailbox: str) -> int:
        positions = session.exec(
            select(FolderRecord.position).where(col(FolderRecord.mailbox) == mailbox)
        ).all()
        return max(positions, default=-1) + 1

    @staticmethod
    def _resolve_folder_id(session: Session, mailbox: str, folder: str) -> str | None:
        row = session.exec(
            select(FolderRecord).where(
                col(FolderRecord.mailbox) == mailbox,
                func.lower(col(FolderRecord.well_known_name)) == folder.casefold(),
            )
        ).first()
        if row is not None:
            return row.id
        return folder if session.get(FolderRecord, (mailbox, folder)) is not None else None

    @staticmethod
    def _delete_folder(session: Session, mailbox: str, folder_id: str) -> None:
        folder = session.get(FolderRecord, (mailbox, folder_id))
        if folder is not None:
            session.delete(folder)
        state = session.get(ItemSyncStateRecord, (mailbox, folder_id))
        if state is not None:
            session.delete(state)
        session.exec(
            delete(MessageRecord).where(
                col(MessageRecord.mailbox) == mailbox,
                col(MessageRecord.parent_folder_id) == folder_id,
            )
        )


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


def _folder_from_row(row: FolderRecord, visible_ids: set[str]) -> Folder:
    return Folder(
        id=row.id,
        parent_id=row.parent_id if row.parent_id in visible_ids else None,
        name=row.name,
        well_known_name=row.well_known_name,
        total_count=row.total_count,
        unread_count=row.unread_count,
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _message_row(mailbox: str, message: MessageDetail) -> MessageRecord:
    return MessageRecord(
        mailbox=mailbox,
        id=message.id,
        change_key=message.change_key,
        parent_folder_id=message.parent_folder_id,
        subject=message.subject,
        from_address=str(message.from_address) if message.from_address is not None else None,
        received_at=_utc_text(message.received_at),
        is_read=message.is_read,
        has_attachments=message.has_attachments,
        importance=message.importance.value,
        sender_json=_ADDRESS_ADAPTER.dump_json(message.sender).decode(),
        to_json=_ADDRESS_LIST_ADAPTER.dump_json(message.to).decode(),
        cc_json=_ADDRESS_LIST_ADAPTER.dump_json(message.cc).decode(),
        bcc_json=_ADDRESS_LIST_ADAPTER.dump_json(message.bcc).decode(),
        reply_to_json=_ADDRESS_LIST_ADAPTER.dump_json(message.reply_to).decode(),
        sent_at=_utc_text(message.sent_at) if message.sent_at is not None else None,
        created_at=_utc_text(message.created_at) if message.created_at is not None else None,
        internet_message_id=message.internet_message_id,
        in_reply_to=message.in_reply_to,
        body_content_type=message.body.content_type,
        body_content=message.body.content,
        internet_headers_json=_HEADER_LIST_ADAPTER.dump_json(message.internet_headers).decode(),
        attachments_json=_ATTACHMENT_LIST_ADAPTER.dump_json(message.attachments).decode(),
    )


def _message_summary_from_row(row: MessageRecord) -> MessageSummary:
    return MessageSummary(
        id=row.id,
        change_key=row.change_key,
        parent_folder_id=row.parent_folder_id,
        subject=row.subject,
        from_address=row.from_address,
        received_at=datetime.fromisoformat(row.received_at),
        is_read=row.is_read,
        has_attachments=row.has_attachments,
        importance=Importance(row.importance),
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
            "body": {"content_type": row.body_content_type, "content": row.body_content},
            "internet_headers": _HEADER_LIST_ADAPTER.validate_json(row.internet_headers_json),
            "attachments": _ATTACHMENT_LIST_ADAPTER.validate_json(row.attachments_json),
        }
    )
