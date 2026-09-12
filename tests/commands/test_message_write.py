import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import keyring
import pytest
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture, MonkeyPatch

from ews.application import MailboxApplicationService
from ews.cli import app
from ews.commands.context import CommandContext
from ews.config import PasswordStore, ProfileStore
from ews.exchange import (
    EwsAuthenticationError,
    EwsNotFoundError,
    EwsRejectedError,
    EwsServiceError,
)
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    DraftMessage,
    Folder,
    FolderSyncResult,
    MailboxAddress,
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


class WriteGateway:
    """Returns server-confirmed write results and records requests."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.sent: list[OutgoingMessage] = []
        self.drafts: list[DraftMessage] = []
        self.replies: list[tuple[str, OutgoingReply, bool]] = []
        self.reply_drafts: list[tuple[str, OutgoingReply, bool]] = []
        self.read_states: list[tuple[str, bool]] = []
        self.moves: list[tuple[str, str]] = []
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
        del password
        self._raise_error()
        self.sent.append(message)
        return MessageSendResult(
            user=profile.user.username,
            subject=message.subject,
            to=[MailboxAddress(address=str(address)) for address in message.to],
            cc=[MailboxAddress(address=str(address)) for address in message.cc],
            bcc=[MailboxAddress(address=str(address)) for address in message.bcc],
        )

    def save_message_draft(
        self, profile: Profile, password: SecretStr, message: DraftMessage
    ) -> MessageDraftResult:
        del password
        self._raise_error()
        self.drafts.append(message)
        return _draft_result(profile, message.subject, message)

    def reply_message(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageSendResult:
        del password
        self._raise_error()
        self.replies.append((message_id, reply, reply_all))
        return MessageSendResult(
            user=profile.user.username,
            subject=reply.subject or "RE: Report",
            to=[MailboxAddress(address="author@example.com")],
            cc=[],
            bcc=[],
        )

    def save_reply_draft(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageDraftResult:
        del password
        self._raise_error()
        self.reply_drafts.append((message_id, reply, reply_all))
        return MessageDraftResult(
            user=profile.user.username,
            message_id="draft-id",
            change_key="draft-change-1",
            folder_id="drafts-id",
            subject=reply.subject or "RE: Report",
            to=[MailboxAddress(address="author@example.com")],
            cc=[],
            bcc=[],
        )

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        del password
        self._raise_error()
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
        del password
        self._raise_error()
        self.moves.append((message_id, folder_id))
        return MessageMoveResult(
            user=profile.user.username,
            previous_message_id=message_id,
            message_id="moved-id",
            change_key="moved-change-1",
            folder_id=folder_id,
        )

    def save_attachment(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        del password
        self._raise_error()
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

    def _raise_error(self) -> None:
        if self.error is not None:
            raise self.error


def test_send_parses_recipients_body_file_and_returns_the_contract(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    body_file = tmp_path / "body.txt"
    body_file.write_text("<p>Body</p>", encoding="utf-8")

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "send",
            "--to",
            "to@example.com",
            "--to",
            "second@example.com",
            "--cc",
            "cc@example.com",
            "--subject",
            "Report",
            "--content-type",
            "html",
            "--body-file",
            str(body_file),
        ],
        capsys,
    )

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "subject": "Report",
            "to": [
                {"name": None, "address": "to@example.com"},
                {"name": None, "address": "second@example.com"},
            ],
            "cc": [{"name": None, "address": "cc@example.com"}],
            "bcc": [],
        },
    }
    assert "message_id" not in json.dumps(output)
    sent = gateway.sent[0]
    assert sent.body.content_type == "html"
    assert sent.body.content == "<p>Body</p>"


def test_send_reads_the_body_from_standard_input(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    monkeypatch.setattr("sys.stdin", _Stdin("Body from stdin"))

    exit_code, _ = _invoke(
        ["--user", MAILBOX, "message", "send", "--to", "to@example.com", "--body-file", "-"],
        capsys,
    )

    assert exit_code == 0
    assert gateway.sent[0].body == MessageBody(content_type="text", content="Body from stdin")


def test_draft_create_allows_no_recipients_and_returns_identifiers(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway, ready=False)
    body_file = tmp_path / "body.txt"
    body_file.write_text("Draft body", encoding="utf-8")

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "draft",
            "create",
            "--subject",
            "Draft subject",
            "--body-file",
            str(body_file),
        ],
        capsys,
    )

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "message_id": "draft-id",
            "change_key": "draft-change-1",
            "folder_id": "drafts-id",
            "subject": "Draft subject",
            "to": [],
            "cc": [],
            "bcc": [],
        },
    }
    assert gateway.drafts[0].body.content == "Draft body"
    assert gateway.sent == []


@pytest.mark.parametrize(
    ("command", "reply_all"),
    [("reply", False), ("reply-all", True)],
)
def test_draft_reply_commands_save_without_sending(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    command: str,
    reply_all: bool,
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    monkeypatch.setattr("sys.stdin", _Stdin("Draft reply"))

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "draft",
            command,
            "message-id",
            "--body-file",
            "-",
        ],
        capsys,
    )

    assert exit_code == 0
    message_id, reply, recorded_reply_all = gateway.reply_drafts[0]
    assert message_id == "message-id"
    assert reply.body.content == "Draft reply"
    assert recorded_reply_all is reply_all
    assert cast(dict[str, JsonValue], output["data"])["message_id"] == "draft-id"
    assert gateway.replies == []


def test_send_handles_an_unreadable_body_file(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "send",
            "--to",
            "to@example.com",
            "--body-file",
            str(tmp_path / "missing.txt"),
        ],
        capsys,
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"
    assert gateway.sent == []


def test_send_requires_at_least_one_recipient(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    body_file = tmp_path / "body.txt"
    body_file.write_text("Body", encoding="utf-8")

    exit_code, output = _invoke(
        ["--user", MAILBOX, "message", "send", "--body-file", str(body_file)],
        capsys,
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"
    assert "At least one of to, cc or bcc is required" in str(_error(output)["message"])


@pytest.mark.parametrize(
    "arguments",
    [
        ["message", "reply", "message-id"],
        ["message", "reply-all", "message-id"],
        ["message", "draft", "create"],
        ["message", "draft", "reply", "message-id"],
        ["message", "draft", "reply-all", "message-id"],
    ],
)
def test_reply_handles_an_unreadable_body_file(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    arguments: list[str],
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            *arguments,
            "--body-file",
            str(tmp_path / "missing.txt"),
        ],
        capsys,
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"
    assert gateway.replies == []
    assert gateway.drafts == []
    assert gateway.reply_drafts == []


def test_send_rejects_an_invalid_recipient_address(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    body_file = tmp_path / "body.txt"
    body_file.write_text("Body", encoding="utf-8")

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "send",
            "--to",
            "not-an-address",
            "--body-file",
            str(body_file),
        ],
        capsys,
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"


def test_reply_omits_the_subject_so_the_server_standard_applies(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    body_file = tmp_path / "body.txt"
    body_file.write_text("Thanks", encoding="utf-8")

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "reply",
            "message-id",
            "--body-file",
            str(body_file),
        ],
        capsys,
    )

    assert exit_code == 0
    message_id, reply, reply_all = gateway.replies[0]
    assert message_id == "message-id"
    assert reply.subject is None
    assert reply_all is False
    assert cast(dict[str, JsonValue], output["data"])["subject"] == "RE: Report"


def test_reply_all_forwards_the_flag_and_an_explicit_subject(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    body_file = tmp_path / "body.txt"
    body_file.write_text("Thanks", encoding="utf-8")

    exit_code, _ = _invoke(
        [
            "--user",
            MAILBOX,
            "message",
            "reply-all",
            "message-id",
            "--subject",
            "Custom",
            "--body-file",
            str(body_file),
        ],
        capsys,
    )

    assert exit_code == 0
    message_id, reply, reply_all = gateway.replies[0]
    assert message_id == "message-id"
    assert reply.subject == "Custom"
    assert reply_all is True


def test_mark_read_marks_a_message_and_the_unread_flag_reverses_it(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    read_exit, read_output = _invoke(
        ["--user", MAILBOX, "message", "mark-read", "message-id"], capsys
    )
    unread_exit, _ = _invoke(
        ["--user", MAILBOX, "message", "mark-read", "message-id", "--unread"], capsys
    )

    assert read_exit == 0
    assert unread_exit == 0
    assert gateway.read_states == [("message-id", True), ("message-id", False)]
    assert read_output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "message_id": "message-id",
            "change_key": "change-2",
            "is_read": True,
        },
    }


def test_move_resolves_the_folder_selector(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", MAILBOX, "message", "move", "message-id", "--folder", "sentitems"],
        capsys,
    )

    assert exit_code == 0
    assert gateway.moves == [("message-id", "sent-id")]
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "previous_message_id": "message-id",
            "message_id": "moved-id",
            "change_key": "moved-change-1",
            "folder_id": "sent-id",
        },
    }


def test_move_rejects_an_unknown_folder(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", MAILBOX, "message", "move", "message-id", "--folder", "missing"],
        capsys,
    )

    assert exit_code == 4
    assert _error(output)["code"] == "resource_not_found"
    assert gateway.moves == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["message", "send", "--to", "to@example.com", "--body-file", "-"],
        ["message", "reply", "message-id", "--body-file", "-"],
        ["message", "reply-all", "message-id", "--body-file", "-"],
        ["message", "draft", "create", "--body-file", "-"],
        ["message", "draft", "reply", "message-id", "--body-file", "-"],
        ["message", "draft", "reply-all", "message-id", "--body-file", "-"],
        ["message", "mark-read", "message-id"],
        ["message", "move", "message-id", "--folder", "sentitems"],
    ],
)
def test_write_commands_require_a_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch, arguments: list[str]
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    monkeypatch.setattr("sys.stdin", _Stdin("Body"))

    exit_code, output = _invoke(arguments, capsys)

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"
    assert _error(output)["message"] == "--user is required"


@pytest.mark.parametrize(
    ("error", "expected_exit", "expected_code", "retryable"),
    [
        (EwsRejectedError("bad recipients"), 2, "invalid_argument", False),
        (EwsAuthenticationError("denied"), 3, "authentication_error", False),
        (EwsNotFoundError("gone"), 4, "resource_not_found", False),
        (EwsServiceError("network"), 5, "service_error", True),
    ],
)
def test_write_commands_map_remote_failures(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    error: Exception,
    expected_exit: int,
    expected_code: str,
    retryable: bool,
) -> None:
    gateway = WriteGateway()
    gateway.error = error
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "mark-read", "message-id"], capsys)

    assert exit_code == expected_exit
    assert _error(output)["code"] == expected_code
    assert _error(output)["retryable"] is retryable


def test_write_commands_require_a_ready_cache(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway, ready=False)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "mark-read", "message-id"], capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "cache_not_ready"
    assert "ews --user agent@example.com sync" in str(_error(output)["message"])


def test_write_commands_reject_a_different_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["--user", "other@example.com", "message", "mark-read", "message-id"], capsys
    )

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_write_commands_report_an_unknown_cached_message(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "mark-read", "other"], capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "resource_not_found"
    assert _error(output)["message"] == "Message not found: other"


def test_write_commands_handle_a_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ews.cli.EwsClient", lambda: gateway)

    exit_code, output = _invoke(["--user", MAILBOX, "message", "mark-read", "message-id"], capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_write_commands_report_a_broken_cache(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = WriteGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    (tmp_path / "cache.db").write_bytes(b"")

    exit_code, output = _invoke(["--user", MAILBOX, "message", "mark-read", "message-id"], capsys)

    assert exit_code == 2
    assert _error(output)["code"] == "cache_error"


def _configure_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: WriteGateway,
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

    def build_context(user: str | None) -> CommandContext:
        service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)


class _Stdin:
    def __init__(self, content: str) -> None:
        self._content = content

    def read(self) -> str:
        return self._content


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


def _invoke(arguments: list[str], capsys: CaptureFixture[str]) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app(arguments)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])


def _draft_result(profile: Profile, subject: str, message: DraftMessage) -> MessageDraftResult:
    return MessageDraftResult(
        user=profile.user.username,
        message_id="draft-id",
        change_key="draft-change-1",
        folder_id="drafts-id",
        subject=subject,
        to=[MailboxAddress(address=str(address)) for address in message.to],
        cc=[MailboxAddress(address=str(address)) for address in message.cc],
        bcc=[MailboxAddress(address=str(address)) for address in message.bcc],
    )
