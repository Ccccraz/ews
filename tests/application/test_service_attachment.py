from collections.abc import Sequence
from pathlib import Path

import keyring
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import (
    AttachmentNotFoundError,
    MailboxApplicationService,
    MessageNotFoundError,
    UnsupportedAttachmentError,
    UserNotFoundError,
)
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    DraftMessage,
    Folder,
    FolderSyncResult,
    MessageBody,
    MessageDetail,
    MessageDraftResult,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    MessageSyncResult,
    OutgoingMessage,
    OutgoingReply,
    Profile,
)
from ews.storage import MailboxCacheNotReadyError, SqliteMailboxStore

MAILBOX = "agent@example.com"
DESTINATION = Path("/tmp/attachment.bin")


class SaveGateway:
    """Records attachment save requests and reports a server-confirmed result."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, Path]] = []

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise AssertionError("Not used")

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        del profile, password, sync_state
        raise AssertionError("Not used")

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        del profile, password, folder_id, sync_state
        raise AssertionError("Not used")

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        del profile, password, message_ids
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
        assert password.get_secret_value() == "top-secret"
        self.saved.append((message_id, attachment_id, destination))
        return AttachmentSaveResult(
            user=profile.user.username,
            message_id=message_id,
            attachment_id=attachment_id,
            name="report.pdf",
            content_type="application/pdf",
            path=destination,
            bytes_written=7,
        )


def test_service_saves_a_cached_file_attachment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.save_attachment(MAILBOX, "message-id", "file-id", DESTINATION)

    assert gateway.saved == [("message-id", "file-id", DESTINATION)]
    assert result.name == "report.pdf"
    assert result.bytes_written == 7


def test_service_rejects_an_unknown_attachment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(AttachmentNotFoundError, match="Attachment not found: other"):
        service.save_attachment(MAILBOX, "message-id", "other", DESTINATION)

    assert gateway.saved == []


def test_service_rejects_an_item_attachment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(UnsupportedAttachmentError, match="Only file attachments"):
        service.save_attachment(MAILBOX, "message-id", "item-id", DESTINATION)

    assert gateway.saved == []


def test_service_rejects_an_unknown_message(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(MessageNotFoundError, match="Message not found: other"):
        service.save_attachment(MAILBOX, "other", "file-id", DESTINATION)


def test_service_requires_a_ready_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway, ready=False)

    with pytest.raises(MailboxCacheNotReadyError):
        service.save_attachment(MAILBOX, "message-id", "file-id", DESTINATION)

    assert gateway.saved == []


def test_service_rejects_a_different_user(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = SaveGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(UserNotFoundError):
        service.save_attachment("someone-else", "message-id", "file-id", DESTINATION)


def _service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: SaveGateway,
    *,
    ready: bool = True,
) -> MailboxApplicationService:
    profile_store = ProfileStore(tmp_path / "profile.toml")
    profile_store.save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": MAILBOX, "username": "DOMAIN\\agent"},
            }
        )
    )

    def get_password(service: str, username: str) -> str:
        del service, username
        return "top-secret"

    monkeypatch.setattr(keyring, "get_password", get_password)

    store = SqliteMailboxStore(tmp_path / "cache.db")
    if ready:
        store.initialize()
        store.replace_folders(
            MAILBOX,
            [
                Folder(
                    id="inbox-id",
                    parent_id=None,
                    name="Inbox",
                    well_known_name="inbox",
                    total_count=1,
                    unread_count=1,
                )
            ],
        )
        store.upsert_messages(MAILBOX, [_message()])
        store.mark_ready(MAILBOX)
    return MailboxApplicationService(profile_store, PasswordStore(), gateway, store)


def _message() -> MessageDetail:
    return MessageDetail.model_validate(
        {
            "id": "message-id",
            "change_key": "change-1",
            "parent_folder_id": "inbox-id",
            "subject": "Report",
            "from_address": "sender@example.com",
            "received_at": "2026-09-11T12:00:00+02:00",
            "is_read": True,
            "has_attachments": True,
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
            "body": MessageBody(content_type="text", content="Body"),
            "internet_headers": [],
            "attachments": [
                {
                    "id": "file-id",
                    "kind": "file",
                    "name": "report.pdf",
                    "content_type": "application/pdf",
                    "size": 7,
                    "is_inline": False,
                    "content_id": None,
                },
                {
                    "id": "item-id",
                    "kind": "item",
                    "name": "attached message",
                    "content_type": None,
                    "size": 12,
                    "is_inline": False,
                    "content_id": None,
                },
            ],
        }
    )
