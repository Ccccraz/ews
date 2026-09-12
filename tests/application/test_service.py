from collections.abc import Sequence
from pathlib import Path

import keyring
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import MailboxApplicationService, UserNotFoundError
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    ConnectionTestResult,
    Folder,
    FolderSyncResult,
    MessageBody,
    MessageDetail,
    MessageListQuery,
    MessageSummary,
    MessageSyncResult,
    Profile,
)
from ews.storage import SqliteMailboxStore


class FakeGateway:
    def __init__(self) -> None:
        self.query: MessageListQuery | None = None

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        assert password.get_secret_value() == "top-secret"
        return ConnectionTestResult(
            user=profile.user.username,
            mailbox=profile.user.mailbox,
            server_version="Exchange2019",
            inbox_total_count=2,
            inbox_unread_count=1,
        )

    def list_folders(self, profile: Profile, password: SecretStr) -> list[Folder]:
        del profile, password
        return [
            Folder(
                id="folder-id",
                parent_id=None,
                name="Inbox",
                well_known_name="inbox",
                total_count=2,
                unread_count=1,
            )
        ]

    def list_messages(
        self, profile: Profile, password: SecretStr, query: MessageListQuery
    ) -> tuple[list[MessageSummary], bool]:
        del profile, password
        self.query = query
        return [_summary()], True

    def get_message(self, profile: Profile, password: SecretStr, message_id: str) -> MessageDetail:
        del profile, password
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


def test_service_coordinates_all_read_use_cases(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    gateway = FakeGateway()
    service = _service(tmp_path, monkeypatch, gateway)
    query = MessageListQuery(offset=0, limit=2)

    test_result = service.test_access("AGENT@EXAMPLE.COM")
    folders = service.list_folders("domain\\AGENT")
    messages = service.list_messages("DOMAIN\\agent", query)
    detail = service.get_message("DOMAIN\\agent", "message-id")

    assert test_result.server_version == "Exchange2019"
    assert folders.user == "DOMAIN\\agent"
    assert folders.folders[0].well_known_name == "inbox"
    assert messages.messages[0].id == "message-id"
    assert messages.pagination.model_dump() == {
        "offset": 0,
        "limit": 2,
        "has_more": False,
        "next_offset": None,
    }
    assert detail.message.body.content == "Body"


def test_service_rejects_a_different_user(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch, FakeGateway())

    try:
        service.list_folders("other")
    except UserNotFoundError as error:
        assert str(error) == "Profile not found for user: other"
    else:
        raise AssertionError("Expected UserNotFoundError")


def _service(
    tmp_path: Path, monkeypatch: MonkeyPatch, gateway: FakeGateway
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
        return "top-secret"

    monkeypatch.setattr(keyring, "get_password", get_password)
    store = SqliteMailboxStore(tmp_path / "cache.db")
    store.initialize()
    store.replace_folders(
        "agent@example.com",
        [
            Folder(
                id="folder-id",
                parent_id=None,
                name="Inbox",
                well_known_name="inbox",
                total_count=2,
                unread_count=1,
            )
        ],
    )
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
    return MailboxApplicationService(profile_store, PasswordStore(), gateway, store)


def _summary() -> MessageSummary:
    return MessageSummary.model_validate(
        {
            "id": "message-id",
            "change_key": "change-key",
            "parent_folder_id": "folder-id",
            "subject": "Report",
            "from_address": "sender@example.com",
            "received_at": "2026-09-11T12:00:00+02:00",
            "is_read": False,
            "has_attachments": False,
            "importance": "normal",
        }
    )
