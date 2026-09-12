from collections.abc import Sequence
from pathlib import Path

import keyring
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import InvalidSyncStateError, MailboxApplicationService
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    ConnectionTestResult,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderSyncResult,
    MailboxSyncResult,
    MessageBody,
    MessageChange,
    MessageChangeKind,
    MessageDetail,
    MessageListQuery,
    MessageSyncCounts,
    MessageSyncResult,
    Profile,
)
from ews.storage import SqliteMailboxStore


class SyncGateway:
    def __init__(self) -> None:
        self.hierarchy_states: list[str | None] = []
        self.item_states: list[str | None] = []
        self.fetch_sizes: list[int] = []
        self.invalidate_hierarchy = False
        self.invalidate_items = False

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise AssertionError("Not used")

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        del profile, password
        self.hierarchy_states.append(sync_state)
        if self.invalidate_hierarchy and sync_state is not None:
            self.invalidate_hierarchy = False
            raise InvalidSyncStateError("invalid")
        changes = [] if sync_state is not None else [_folder_change()]
        return FolderSyncResult(changes=changes, sync_state="hierarchy-state")

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        del profile, password
        assert folder_id == "inbox-id"
        self.item_states.append(sync_state)
        if self.invalidate_items and sync_state is not None:
            self.invalidate_items = False
            raise InvalidSyncStateError("invalid")
        changes = (
            [
                MessageChange(
                    kind=MessageChangeKind.CREATE,
                    message_id=f"message-{index}",
                    change_key=f"change-{index}",
                )
                for index in range(11)
            ]
            if sync_state is None
            else []
        )
        return MessageSyncResult(changes=changes, sync_state="item-state")

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        del profile, password
        self.fetch_sizes.append(len(message_ids))
        return [_message(message_id, change_key) for message_id, change_key in message_ids]


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def hierarchy_started(self) -> None:
        self.events.append(("hierarchy_started",))

    def hierarchy_completed(self, folder_total: int) -> None:
        self.events.append(("hierarchy_completed", folder_total))

    def folder_started(self, name: str, index: int, total: int) -> None:
        self.events.append(("folder_started", name, index, total))

    def message_fetch_started(self, total: int) -> None:
        self.events.append(("message_fetch_started", total))

    def messages_fetched(self, count: int) -> None:
        self.events.append(("messages_fetched", count))

    def folder_completed(self, counts: MessageSyncCounts) -> None:
        self.events.append(("folder_completed", counts.created))

    def sync_completed(self, result: MailboxSyncResult) -> None:
        self.events.append(("sync_completed", result.messages.created))


def test_sync_batches_get_item_and_reads_remain_local(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = SyncGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.sync("agent@example.com")

    assert result.folders.created == 1
    assert result.messages.created == 11
    assert gateway.fetch_sizes == [10, 1]
    assert gateway.hierarchy_states == [None]
    assert gateway.item_states == [None]

    def reject_password(service_name: str, username: str) -> str:
        del service_name, username
        raise AssertionError("Local reads must not access Keychain")

    monkeypatch.setattr(keyring, "get_password", reject_password)
    assert service.list_folders("DOMAIN\\agent").folders[0].id == "inbox-id"
    page = service.list_messages("DOMAIN\\agent", MessageListQuery(folder="inbox", limit=10))
    assert len(page.messages) == 10
    assert page.pagination.has_more is True
    assert service.get_message("DOMAIN\\agent", "message-0").message.id == "message-0"


def test_sync_reports_typed_progress_events(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SyncGateway()
    service = _service(tmp_path, monkeypatch, gateway)
    progress = RecordingProgress()

    service.sync("agent@example.com", progress=progress)

    assert progress.events == [
        ("hierarchy_started",),
        ("hierarchy_completed", 1),
        ("folder_started", "Inbox", 1, 1),
        ("message_fetch_started", 11),
        ("messages_fetched", 10),
        ("messages_fetched", 1),
        ("folder_completed", 11),
        ("sync_completed", 11),
    ]


def test_invalid_states_rebuild_only_the_affected_ranges(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = SyncGateway()
    service = _service(tmp_path, monkeypatch, gateway)
    service.sync("DOMAIN\\agent")
    gateway.invalidate_hierarchy = True
    gateway.invalidate_items = True

    result = service.sync("DOMAIN\\agent")

    assert gateway.hierarchy_states[-2:] == ["hierarchy-state", None]
    assert gateway.item_states[-2:] == ["item-state", None]
    assert result.messages.created == 11


def _service(
    tmp_path: Path, monkeypatch: MonkeyPatch, gateway: SyncGateway
) -> MailboxApplicationService:
    profile_store = ProfileStore(tmp_path / "profile.toml")
    profile_store.save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
            }
        )
    )

    def get_password(service: str, username: str) -> str:
        del service, username
        return "secret"

    monkeypatch.setattr(keyring, "get_password", get_password)
    return MailboxApplicationService(
        profile_store,
        PasswordStore(),
        gateway,
        SqliteMailboxStore(tmp_path / "cache.db"),
    )


def _folder_change() -> FolderChange:
    folder = Folder(
        id="inbox-id",
        parent_id=None,
        name="Inbox",
        well_known_name="inbox",
        total_count=11,
        unread_count=11,
    )
    return FolderChange(kind=FolderChangeKind.CREATE, folder_id=folder.id, folder=folder)


def _message(message_id: str, change_key: str) -> MessageDetail:
    return MessageDetail.model_validate(
        {
            "id": message_id,
            "change_key": change_key,
            "parent_folder_id": "inbox-id",
            "subject": f"Report {message_id}",
            "from_address": "sender@example.com",
            "received_at": "2026-09-12T10:00:00+02:00",
            "is_read": False,
            "has_attachments": False,
            "importance": "normal",
            "sender": None,
            "to": [],
            "cc": [],
            "bcc": [],
            "reply_to": [],
            "sent_at": None,
            "created_at": None,
            "internet_message_id": None,
            "in_reply_to": None,
            "body": MessageBody(content_type="text", content="deadline"),
            "internet_headers": [],
            "attachments": [],
        }
    )
