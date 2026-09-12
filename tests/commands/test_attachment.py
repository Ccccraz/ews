import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import keyring
import pytest
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture, MonkeyPatch

from ews.application import (
    AttachmentNotFoundError,
    DestinationExistsError,
    InvalidDestinationError,
    MailboxApplicationService,
    UnsupportedAttachmentError,
)
from ews.cli import app
from ews.commands.context import CommandContext
from ews.config import PasswordStore, ProfileStore
from ews.exchange import EwsAuthenticationError, EwsNotFoundError, EwsServiceError
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    Contact,
    ContactSyncResult,
    DirectorySearchResult,
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
from ews.storage import SqliteMailboxStore

MAILBOX = "agent@example.com"


class SaveGateway:
    """Returns a server-confirmed save result and records the request."""

    def __init__(self) -> None:
        self.error: Exception | None = None
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


def test_attachment_save_returns_the_saved_file_contract(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = SaveGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    destination = tmp_path / "report.pdf"

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "attachment",
            "save",
            "message-id",
            "file-id",
            "--path",
            str(destination),
        ],
        capsys,
    )

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "message_id": "message-id",
            "attachment_id": "file-id",
            "name": "report.pdf",
            "content_type": "application/pdf",
            "path": str(destination),
            "bytes_written": 7,
        },
    }
    assert gateway.saved == [("message-id", "file-id", destination)]


def test_attachment_save_requires_a_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = SaveGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        ["attachment", "save", "message-id", "file-id", "--path", str(tmp_path / "file.bin")],
        capsys,
    )

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"
    assert _error(output)["message"] == "--user is required"
    assert gateway.saved == []


def test_attachment_save_rejects_a_different_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = SaveGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        [
            "--user",
            "other@example.com",
            "attachment",
            "save",
            "message-id",
            "file-id",
            "--path",
            str(tmp_path / "file.bin"),
        ],
        capsys,
    )

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_attachment_save_requires_a_ready_cache(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = SaveGateway()
    _configure_context(tmp_path, monkeypatch, gateway, ready=False)

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "attachment",
            "save",
            "message-id",
            "file-id",
            "--path",
            str(tmp_path / "file.bin"),
        ],
        capsys,
    )

    assert exit_code == 4
    assert _error(output)["code"] == "cache_not_ready"


def test_attachment_save_reports_an_unknown_attachment(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = SaveGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "attachment",
            "save",
            "message-id",
            "missing",
            "--path",
            str(tmp_path / "file.bin"),
        ],
        capsys,
    )

    assert exit_code == 4
    assert _error(output)["code"] == "resource_not_found"
    assert _error(output)["message"] == "Attachment not found: missing"


@pytest.mark.parametrize(
    ("error", "expected_exit", "expected_code", "retryable"),
    [
        (UnsupportedAttachmentError("item"), 2, "invalid_argument", False),
        (DestinationExistsError("exists"), 2, "destination_exists", False),
        (InvalidDestinationError("bad path"), 2, "invalid_argument", False),
        (AttachmentNotFoundError("gone"), 4, "resource_not_found", False),
        (EwsAuthenticationError("denied"), 3, "authentication_error", False),
        (EwsNotFoundError("gone"), 4, "resource_not_found", False),
        (EwsServiceError("network"), 5, "service_error", True),
    ],
)
def test_attachment_save_maps_failures(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    error: Exception,
    expected_exit: int,
    expected_code: str,
    retryable: bool,
) -> None:
    gateway = SaveGateway()
    gateway.error = error
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(
        [
            "--user",
            MAILBOX,
            "attachment",
            "save",
            "message-id",
            "file-id",
            "--path",
            str(tmp_path / "file.bin"),
        ],
        capsys,
    )

    assert exit_code == expected_exit
    assert _error(output)["code"] == expected_code
    assert _error(output)["retryable"] is retryable


def _configure_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: SaveGateway,
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
                )
            ],
        )
        store.upsert_messages(MAILBOX, [_message()])
        store.mark_ready(MAILBOX)

    def build_context(user: str | None) -> CommandContext:
        service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)


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
                }
            ],
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
