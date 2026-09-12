from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

from ews.models import (
    Folder,
    FolderChange,
    FolderChangeKind,
    MessageChange,
    MessageChangeKind,
    MessageDetail,
    MessageListQuery,
)
from ews.storage import (
    MailboxCacheNotReadyError,
    MailboxStoreError,
    SqliteMailboxStore,
    UnsupportedCacheSchemaVersionError,
    default_cache_path,
)
from ews.storage.sqlite import CacheMetadataRecord, MessageRecord


def test_default_cache_path_uses_config_directory(tmp_path: Path) -> None:
    assert default_cache_path(tmp_path) == tmp_path / ".config" / "taskseed" / "ews" / "cache.db"


def test_initialize_is_explicit_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "cache.db"
    store = SqliteMailboxStore(path)

    with pytest.raises(MailboxStoreError, match="not initialized"):
        store.list_folders("agent@example.com")
    assert not path.exists()

    store.initialize()
    store.initialize()

    with _session(store) as session:
        metadata = session.get(CacheMetadataRecord, "schema_version")
        assert metadata is not None
        assert metadata.value == "2"


def test_initialize_rejects_unknown_schema_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _set_schema_version(store, "3")

    with pytest.raises(UnsupportedCacheSchemaVersionError, match="version: 3"):
        store.initialize()


def test_initialize_migrates_v1_without_marking_mailbox_ready(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _set_schema_version(store, "1")

    store.initialize()

    with _session(store) as session:
        metadata = session.get(CacheMetadataRecord, "schema_version")
        assert metadata is not None
        assert metadata.value == "2"
    with pytest.raises(MailboxCacheNotReadyError):
        store.require_ready("agent@example.com")


def test_replace_folders_preserves_order_and_isolates_mailboxes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inbox = _folder("inbox-id", "Inbox")
    archive = _folder("archive-id", "Archive", parent_id="inbox-id")
    other = _folder("other-id", "Other")

    store.replace_folders("Agent@Example.com", [archive, inbox])
    store.replace_folders("other@example.com", [other])

    assert store.list_folders("agent@example.COM") == [archive, inbox]
    assert store.list_folders("other@example.com") == [other]

    store.replace_folders("AGENT@example.com", [])

    assert store.list_folders("agent@example.com") == []
    assert store.list_folders("other@example.com") == [other]


def test_message_round_trip_and_complete_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _message()

    store.upsert_messages("Agent@Example.com", [original])

    assert store.get_message("agent@example.COM", original.id) == original

    updated = original.model_copy(
        update={
            "change_key": "change-2",
            "subject": None,
            "is_read": True,
            "body": original.body.model_copy(update={"content": "Updated"}),
        }
    )
    store.upsert_messages("agent@example.com", [updated])

    assert store.get_message("AGENT@example.com", original.id) == updated


def test_message_ids_are_isolated_by_mailbox_and_misses_return_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _message(subject="First")
    second = _message(subject="Second")

    store.upsert_messages("first@example.com", [first])
    store.upsert_messages("second@example.com", [second])

    assert store.get_message("first@example.com", first.id) == first
    assert store.get_message("second@example.com", second.id) == second
    assert store.get_message("first@example.com", "missing") is None


def test_replacing_folders_does_not_delete_messages(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message = _message()
    store.upsert_messages("agent@example.com", [message])

    store.replace_folders("agent@example.com", [])

    assert store.get_message("agent@example.com", message.id) == message


def test_message_batch_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    accepted = _message(message_id="accepted-id")
    rejected = _message(message_id="rejected-id")

    def reject_commit(self: Session) -> None:
        del self
        raise SQLAlchemyError("rejected for test")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", reject_commit)
        with pytest.raises(MailboxStoreError, match="update cached messages"):
            store.upsert_messages("agent@example.com", [accepted, rejected])

    assert store.get_message("agent@example.com", accepted.id) is None


def test_get_message_rejects_corrupt_nested_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    message = _message()
    store.upsert_messages("agent@example.com", [message])
    with _session(store) as session:
        row = session.get(MessageRecord, ("agent@example.com", message.id))
        assert row is not None
        row.attachments_json = "not-json"
        session.add(row)
        session.commit()

    with pytest.raises(MailboxStoreError, match="data is invalid"):
        store.get_message("agent@example.com", message.id)


def test_operations_reject_unknown_schema_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _set_schema_version(store, "3")

    with pytest.raises(UnsupportedCacheSchemaVersionError, match="version: 3"):
        store.list_folders("agent@example.com")


def test_mailbox_must_not_be_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="must not be empty"):
        store.list_folders("  ")


def test_local_query_combines_filters_normalizes_utc_and_uses_limit_plus_one(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.replace_folders("agent@example.com", [_folder("inbox-id", "Inbox")])
    matching = _message(message_id="matching", subject="Quarterly Report")
    later = matching.model_copy(
        update={
            "id": "later",
            "received_at": datetime.fromisoformat("2026-09-12T11:00:00+00:00"),
        }
    )
    ignored = matching.model_copy(update={"id": "ignored", "is_read": True, "subject": "Other"})
    store.upsert_messages("agent@example.com", [matching, later, ignored])

    query = MessageListQuery.model_validate(
        {
            "folder": "inbox",
            "read_state": "unread",
            "sender": "FROM@example.com",
            "subject_contains": "Report",
            "body_contains": "Body",
            "received_from": "2026-09-12T07:00:00+00:00",
            "received_before": "2026-09-12T12:00:00+00:00",
            "limit": 1,
        }
    )
    messages, has_more = store.list_messages("agent@example.com", query)

    assert [message.id for message in messages] == ["later"]
    assert has_more is True
    assert messages[0].received_at.isoformat() == "2026-09-12T11:00:00+00:00"


def test_sync_states_changes_and_folder_delete_are_atomic_by_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    folder = _folder("inbox-id", "Inbox")
    folder_counts = store.apply_folder_changes(
        "agent@example.com",
        [FolderChange(kind=FolderChangeKind.CREATE, folder_id=folder.id, folder=folder)],
        "hierarchy-state",
        reset=True,
    )
    message = _message()
    created = store.apply_message_changes(
        "agent@example.com",
        folder.id,
        [
            MessageChange(
                kind=MessageChangeKind.CREATE,
                message_id=message.id,
                change_key=message.change_key,
            )
        ],
        {message.id: message},
        "item-state",
        reset=True,
    )
    changed = store.apply_message_changes(
        "agent@example.com",
        folder.id,
        [
            MessageChange(
                kind=MessageChangeKind.READ_FLAG_CHANGE,
                message_id=message.id,
                is_read=True,
            )
        ],
        {},
        "item-state-2",
        reset=False,
    )

    assert folder_counts.created == 1
    assert created.created == 1
    assert changed.read_state_changed == 1
    assert store.get_hierarchy_sync_state("agent@example.com") == "hierarchy-state"
    assert store.get_item_sync_state("agent@example.com", folder.id) == "item-state-2"
    cached = store.get_message("agent@example.com", message.id)
    assert cached is not None
    assert cached.is_read is True

    deleted = store.apply_folder_changes(
        "agent@example.com",
        [FolderChange(kind=FolderChangeKind.DELETE, folder_id=folder.id)],
        "hierarchy-state-2",
        reset=False,
    )
    assert deleted.deleted == 1
    assert store.get_message("agent@example.com", message.id) is None
    assert store.get_item_sync_state("agent@example.com", folder.id) is None


def test_folder_sync_repairs_well_known_inbox_mapping_without_counting_a_change(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    folder = _folder("inbox-id", "Localized inbox")
    store.replace_folders("agent@example.com", [folder])

    counts = store.apply_folder_changes(
        "agent@example.com",
        [],
        "hierarchy-state",
        reset=False,
        well_known_folder_ids={"inbox": folder.id},
    )

    assert counts.created == 0
    assert counts.updated == 0
    assert counts.deleted == 0
    assert store.folder_exists("agent@example.com", "inbox") is True
    assert store.list_folders("agent@example.com")[0].well_known_name == "inbox"


def test_failed_message_application_does_not_advance_item_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    folder = _folder("inbox-id", "Inbox")
    store.replace_folders("agent@example.com", [folder])

    with pytest.raises(MailboxStoreError, match="Fetched message is missing"):
        store.apply_message_changes(
            "agent@example.com",
            folder.id,
            [
                MessageChange(
                    kind=MessageChangeKind.UPDATE,
                    message_id="missing",
                    change_key="change-key",
                )
            ],
            {},
            "must-not-be-saved",
            reset=False,
        )

    assert store.get_item_sync_state("agent@example.com", folder.id) is None


def test_update_and_delete_message_changes_are_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    folder = _folder("inbox-id", "Inbox")
    store.replace_folders("agent@example.com", [folder])
    original = _message()
    store.upsert_messages("agent@example.com", [original])
    updated = original.model_copy(update={"change_key": "change-2", "subject": "Updated"})

    counts = store.apply_message_changes(
        "agent@example.com",
        folder.id,
        [
            MessageChange(
                kind=MessageChangeKind.UPDATE,
                message_id=updated.id,
                change_key=updated.change_key,
            )
        ],
        {updated.id: updated},
        "state-1",
        reset=False,
    )
    deleted = store.apply_message_changes(
        "agent@example.com",
        folder.id,
        [MessageChange(kind=MessageChangeKind.DELETE, message_id=updated.id)],
        {},
        "state-2",
        reset=False,
    )
    repeated = store.apply_message_changes(
        "agent@example.com",
        folder.id,
        [MessageChange(kind=MessageChangeKind.DELETE, message_id=updated.id)],
        {},
        "state-3",
        reset=False,
    )

    assert counts.updated == 1
    assert deleted.deleted == 1
    assert repeated.deleted == 0
    assert store.get_message("agent@example.com", updated.id) is None


def _store(tmp_path: Path) -> SqliteMailboxStore:
    store = SqliteMailboxStore(tmp_path / "cache.db")
    store.initialize()
    return store


def _set_schema_version(store: SqliteMailboxStore, version: str) -> None:
    with _session(store) as session:
        metadata = session.get(CacheMetadataRecord, "schema_version")
        assert metadata is not None
        metadata.value = version
        session.add(metadata)
        session.commit()


def _session(store: SqliteMailboxStore) -> Session:
    database_url = URL.create("sqlite", database=str(store.path))
    engine = create_engine(database_url, poolclass=NullPool)
    return Session(engine)


def _folder(folder_id: str, name: str, *, parent_id: str | None = None) -> Folder:
    return Folder(
        id=folder_id,
        parent_id=parent_id,
        name=name,
        well_known_name=name.casefold() if parent_id is None else None,
        total_count=12,
        unread_count=3,
    )


def _message(*, message_id: str = "message-id", subject: str | None = "Report") -> MessageDetail:
    return MessageDetail.model_validate(
        {
            "id": message_id,
            "change_key": "change-1",
            "parent_folder_id": "inbox-id",
            "subject": subject,
            "from_address": "from@example.com",
            "received_at": "2026-09-12T10:30:00+02:00",
            "is_read": False,
            "has_attachments": True,
            "importance": "high",
            "sender": {"name": "Sender", "address": "sender@example.com"},
            "to": [{"name": "Agent", "address": "agent@example.com"}],
            "cc": [{"name": None, "address": "cc@example.com"}],
            "bcc": [],
            "reply_to": [{"name": "Reply", "address": "reply@example.com"}],
            "sent_at": "2026-09-12T10:29:00+02:00",
            "created_at": None,
            "internet_message_id": "<message@example.com>",
            "in_reply_to": "<parent@example.com>",
            "body": {"content_type": "html", "content": "<p>Body</p>"},
            "internet_headers": [
                {"name": "X-Test", "value": "first"},
                {"name": "X-Test", "value": "second"},
            ],
            "attachments": [
                {
                    "id": "file-id",
                    "kind": "file",
                    "name": "report.pdf",
                    "content_type": "application/pdf",
                    "size": 123,
                    "is_inline": False,
                    "content_id": None,
                },
                {
                    "id": "item-id",
                    "kind": "item",
                    "name": "attached message",
                    "content_type": None,
                    "size": 456,
                    "is_inline": True,
                    "content_id": "inline-id",
                },
            ],
        }
    )
