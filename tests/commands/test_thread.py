import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, cast

import keyring
import pytest
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture, MonkeyPatch

from ews.application import MailboxApplicationService
from ews.cli import app
from ews.commands.context import CommandContext
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    Contact,
    ContactSyncResult,
    DirectorySearchResult,
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
    OutgoingMessage,
    OutgoingReply,
    Profile,
)
from ews.storage import SqliteMailboxStore

MAILBOX = "agent@example.com"
CONVERSATION = "conversation-1"


class ThreadGateway:
    """The thread command never talks to EWS; any call would be a defect."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        return self._unexpected("test_access")

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        del profile, password, sync_state
        return self._unexpected("sync_hierarchy")

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        del profile, password, folder_id, sync_state
        return self._unexpected("sync_items")

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        del profile, password, message_ids
        return self._unexpected("fetch_messages")

    def sync_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> ContactSyncResult:
        del profile, password, folder_id, sync_state
        raise AssertionError("Not used")

    def fetch_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        contact_ids: Sequence[tuple[str, str]],
    ) -> list[Contact]:
        del profile, password, contact_ids
        raise AssertionError("Not used")

    def search_directory(
        self, profile: Profile, password: SecretStr, query: str
    ) -> DirectorySearchResult:
        del profile, password, query
        raise AssertionError("Not used")

    def send_message(
        self, profile: Profile, password: SecretStr, message: OutgoingMessage
    ) -> MessageSendResult:
        del profile, password, message
        return self._unexpected("send_message")

    def save_message_draft(
        self, profile: Profile, password: SecretStr, message: DraftMessage
    ) -> MessageDraftResult:
        del profile, password, message
        return self._unexpected("save_message_draft")

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
        return self._unexpected("reply_message")

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
        return self._unexpected("save_reply_draft")

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        del profile, password, message_id, is_read
        return self._unexpected("set_read_state")

    def move_message(
        self, profile: Profile, password: SecretStr, message_id: str, folder_id: str
    ) -> MessageMoveResult:
        del profile, password, message_id, folder_id
        return self._unexpected("move_message")

    def save_attachment(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        del profile, password, message_id, attachment_id, destination
        return self._unexpected("save_attachment")

    def _unexpected(self, call: str) -> NoReturn:
        self.calls.append(call)
        raise AssertionError(f"message thread must not call {call}")


def test_thread_returns_every_cached_message_with_folder_and_body(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "thread", "root"], capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    assert data["conversation_id"] == CONVERSATION
    assert data["conversation_topic"] == "mxbi project"
    assert data["message_count"] == 3
    messages = cast(list[JsonValue], data["messages"])
    folder_names = [cast(dict[str, JsonValue], message)["folder_name"] for message in messages]
    assert folder_names == ["longterm", "Sent Items", "Deleted Items"]
    first = cast(dict[str, JsonValue], messages[0])
    assert first["subject"] == "mxbi project"
    assert first["conversation_depth"] == 0
    assert first["is_draft"] is False
    assert first["categories"] == []
    assert first["flag_status"] == "none"
    assert first["body"] == {"content_type": "text", "content": "Body"}
    assert first["text_body"] == "Plain body"
    assert data["pagination"] == {
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "next_offset": None,
    }


def test_thread_paginates_with_limit_and_offset(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", MAILBOX, "message", "thread", "root", "--limit", "1", "--offset", "1"],
        capsys,
    )

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    messages = cast(list[JsonValue], data["messages"])
    assert [cast(dict[str, JsonValue], message)["id"] for message in messages] == ["reply"]
    assert data["message_count"] == 3
    assert data["pagination"] == {
        "offset": 1,
        "limit": 1,
        "has_more": True,
        "next_offset": 2,
    }


def test_thread_rejects_a_limit_above_the_maximum(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", MAILBOX, "message", "thread", "root", "--limit", "500"], capsys
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"


def test_thread_requires_a_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["message", "thread", "root"], capsys)

    assert exit_code == 2
    assert _error(output)["message"] == "--user is required"


def test_thread_reports_an_unknown_message(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "thread", "missing"], capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "resource_not_found"
    assert _error(output)["message"] == "Message not found: missing"


def test_thread_requires_a_ready_cache(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway, ready=False)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "thread", "root"], capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "cache_not_ready"


def test_thread_rejects_a_different_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", "other@example.com", "message", "thread", "root"], capsys
    )

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_thread_reads_only_the_local_cache(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ThreadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, _ = _invoke(["--user", MAILBOX, "message", "thread", "root"], capsys)

    assert exit_code == 0
    assert gateway.calls == []


def _configure_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: ThreadGateway,
    *,
    ready: bool = True,
) -> None:
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
            ],
        )
        store.mark_ready(MAILBOX)

    def build_context(user: str | None) -> CommandContext:
        service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)


def _message(
    message_id: str,
    *,
    folder_id: str,
    depth: int,
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
            "conversation_id": CONVERSATION,
            "conversation_topic": "mxbi project",
            "conversation_index": "01" * (22 + 5 * depth),
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


def _invoke(arguments: list[str], capsys: CaptureFixture[str]) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app(arguments)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])
