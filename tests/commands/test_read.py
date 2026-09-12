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
from ews.exchange import EwsServiceError
from ews.models import (
    ConnectionTestResult,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderSyncResult,
    MessageBody,
    MessageDetail,
    MessageListQuery,
    MessageSummary,
    MessageSyncResult,
    Profile,
)
from ews.storage import SqliteMailboxStore


class ReadGateway:
    def __init__(self) -> None:
        self.query: MessageListQuery | None = None
        self.error: Exception | None = None

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        raise AssertionError("Not used")

    def list_folders(self, profile: Profile, password: SecretStr) -> list[Folder]:
        self._raise_error()
        return [
            Folder(
                id="inbox-id",
                parent_id=None,
                name="Inbox",
                well_known_name="inbox",
                total_count=1,
                unread_count=1,
            )
        ]

    def list_messages(
        self, profile: Profile, password: SecretStr, query: MessageListQuery
    ) -> tuple[list[MessageSummary], bool]:
        del profile, password
        self._raise_error()
        self.query = query
        return [_summary()], True

    def get_message(self, profile: Profile, password: SecretStr, message_id: str) -> MessageDetail:
        del profile, password
        self._raise_error()
        assert message_id == "message-id"
        summary = _summary()
        return MessageDetail(
            id=summary.id,
            change_key=summary.change_key,
            parent_folder_id=summary.parent_folder_id,
            subject=summary.subject,
            from_address=summary.from_address,
            received_at=summary.received_at,
            is_read=summary.is_read,
            has_attachments=summary.has_attachments,
            importance=summary.importance,
            sender=None,
            to=[],
            cc=[],
            bcc=[],
            reply_to=[],
            sent_at=None,
            created_at=None,
            internet_message_id=None,
            in_reply_to=None,
            body=MessageBody(content_type="text", content="Body"),
            internet_headers=[],
            attachments=[],
        )

    def _raise_error(self) -> None:
        if self.error is not None:
            raise self.error

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        del profile, password, sync_state
        self._raise_error()
        folder = Folder(
            id="inbox-id",
            parent_id=None,
            name="Inbox",
            well_known_name="inbox",
            total_count=0,
            unread_count=0,
        )
        return FolderSyncResult(
            changes=[
                FolderChange(
                    kind=FolderChangeKind.CREATE,
                    folder_id=folder.id,
                    folder=folder,
                )
            ],
            sync_state="hierarchy-state",
        )

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        del profile, password, folder_id, sync_state
        return MessageSyncResult(changes=[], sync_state="item-state")

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        del profile, password, message_ids
        raise AssertionError("Not used")


def test_folder_list_uses_global_user_and_returns_contract(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ReadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(["--user", "AGENT@EXAMPLE.COM", "folder", "list"], capsys)

    assert exit_code == 0
    assert output["schema_version"] == 1
    assert output["ok"] is True
    data = cast(dict[str, JsonValue], output["data"])
    assert data["user"] == "DOMAIN\\agent"
    assert cast(list[JsonValue], data["folders"])[0] == {
        "id": "inbox-id",
        "parent_id": None,
        "name": "Inbox",
        "well_known_name": "inbox",
        "total_count": 1,
        "unread_count": 1,
    }


def test_message_list_parses_all_options_and_returns_pagination(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ReadGateway()
    _configure_context(tmp_path, monkeypatch, gateway)
    args = [
        "--user",
        "DOMAIN\\agent",
        "message",
        "list",
        "--folder",
        "inbox-id",
        "--read-state",
        "unread",
        "--sender",
        "sender@example.com",
        "--subject-contains",
        "report",
        "--body-contains",
        "deadline",
        "--received-from",
        "2026-09-01T00:00:00+02:00",
        "--received-before",
        "2026-10-01T00:00:00+02:00",
        "--offset",
        "10",
        "--limit",
        "2",
    ]

    exit_code, output = _invoke(args, capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    assert data["pagination"] == {
        "offset": 10,
        "limit": 2,
        "has_more": False,
        "next_offset": None,
    }


def test_message_get_returns_detail(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ReadGateway())

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "message", "get", "message-id"], capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    message = cast(dict[str, JsonValue], data["message"])
    assert message["body"] == {"content_type": "text", "content": "Body"}
    assert message["attachments"] == []


def test_sync_returns_counts_and_enables_local_reads(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ReadGateway(), ready=False)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "sync"], capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    assert data["folders"] == {"created": 1, "updated": 0, "deleted": 0}
    assert data["messages"] == {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "read_state_changed": 0,
    }


def test_sync_progress_is_opt_in_and_writes_to_stdout(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ReadGateway(), ready=False)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "DOMAIN\\agent", "sync", "--progress"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert captured.err == ""
    assert "Synchronization complete" in captured.out
    output = cast(dict[str, JsonValue], json.loads(captured.out.splitlines()[-1]))
    assert output["ok"] is True


def test_sync_progress_closes_before_error_contract(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = ReadGateway()
    gateway.error = EwsServiceError("service unavailable")
    _configure_context(tmp_path, monkeypatch, gateway, ready=False)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "DOMAIN\\agent", "sync", "--progress"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 5
    assert captured.err == ""
    assert "Synchronization complete" not in captured.out
    output = cast(dict[str, JsonValue], json.loads(captured.out.splitlines()[-1]))
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "service_error"


def test_read_before_first_sync_returns_cache_not_ready(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ReadGateway(), ready=False)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "folder", "list"], capsys)

    assert exit_code == 4
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "cache_not_ready"
    assert error["retryable"] is False
    assert "ews --user DOMAIN\\agent sync" in str(error["message"])


@pytest.mark.parametrize(
    ("args", "gateway_error", "expected_exit", "expected_code", "retryable"),
    [
        (["folder", "list"], None, 2, "invalid_argument", False),
        (
            ["--user", "DOMAIN\\agent", "message", "list", "--limit", "201"],
            None,
            2,
            "invalid_argument",
            False,
        ),
    ],
)
def test_read_commands_map_expected_errors(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    args: list[str],
    gateway_error: Exception | None,
    expected_exit: int,
    expected_code: str,
    retryable: bool,
) -> None:
    gateway = ReadGateway()
    gateway.error = gateway_error
    _configure_context(tmp_path, monkeypatch, gateway)

    exit_code, output = _invoke(args, capsys)

    assert exit_code == expected_exit
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == expected_code
    assert error["retryable"] is retryable


def _configure_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    gateway: ReadGateway,
    *,
    ready: bool = True,
) -> None:
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
        return "top-secret"

    monkeypatch.setattr(keyring, "get_password", get_password)

    store = SqliteMailboxStore(tmp_path / "cache.db")
    if not ready:

        def build_empty_context(user: str | None) -> CommandContext:
            service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
            return CommandContext(user=user, service=service)

        monkeypatch.setattr("ews.cli._build_context", build_empty_context)
        return
    store.initialize()
    folder = Folder(
        id="inbox-id",
        parent_id=None,
        name="Inbox",
        well_known_name="inbox",
        total_count=1,
        unread_count=1,
    )
    store.replace_folders("agent@example.com", [folder])
    summary = _summary()
    store.upsert_messages(
        "agent@example.com",
        [
            MessageDetail(
                id=summary.id,
                change_key=summary.change_key,
                parent_folder_id=summary.parent_folder_id,
                subject=summary.subject,
                from_address=summary.from_address,
                received_at=summary.received_at,
                is_read=summary.is_read,
                has_attachments=summary.has_attachments,
                importance=summary.importance,
                sender=None,
                to=[],
                cc=[],
                bcc=[],
                reply_to=[],
                sent_at=None,
                created_at=None,
                internet_message_id=None,
                in_reply_to=None,
                body=MessageBody(content_type="text", content="Body"),
                internet_headers=[],
                attachments=[],
            )
        ],
    )
    store.mark_ready("agent@example.com")

    def build_context(user: str | None) -> CommandContext:
        service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)


def _invoke(args: list[str], capsys: CaptureFixture[str]) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app(args)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _summary() -> MessageSummary:
    return MessageSummary.model_validate(
        {
            "id": "message-id",
            "change_key": "change-key",
            "parent_folder_id": "inbox-id",
            "subject": "Report",
            "from_address": "sender@example.com",
            "received_at": "2026-09-11T12:00:00+02:00",
            "is_read": False,
            "has_attachments": False,
            "importance": "normal",
        }
    )
