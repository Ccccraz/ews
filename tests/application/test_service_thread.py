from collections.abc import Sequence
from pathlib import Path

import keyring
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import (
    MailboxApplicationService,
    MessageNotFoundError,
    UserNotFoundError,
)
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    DraftMessage,
    FlagStatus,
    Folder,
    FolderSyncResult,
    MessageBody,
    MessageDetail,
    MessageDraftResult,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    MessageSyncResult,
    MessageThreadQuery,
    OutgoingMessage,
    OutgoingReply,
    Profile,
)
from ews.storage import MailboxCacheNotReadyError, SqliteMailboxStore

MAILBOX = "agent@example.com"
CONVERSATION = "conversation-1"


class UnusedGateway:
    """The thread use case never talks to EWS."""

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
        del profile, password, message_id, attachment_id, destination
        raise AssertionError("Not used")


def test_service_returns_the_whole_conversation_in_reading_order(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)

    result = service.get_thread(MAILBOX, "root", MessageThreadQuery.model_validate({}))

    assert result.conversation_id == CONVERSATION
    assert result.conversation_topic == "mxbi project"
    assert result.message_count == 3
    assert [message.id for message in result.messages] == ["root", "reply", "deleted"]
    assert [message.folder_name for message in result.messages] == [
        "longterm",
        "Sent Items",
        "Deleted Items",
    ]
    assert result.messages[0].conversation_depth == 0
    assert result.messages[1].conversation_depth == 1
    assert result.messages[1].body.content == "Body"
    assert result.messages[1].text_body == "Plain body"
    assert result.pagination.model_dump() == {
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "next_offset": None,
    }


def test_service_paginates_a_conversation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)

    result = service.get_thread(
        MAILBOX, "root", MessageThreadQuery.model_validate({"offset": 1, "limit": 1})
    )

    assert [message.id for message in result.messages] == ["reply"]
    assert result.message_count == 3
    assert result.pagination.has_more is True
    assert result.pagination.next_offset == 2


def test_service_returns_a_single_message_without_a_conversation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    store = service_store(tmp_path)
    store.upsert_messages(MAILBOX, [_message("lone", conversation_id=None, depth=None)])

    result = service.get_thread(MAILBOX, "lone", MessageThreadQuery.model_validate({}))

    assert result.conversation_id is None
    assert result.conversation_topic is None
    assert result.message_count == 1
    assert [message.id for message in result.messages] == ["lone"]


def test_service_rejects_an_unknown_message(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)

    with pytest.raises(MessageNotFoundError, match="Message not found: missing"):
        service.get_thread(MAILBOX, "missing", MessageThreadQuery.model_validate({}))


def test_service_requires_a_ready_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch, ready=False)

    with pytest.raises(MailboxCacheNotReadyError):
        service.get_thread(MAILBOX, "root", MessageThreadQuery.model_validate({}))


def test_service_rejects_a_different_user(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)

    with pytest.raises(UserNotFoundError):
        service.get_thread("someone-else", "root", MessageThreadQuery.model_validate({}))


def service_store(tmp_path: Path) -> SqliteMailboxStore:
    return SqliteMailboxStore(tmp_path / "cache.db")


def _service(
    tmp_path: Path, monkeypatch: MonkeyPatch, *, ready: bool = True
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

    store = service_store(tmp_path)
    if ready:
        store.initialize()
        store.replace_folders(
            MAILBOX,
            [
                Folder(
                    id="longterm-id",
                    parent_id=None,
                    name="longterm",
                    well_known_name="msgfolderroot",
                    total_count=1,
                    unread_count=0,
                ),
                Folder(
                    id="sent-id",
                    parent_id=None,
                    name="Sent Items",
                    well_known_name="sentitems",
                    total_count=1,
                    unread_count=0,
                ),
                Folder(
                    id="deleted-id",
                    parent_id=None,
                    name="Deleted Items",
                    well_known_name="deleteditems",
                    total_count=1,
                    unread_count=0,
                ),
            ],
        )
        store.upsert_messages(
            MAILBOX,
            [
                _message("root", folder_id="longterm-id", depth=0),
                _message(
                    "reply",
                    folder_id="sent-id",
                    depth=1,
                    received_at="2026-05-05T13:18:08+00:00",
                ),
                _message(
                    "deleted",
                    folder_id="deleted-id",
                    depth=2,
                    received_at="2026-05-17T21:23:09+00:00",
                ),
                _message("outsider", conversation_id="conversation-2", depth=0),
            ],
        )
        store.mark_ready(MAILBOX)
    return MailboxApplicationService(profile_store, PasswordStore(), UnusedGateway(), store)


def _message(
    message_id: str,
    *,
    folder_id: str = "longterm-id",
    conversation_id: str | None = CONVERSATION,
    depth: int | None = 0,
    received_at: str = "2026-05-05T13:11:26+00:00",
) -> MessageDetail:
    return MessageDetail.model_validate(
        {
            "id": message_id,
            "change_key": f"change-{message_id}",
            "parent_folder_id": folder_id,
            "subject": "mxbi project",
            "from_address": "sender@example.com",
            "received_at": received_at,
            "is_read": True,
            "has_attachments": False,
            "importance": "normal",
            "conversation_id": conversation_id,
            "conversation_topic": None if conversation_id is None else "mxbi project",
            "conversation_index": None if depth is None else "01" * (22 + 5 * depth),
            "conversation_depth": depth,
            "is_draft": False,
            "categories": [],
            "flag_status": FlagStatus.NONE,
            "sender": None,
            "to": [],
            "cc": [],
            "bcc": [],
            "reply_to": [],
            "sent_at": None,
            "created_at": None,
            "internet_message_id": f"<{message_id}@example.com>",
            "in_reply_to": None,
            "body": MessageBody(content_type="text", content="Body"),
            "internet_headers": [],
            "attachments": [],
            "text_body": "Plain body",
            "references": None,
        }
    )
