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
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    Contact,
    ContactChange,
    ContactChangeKind,
    ContactEmail,
    ContactSyncResult,
    DirectoryContact,
    DirectorySearchResult,
    DraftMessage,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderKind,
    FolderSyncResult,
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


class ContactGateway:
    """A gateway whose remote methods must never run during local reads."""

    def __init__(self) -> None:
        self.directory_queries: list[str] = []
        self.directory_contacts: list[DirectoryContact] = [_directory_contact()]
        self.directory_truncated = False

    def search_directory(
        self, profile: Profile, password: SecretStr, query: str
    ) -> DirectorySearchResult:
        del password
        self.directory_queries.append(query)
        return DirectorySearchResult(
            user=profile.user.username,
            query=query,
            contacts=self.directory_contacts,
            truncated=self.directory_truncated,
        )

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        raise AssertionError("Not used")

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        raise AssertionError("Not used")

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        raise AssertionError("Not used")

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        raise AssertionError("Not used")

    def sync_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> ContactSyncResult:
        raise AssertionError("Not used")

    def fetch_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        contact_ids: Sequence[tuple[str, str]],
    ) -> list[Contact]:
        raise AssertionError("Not used")

    def send_message(
        self, profile: Profile, password: SecretStr, message: OutgoingMessage
    ) -> MessageSendResult:
        raise AssertionError("Not used")

    def save_message_draft(
        self, profile: Profile, password: SecretStr, message: DraftMessage
    ) -> MessageDraftResult:
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
        raise AssertionError("Not used")

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        raise AssertionError("Not used")

    def move_message(
        self, profile: Profile, password: SecretStr, message_id: str, folder_id: str
    ) -> MessageMoveResult:
        raise AssertionError("Not used")

    def save_attachment(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        raise AssertionError("Not used")


def test_contact_list_returns_cached_contacts(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "list"], capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    contacts = cast(list[dict[str, JsonValue]], data["contacts"])
    assert contacts[0]["display_name"] == "Alice Zhang"
    assert contacts[0]["folder_name"] == "Contacts"
    assert data["pagination"] == {"offset": 0, "limit": 50, "has_more": False, "next_offset": None}


def test_contact_list_filters_by_search_and_folder(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(
        [
            "--user",
            "DOMAIN\\agent",
            "contact",
            "list",
            "--folder",
            "contacts",
            "--search",
            "alice",
        ],
        capsys,
    )

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    assert len(cast(list[JsonValue], data["contacts"])) == 1


def test_contact_get_returns_one_contact(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "get", "contact-id"], capsys)

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    contact = cast(dict[str, JsonValue], data["contact"])
    assert contact["id"] == "contact-id"
    assert contact["emails"] == [{"label": "EmailAddress1", "address": "alice@example.com"}]


def test_contact_get_missing_returns_resource_not_found(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "get", "missing-id"], capsys)

    assert exit_code == 4
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "resource_not_found"


def test_contact_list_unknown_folder_returns_resource_not_found(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(
        ["--user", "DOMAIN\\agent", "contact", "list", "--folder", "inbox"], capsys
    )

    assert exit_code == 4
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "resource_not_found"


def test_contact_read_before_sync_returns_cache_not_ready(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=False)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "list"], capsys)

    assert exit_code == 4
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "cache_not_ready"


def test_contact_commands_require_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=True)

    exit_code, output = _invoke(["contact", "list"], capsys)

    assert exit_code == 2
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "invalid_argument"


def test_contact_search_returns_directory_contacts(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = _configure_context(tmp_path, monkeypatch, ready=False)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "search", "abel"], capsys)

    assert exit_code == 0
    assert gateway.directory_queries == ["abel"]
    data = cast(dict[str, JsonValue], output["data"])
    assert data["query"] == "abel"
    assert data["truncated"] is False
    contacts = cast(list[dict[str, JsonValue]], data["contacts"])
    assert contacts[0]["display_name"] == "Abel, Jacqueline"
    assert contacts[0]["email_address"] == "jabel@dpz.eu"
    assert contacts[0]["mailbox_type"] == "Mailbox"
    assert contacts[0]["department"] == "Tierhaltung"


def test_contact_search_applies_limit_and_marks_truncation(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    gateway = _configure_context(tmp_path, monkeypatch, ready=False)
    gateway.directory_contacts = [
        _directory_contact(display_name="Abel, Jacqueline"),
        _directory_contact(display_name="Abel, Thomas", address="tabel@dpz.eu"),
    ]
    gateway.directory_truncated = True

    exit_code, output = _invoke(
        ["--user", "DOMAIN\\agent", "contact", "search", "abel", "--limit", "1"], capsys
    )

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    contacts = cast(list[dict[str, JsonValue]], data["contacts"])
    assert len(contacts) == 1
    assert data["truncated"] is True


def test_contact_search_rejects_empty_query_and_bad_limit(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_context(tmp_path, monkeypatch, ready=False)

    exit_code, output = _invoke(["--user", "DOMAIN\\agent", "contact", "search", ""], capsys)
    assert exit_code == 2
    assert cast(dict[str, JsonValue], output["error"])["code"] == "invalid_argument"

    exit_code, output = _invoke(
        ["--user", "DOMAIN\\agent", "contact", "search", "abel", "--limit", "0"], capsys
    )
    assert exit_code == 2
    assert cast(dict[str, JsonValue], output["error"])["code"] == "invalid_argument"


def _configure_context(tmp_path: Path, monkeypatch: MonkeyPatch, *, ready: bool) -> ContactGateway:
    profile_store = ProfileStore(tmp_path / "profile.toml")
    profile_store.save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
            }
        )
    )
    store = SqliteMailboxStore(tmp_path / "cache.db")
    gateway = ContactGateway()

    def get_password(service: str, username: str) -> str:
        del service, username
        return "top-secret"

    monkeypatch.setattr(keyring, "get_password", get_password)

    def build_context(user: str | None) -> CommandContext:
        service = MailboxApplicationService(profile_store, PasswordStore(), gateway, store)
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)
    if not ready:
        return gateway

    store.initialize()
    folder = Folder(
        id="contacts-id",
        parent_id=None,
        name="Contacts",
        well_known_name="contacts",
        total_count=1,
        unread_count=0,
    )
    store.apply_folder_changes(
        "agent@example.com",
        [
            FolderChange(
                kind=FolderChangeKind.CREATE,
                folder_id=folder.id,
                folder=folder,
                folder_kind=FolderKind.CONTACTS,
            )
        ],
        "hierarchy-state",
        reset=True,
        well_known_folder_ids={"contacts": "contacts-id"},
    )
    contact = _contact()
    store.apply_contact_changes(
        "agent@example.com",
        "contacts-id",
        [
            ContactChange(
                kind=ContactChangeKind.CREATE,
                contact_id=contact.id,
                change_key=contact.change_key,
            )
        ],
        {contact.id: contact},
        "contact-state",
        reset=False,
    )
    store.mark_ready("agent@example.com")
    return gateway


def _invoke(args: list[str], capsys: CaptureFixture[str]) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app(args)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _contact() -> Contact:
    return Contact(
        id="contact-id",
        change_key="change-1",
        parent_folder_id="contacts-id",
        display_name="Alice Zhang",
        file_as="Zhang, Alice",
        company_name="Example",
        emails=[ContactEmail(label="EmailAddress1", address="alice@example.com")],
    )


def _directory_contact(
    *, display_name: str = "Abel, Jacqueline", address: str = "jabel@dpz.eu"
) -> DirectoryContact:
    return DirectoryContact(
        display_name=display_name,
        email_address=address,
        mailbox_type="Mailbox",
        given_name="Jacqueline",
        surname="Abel",
        company_name="Deutsches Primatenzentrum GmbH",
        department="Tierhaltung",
        job_title="Mitarbeiterin",
        email_alias="jabel",
        directory_id="<GUID=82035e82-399d-4bad-9b8d-b8b210ba8aa4>",
        emails=[ContactEmail(label="EmailAddress1", address=address)],
    )
