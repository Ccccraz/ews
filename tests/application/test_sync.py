from collections.abc import Sequence
from pathlib import Path

import keyring
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import InvalidSyncStateError, MailboxApplicationService
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    Contact,
    ContactChange,
    ContactChangeKind,
    ContactEmail,
    ContactSyncCounts,
    ContactSyncResult,
    DirectorySearchResult,
    DraftMessage,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderKind,
    FolderSyncResult,
    MailboxSyncResult,
    MessageBody,
    MessageChange,
    MessageChangeKind,
    MessageDetail,
    MessageDraftResult,
    MessageListQuery,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    MessageSyncCounts,
    MessageSyncResult,
    OutgoingMessage,
    OutgoingReply,
    Profile,
)
from ews.storage import SqliteMailboxStore


class SyncGateway:
    def __init__(self) -> None:
        self.hierarchy_states: list[str | None] = []
        self.item_states: list[str | None] = []
        self.contact_states: list[str | None] = []
        self.fetch_sizes: list[int] = []
        self.contact_fetch_sizes: list[int] = []
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
        changes = [] if sync_state is not None else [_folder_change(), _contact_folder_change()]
        return FolderSyncResult(
            changes=changes,
            sync_state="hierarchy-state",
            well_known_folder_ids={"inbox": "inbox-id", "contacts": "contacts-id"},
        )

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

    def sync_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> ContactSyncResult:
        del profile, password
        assert folder_id == "contacts-id"
        self.contact_states.append(sync_state)
        changes = (
            [
                ContactChange(
                    kind=ContactChangeKind.CREATE,
                    contact_id=f"contact-{index}",
                    change_key=f"contact-change-{index}",
                )
                for index in range(3)
            ]
            if sync_state is None
            else []
        )
        return ContactSyncResult(changes=changes, sync_state="contact-state")

    def fetch_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        contact_ids: Sequence[tuple[str, str]],
    ) -> list[Contact]:
        del profile, password
        self.contact_fetch_sizes.append(len(contact_ids))
        return [_contact(contact_id, change_key) for contact_id, change_key in contact_ids]

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        del profile, password
        self.fetch_sizes.append(len(message_ids))
        return [_message(message_id, change_key) for message_id, change_key in message_ids]

    def search_directory(
        self, profile: Profile, password: SecretStr, query: str
    ) -> DirectorySearchResult:
        del profile, password, query
        raise AssertionError("Not used")

    def send_message(
        self, profile: Profile, password: SecretStr, message: OutgoingMessage
    ) -> MessageSendResult:
        del profile, password, message
        raise AssertionError("Not used")

    def save_message_draft(
        self, profile: Profile, password: SecretStr, message: DraftMessage
    ) -> MessageDraftResult:
        del profile, password, message
        raise AssertionError("Not used")

    def reply_message(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageSendResult:
        del profile, password, message_id, reply, reply_all
        raise AssertionError("Not used")

    def save_reply_draft(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageDraftResult:
        del profile, password, message_id, reply, reply_all
        raise AssertionError("Not used")

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        del profile, password, message_id, is_read
        raise AssertionError("Not used")

    def move_message(
        self, profile: Profile, password: SecretStr, message_id: str, folder_id: str
    ) -> MessageMoveResult:
        del profile, password, message_id, folder_id
        raise AssertionError("Not used")

    def save_attachment(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        del profile, password, message_id, attachment_id, destination
        raise AssertionError("Not used")


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

    def folder_completed(self, counts: MessageSyncCounts | ContactSyncCounts) -> None:
        if isinstance(counts, ContactSyncCounts):
            self.events.append(("contact_folder_completed", counts.created))
        else:
            self.events.append(("folder_completed", counts.created))

    def sync_completed(self, result: MailboxSyncResult) -> None:
        self.events.append(("sync_completed", result.messages.created, result.contacts.created))


def test_sync_batches_get_item_and_reads_remain_local(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = SyncGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.sync("agent@example.com")

    assert result.folders.created == 2
    assert result.messages.created == 11
    assert result.contacts.created == 3
    assert gateway.fetch_sizes == [10, 1]
    assert gateway.contact_fetch_sizes == [3]
    assert gateway.hierarchy_states == [None]
    assert gateway.item_states == [None]
    assert gateway.contact_states == [None]

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
        ("hierarchy_completed", 2),
        ("folder_started", "Inbox", 1, 2),
        ("message_fetch_started", 11),
        ("messages_fetched", 10),
        ("messages_fetched", 1),
        ("folder_completed", 11),
        ("folder_started", "Contacts", 2, 2),
        ("contact_folder_completed", 3),
        ("sync_completed", 11, 3),
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
    return FolderChange(
        kind=FolderChangeKind.CREATE,
        folder_id=folder.id,
        folder=folder,
        folder_kind=FolderKind.MAIL,
    )


def _contact_folder_change() -> FolderChange:
    folder = Folder(
        id="contacts-id",
        parent_id=None,
        name="Contacts",
        well_known_name="contacts",
        total_count=3,
        unread_count=0,
    )
    return FolderChange(
        kind=FolderChangeKind.CREATE,
        folder_id=folder.id,
        folder=folder,
        folder_kind=FolderKind.CONTACTS,
    )


def _contact(contact_id: str, change_key: str) -> Contact:
    return Contact(
        id=contact_id,
        change_key=change_key,
        parent_folder_id="contacts-id",
        display_name=f"Contact {contact_id}",
        emails=[
            ContactEmail(label="EmailAddress1", address=f"{contact_id}@example.com"),
        ],
    )


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
