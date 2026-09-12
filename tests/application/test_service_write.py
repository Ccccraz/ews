from collections.abc import Sequence
from pathlib import Path

import keyring
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import (
    FolderNotFoundError,
    MailboxApplicationService,
    MessageNotFoundError,
    UserNotFoundError,
)
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    ConnectionTestResult,
    Folder,
    FolderSyncResult,
    MessageBody,
    MessageDetail,
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


class WriteGateway:
    """Records write requests and returns server-confirmed identifiers."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []
        self.replies: list[tuple[str, OutgoingReply, bool]] = []
        self.read_states: list[tuple[str, bool]] = []
        self.moves: list[tuple[str, str]] = []

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
        assert password.get_secret_value() == "top-secret"
        self.sent.append(message)
        return _send_result(profile, message.subject)

    def reply_message(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageSendResult:
        assert password.get_secret_value() == "top-secret"
        self.replies.append((message_id, reply, reply_all))
        return _send_result(profile, "RE: Report")

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        assert password.get_secret_value() == "top-secret"
        self.read_states.append((message_id, is_read))
        return MessageReadStateResult(
            user=profile.user.username,
            message_id=message_id,
            change_key="change-2",
            is_read=is_read,
        )

    def move_message(
        self, profile: Profile, password: SecretStr, message_id: str, folder_id: str
    ) -> MessageMoveResult:
        assert password.get_secret_value() == "top-secret"
        self.moves.append((message_id, folder_id))
        return MessageMoveResult(
            user=profile.user.username,
            previous_message_id=message_id,
            message_id="moved-id",
            change_key="moved-change-1",
            folder_id=folder_id,
        )


def test_service_sends_a_message_without_a_ready_cache(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway, ready=False)

    result = service.send_message("DOMAIN\\agent", _outgoing())

    assert gateway.sent == [_outgoing()]
    assert result.subject == "Report"


def test_service_replies_to_a_cached_message(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.reply_to_message(MAILBOX, "message-id", _reply(), reply_all=False)

    assert gateway.replies == [("message-id", _reply(), False)]
    assert result.subject == "RE: Report"


def test_service_reply_all_forwards_the_flag(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    service.reply_to_message(MAILBOX, "message-id", _reply(), reply_all=True)

    assert gateway.replies[0][2] is True


def test_service_updates_the_read_state_of_a_cached_message(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.set_read_state(MAILBOX, "message-id", is_read=False)

    assert gateway.read_states == [("message-id", False)]
    assert result.is_read is False


def test_service_moves_a_cached_message_to_a_well_known_folder(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    result = service.move_message(MAILBOX, "message-id", "sentitems")

    assert gateway.moves == [("message-id", "sent-id")]
    assert result.folder_id == "sent-id"


def test_service_rejects_a_write_for_an_unknown_folder(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(FolderNotFoundError, match="Folder not found: missing"):
        service.move_message(MAILBOX, "message-id", "missing")

    assert gateway.moves == []


def test_service_rejects_a_write_for_an_unknown_message(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(MessageNotFoundError, match="Message not found: other"):
        service.set_read_state(MAILBOX, "other", is_read=True)

    assert gateway.read_states == []


def test_service_requires_a_ready_cache_for_message_writes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway, ready=False)

    with pytest.raises(MailboxCacheNotReadyError):
        service.reply_to_message(MAILBOX, "message-id", _reply(), reply_all=False)


def test_service_rejects_a_write_for_a_different_user(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    service = _service(tmp_path, monkeypatch, gateway)

    with pytest.raises(UserNotFoundError):
        service.move_message("someone-else", "message-id", "sentitems")


def _service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: WriteGateway,
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
                ),
                Folder(
                    id="sent-id",
                    parent_id=None,
                    name="Sent Items",
                    well_known_name="sentitems",
                    total_count=1,
                    unread_count=0,
                ),
            ],
        )
        store.upsert_messages(MAILBOX, [_message()])
        store.mark_ready(MAILBOX)
    return MailboxApplicationService(profile_store, PasswordStore(), gateway, store)


def _send_result(profile: Profile, subject: str) -> MessageSendResult:
    return MessageSendResult(
        user=profile.user.username,
        subject=subject,
        to=[],
        cc=[],
        bcc=[],
    )


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
            "body": MessageBody(content_type="text", content="Body"),
            "internet_headers": [],
            "attachments": [],
        }
    )


def _outgoing() -> OutgoingMessage:
    return OutgoingMessage.model_validate(
        {
            "to": ["to@example.com"],
            "subject": "Report",
            "body": {"content_type": "text", "content": "Body"},
        }
    )


def _reply() -> OutgoingReply:
    return OutgoingReply.model_validate({"body": {"content_type": "text", "content": "Thanks"}})
