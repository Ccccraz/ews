"""SQLModel persistence for synchronized mailbox data."""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import URL, Index, func, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, col, create_engine, delete, select

from ews_cli.models import (
    AttachmentMetadata,
    Contact,
    ContactAddress,
    ContactChange,
    ContactChangeKind,
    ContactEmail,
    ContactIm,
    ContactListQuery,
    ContactPhone,
    ContactSyncCounts,
    FlagStatus,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderKind,
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

_SCHEMA_VERSION = "4"
_REBUILD_SCHEMA_VERSIONS = {"1", "2", "3"}
_SCHEMA_VERSION_KEY = "schema_version"

_ADDRESS_ADAPTER = TypeAdapter[MailboxAddress | None](MailboxAddress | None)
_ADDRESS_LIST_ADAPTER = TypeAdapter(list[MailboxAddress])
_HEADER_LIST_ADAPTER = TypeAdapter(list[InternetHeader])
_ATTACHMENT_LIST_ADAPTER = TypeAdapter(list[AttachmentMetadata])
_CATEGORY_LIST_ADAPTER = TypeAdapter(list[str])
_CONTACT_EMAIL_LIST_ADAPTER = TypeAdapter(list[ContactEmail])
_CONTACT_PHONE_LIST_ADAPTER = TypeAdapter(list[ContactPhone])
_CONTACT_ADDRESS_LIST_ADAPTER = TypeAdapter(list[ContactAddress])
_CONTACT_IM_LIST_ADAPTER = TypeAdapter(list[ContactIm])


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
    kind: str = FolderKind.MAIL.value
    total_count: int
    unread_count: int


class ContactRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "contacts"  # pyright: ignore[reportIncompatibleVariableOverride]

    mailbox: str = Field(primary_key=True)
    id: str = Field(primary_key=True)
    change_key: str
    parent_folder_id: str
    display_name: str
    file_as: str | None = None
    given_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    nickname: str | None = None
    initials: str | None = None
    generation: str | None = None
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None
    office: str | None = None
    manager: str | None = None
    profession: str | None = None
    business_homepage: str | None = None
    emails_json: str
    phones_json: str
    addresses_json: str
    im_addresses_json: str
    categories_json: str = "[]"
    notes: str | None = None
    birthday: str | None = None
    has_picture: bool = False
    search_text: str = ""


class MessageRecord(SQLModel, table=True):
    __tablename__: ClassVar[str] = "messages"  # pyright: ignore[reportIncompatibleVariableOverride]
    __table_args__ = (Index("ix_messages_conversation", "mailbox", "conversation_id"),)

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
    conversation_id: str | None = None
    conversation_topic: str | None = None
    conversation_index: str | None = None
    conversation_depth: int | None = None
    text_body: str | None = None
    references: str | None = None
    is_draft: bool = False
    categories_json: str = "[]"
    flag_status: str = FlagStatus.NONE.value


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
    return root / ".config" / "taskseed" / "ews-cli" / "cache.db"


class SqliteMailboxStore:
    """Persist synchronized mailbox models in a local SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_cache_path() if path is None else path
        database_url = URL.create("sqlite", database=str(self.path))
        self._engine = create_engine(database_url, poolclass=NullPool)

    def initialize(self) -> None:
        """Create schema v3, rebuilding any older cache without marking it ready."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            version = self._existing_schema_version()
            if version not in {None, _SCHEMA_VERSION, *_REBUILD_SCHEMA_VERSIONS}:
                raise UnsupportedCacheSchemaVersionError(
                    f"Unsupported cache schema version: {version}"
                )
            if version in _REBUILD_SCHEMA_VERSIONS:
                self._rebuild_older_schema()
            SQLModel.metadata.create_all(self._engine)
            with Session(self._engine) as session:
                metadata = session.get(CacheMetadataRecord, _SCHEMA_VERSION_KEY)
                if metadata is None:
                    session.add(CacheMetadataRecord(key=_SCHEMA_VERSION_KEY, value=_SCHEMA_VERSION))
                elif metadata.value in _REBUILD_SCHEMA_VERSIONS:
                    metadata.value = _SCHEMA_VERSION
                    session.add(metadata)
                session.commit()
        except UnsupportedCacheSchemaVersionError:
            raise
        except (OSError, SQLAlchemyError) as error:
            raise MailboxStoreError(f"Unable to initialize mailbox cache: {self.path}") from error

    def _rebuild_older_schema(self) -> None:
        """Drop the changed tables so the next synchronization refills them all."""
        with self._engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS messages"))
            connection.execute(text("DROP TABLE IF EXISTS folders"))
            connection.execute(text("DROP TABLE IF EXISTS contacts"))
        with Session(self._engine) as session:
            session.exec(delete(HierarchySyncStateRecord))
            session.exec(delete(ItemSyncStateRecord))
            session.exec(delete(MailboxStateRecord))
            session.commit()

    def require_ready(self, mailbox: str) -> None:
        """Require a complete initial synchronization for the selected mailbox."""
        mailbox_key = _mailbox_key(mailbox)
        if not self.path.is_file():
            raise MailboxCacheNotReadyError("Mailbox cache is not ready")
        try:
            if self._existing_schema_version() in _REBUILD_SCHEMA_VERSIONS:
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
                    folder_kind = change.folder_kind
                    if folder_kind is None:
                        raise MailboxStoreError("Folder change payload is missing a kind")
                    position = existing.position if existing is not None else next_position
                    if existing is None:
                        next_position += 1
                        created += 1
                    elif change.kind is FolderChangeKind.UPDATE:
                        updated += 1
                    session.merge(_folder_row(mailbox_key, position, folder, folder_kind))

                # A supplied id map is the only source of well-known names: clear every name
                # first, so a folder that no longer resolves loses a stale one, then apply
                # the map. This repair is deliberately not counted as a folder change.
                if well_known_folder_ids is not None:
                    stale_rows = session.exec(
                        select(FolderRecord).where(
                            col(FolderRecord.mailbox) == mailbox_key,
                            col(FolderRecord.well_known_name).is_not(None),
                        )
                    ).all()
                    for row in stale_rows:
                        row.well_known_name = None
                        session.add(row)

                for well_known_name, folder_id in (well_known_folder_ids or {}).items():
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

    def apply_contact_changes(
        self,
        mailbox: str,
        folder_id: str,
        changes: Sequence[ContactChange],
        contacts: Mapping[str, Contact],
        sync_state: str,
        *,
        reset: bool,
    ) -> ContactSyncCounts:
        """Apply one folder's contact changes and state in one transaction."""
        mailbox_key = _mailbox_key(mailbox)
        created = updated = deleted_count = 0
        try:
            with self._session() as session:
                if reset:
                    session.exec(
                        delete(ContactRecord).where(
                            col(ContactRecord.mailbox) == mailbox_key,
                            col(ContactRecord.parent_folder_id) == folder_id,
                        )
                    )
                for change in changes:
                    existing = session.get(ContactRecord, (mailbox_key, change.contact_id))
                    if change.kind is ContactChangeKind.DELETE:
                        if existing is not None:
                            session.delete(existing)
                            deleted_count += 1
                    else:
                        contact = contacts.get(change.contact_id)
                        if contact is None:
                            raise MailboxStoreError(
                                f"Fetched contact is missing: {change.contact_id}"
                            )
                        session.merge(_contact_row(mailbox_key, contact))
                        if change.kind is ContactChangeKind.CREATE:
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
            raise MailboxStoreError("Unable to apply contact synchronization") from error
        return ContactSyncCounts(created=created, updated=updated, deleted=deleted_count)

    def replace_folders(self, mailbox: str, folders: Sequence[Folder]) -> None:
        """Atomically replace one mailbox's complete folder snapshot."""
        changes = [
            FolderChange(
                kind=FolderChangeKind.CREATE,
                folder_id=folder.id,
                folder=folder,
                folder_kind=FolderKind.MAIL,
            )
            for folder in folders
        ]
        self.apply_folder_changes(mailbox, changes, "snapshot", reset=True)

    def list_folders(self, mailbox: str, kind: FolderKind = FolderKind.MAIL) -> list[Folder]:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                rows = session.exec(
                    select(FolderRecord)
                    .where(
                        col(FolderRecord.mailbox) == mailbox_key,
                        col(FolderRecord.kind) == kind.value,
                    )
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
                folder_id = self._resolve_folder_id(
                    session, mailbox_key, query.folder, FolderKind.MAIL
                )
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

    def folder_exists(self, mailbox: str, folder: str, kind: FolderKind = FolderKind.MAIL) -> bool:
        return self.resolve_folder_id(mailbox, folder, kind) is not None

    def resolve_folder_id(
        self, mailbox: str, folder: str, kind: FolderKind = FolderKind.MAIL
    ) -> str | None:
        """Resolve a folder selector to its cached EWS folder ID."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                return self._resolve_folder_id(session, mailbox_key, folder, kind)
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

    def list_contacts(self, mailbox: str, query: ContactListQuery) -> tuple[list[Contact], bool]:
        """Return a locally filtered contact page ordered by display name."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                statement = select(ContactRecord).where(col(ContactRecord.mailbox) == mailbox_key)
                if query.folder is not None:
                    folder_id = self._resolve_folder_id(
                        session, mailbox_key, query.folder, FolderKind.CONTACTS
                    )
                    if folder_id is None:
                        return [], False
                    statement = statement.where(col(ContactRecord.parent_folder_id) == folder_id)
                if query.search is not None:
                    statement = statement.where(
                        col(ContactRecord.search_text).contains(
                            query.search.casefold(), autoescape=True
                        )
                    )
                rows = session.exec(
                    statement.order_by(
                        func.lower(
                            func.coalesce(ContactRecord.file_as, ContactRecord.display_name)
                        ),
                        col(ContactRecord.id),
                    )
                    .offset(query.offset)
                    .limit(query.limit + 1)
                ).all()
                folder_names = self._folder_names(session, mailbox_key)
                return (
                    [
                        _contact_from_row(row, folder_names.get(row.parent_folder_id))
                        for row in rows[: query.limit]
                    ],
                    len(rows) > query.limit,
                )
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached contact data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to list cached contacts") from error

    def get_contact(self, mailbox: str, contact_id: str) -> Contact | None:
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                row = session.get(ContactRecord, (mailbox_key, contact_id))
                if row is None:
                    return None
                folder_names = self._folder_names(session, mailbox_key)
                return _contact_from_row(row, folder_names.get(row.parent_folder_id))
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached contact data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached contact") from error

    def count_thread(self, mailbox: str, conversation_id: str) -> int:
        """Count every cached message of one conversation across all folders."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                return int(
                    session.exec(
                        select(func.count())
                        .select_from(MessageRecord)
                        .where(
                            col(MessageRecord.mailbox) == mailbox_key,
                            col(MessageRecord.conversation_id) == conversation_id,
                        )
                    ).one()
                )
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to count cached conversation") from error

    def list_thread(
        self, mailbox: str, conversation_id: str, *, offset: int, limit: int
    ) -> tuple[list[MessageDetail], bool]:
        """Return one chronological page of a conversation's cached messages."""
        mailbox_key = _mailbox_key(mailbox)
        try:
            with self._session() as session:
                rows = session.exec(
                    select(MessageRecord)
                    .where(
                        col(MessageRecord.mailbox) == mailbox_key,
                        col(MessageRecord.conversation_id) == conversation_id,
                    )
                    .order_by(col(MessageRecord.received_at), col(MessageRecord.id))
                    .offset(offset)
                    .limit(limit + 1)
                ).all()
                return (
                    [_message_from_row(row) for row in rows[:limit]],
                    len(rows) > limit,
                )
        except (TypeError, ValueError, ValidationError) as error:
            raise MailboxStoreError("Cached message data is invalid") from error
        except SQLAlchemyError as error:
            raise MailboxStoreError("Unable to read cached conversation") from error

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
    def _resolve_folder_id(
        session: Session, mailbox: str, folder: str, kind: FolderKind
    ) -> str | None:
        row = session.exec(
            select(FolderRecord).where(
                col(FolderRecord.mailbox) == mailbox,
                col(FolderRecord.kind) == kind.value,
                func.lower(col(FolderRecord.well_known_name)) == folder.casefold(),
            )
        ).first()
        if row is not None:
            return row.id
        candidate = session.get(FolderRecord, (mailbox, folder))
        if candidate is not None and candidate.kind == kind.value:
            return folder
        return None

    @staticmethod
    def _folder_names(session: Session, mailbox: str) -> dict[str, str]:
        rows = session.exec(select(FolderRecord).where(col(FolderRecord.mailbox) == mailbox)).all()
        return {row.id: row.name for row in rows}

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
        session.exec(
            delete(ContactRecord).where(
                col(ContactRecord.mailbox) == mailbox,
                col(ContactRecord.parent_folder_id) == folder_id,
            )
        )


def _mailbox_key(mailbox: str) -> str:
    value = mailbox.strip().casefold()
    if not value:
        raise ValueError("mailbox must not be empty")
    return value


def _folder_row(mailbox: str, position: int, folder: Folder, kind: FolderKind) -> FolderRecord:
    return FolderRecord(
        mailbox=mailbox,
        id=folder.id,
        position=position,
        parent_id=folder.parent_id,
        name=folder.name,
        well_known_name=folder.well_known_name,
        kind=kind.value,
        total_count=folder.total_count,
        unread_count=folder.unread_count,
    )


def _contact_row(mailbox: str, contact: Contact) -> ContactRecord:
    return ContactRecord(
        mailbox=mailbox,
        id=contact.id,
        change_key=contact.change_key,
        parent_folder_id=contact.parent_folder_id,
        display_name=contact.display_name,
        file_as=contact.file_as,
        given_name=contact.given_name,
        middle_name=contact.middle_name,
        surname=contact.surname,
        nickname=contact.nickname,
        initials=contact.initials,
        generation=contact.generation,
        company_name=contact.company_name,
        department=contact.department,
        job_title=contact.job_title,
        office=contact.office,
        manager=contact.manager,
        profession=contact.profession,
        business_homepage=contact.business_homepage,
        emails_json=_CONTACT_EMAIL_LIST_ADAPTER.dump_json(contact.emails).decode(),
        phones_json=_CONTACT_PHONE_LIST_ADAPTER.dump_json(contact.phones).decode(),
        addresses_json=_CONTACT_ADDRESS_LIST_ADAPTER.dump_json(contact.addresses).decode(),
        im_addresses_json=_CONTACT_IM_LIST_ADAPTER.dump_json(contact.im_addresses).decode(),
        categories_json=_CATEGORY_LIST_ADAPTER.dump_json(contact.categories).decode(),
        notes=contact.notes,
        birthday=contact.birthday.isoformat() if contact.birthday is not None else None,
        has_picture=contact.has_picture,
        search_text=_contact_search_text(contact),
    )


def _contact_from_row(row: ContactRecord, folder_name: str | None) -> Contact:
    return Contact(
        id=row.id,
        change_key=row.change_key,
        parent_folder_id=row.parent_folder_id,
        folder_name=folder_name,
        display_name=row.display_name,
        file_as=row.file_as,
        given_name=row.given_name,
        middle_name=row.middle_name,
        surname=row.surname,
        nickname=row.nickname,
        initials=row.initials,
        generation=row.generation,
        company_name=row.company_name,
        department=row.department,
        job_title=row.job_title,
        office=row.office,
        manager=row.manager,
        profession=row.profession,
        business_homepage=row.business_homepage,
        emails=_CONTACT_EMAIL_LIST_ADAPTER.validate_json(row.emails_json),
        phones=_CONTACT_PHONE_LIST_ADAPTER.validate_json(row.phones_json),
        addresses=_CONTACT_ADDRESS_LIST_ADAPTER.validate_json(row.addresses_json),
        im_addresses=_CONTACT_IM_LIST_ADAPTER.validate_json(row.im_addresses_json),
        categories=_CATEGORY_LIST_ADAPTER.validate_json(row.categories_json),
        notes=row.notes,
        birthday=date.fromisoformat(row.birthday) if row.birthday is not None else None,
        has_picture=row.has_picture,
    )


def _contact_search_text(contact: Contact) -> str:
    parts = [
        contact.display_name,
        contact.file_as,
        contact.company_name,
        contact.department,
        contact.job_title,
        *(email.address for email in contact.emails),
    ]
    return " ".join(part.casefold() for part in parts if part)


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
        conversation_id=message.conversation_id,
        conversation_topic=message.conversation_topic,
        conversation_index=message.conversation_index,
        conversation_depth=message.conversation_depth,
        text_body=message.text_body,
        references=message.references,
        is_draft=message.is_draft,
        categories_json=_CATEGORY_LIST_ADAPTER.dump_json(message.categories).decode(),
        flag_status=message.flag_status.value,
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
        conversation_id=row.conversation_id,
        conversation_topic=row.conversation_topic,
        conversation_index=row.conversation_index,
        conversation_depth=row.conversation_depth,
        is_draft=row.is_draft,
        categories=_CATEGORY_LIST_ADAPTER.validate_json(row.categories_json),
        flag_status=FlagStatus(row.flag_status),
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
            "conversation_id": row.conversation_id,
            "conversation_topic": row.conversation_topic,
            "conversation_index": row.conversation_index,
            "conversation_depth": row.conversation_depth,
            "is_draft": row.is_draft,
            "categories": _CATEGORY_LIST_ADAPTER.validate_json(row.categories_json),
            "flag_status": row.flag_status,
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
            "text_body": row.text_body,
            "references": row.references,
        }
    )
