# pyright: reportMissingTypeStubs=false

from collections.abc import Sequence
from datetime import datetime
from types import SimpleNamespace

import pytest
from exchangelib.items import Message
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import FolderNotFoundError, InvalidFolderError, MessageNotFoundError
from ews.exchange import EwsAuthenticationError, EwsClient, EwsServiceError
from ews.models import MessageListQuery, Profile, ReadState


class FakeInbox:
    total_count = 12
    unread_count = 3

    def __init__(self) -> None:
        self.refreshed = False

    def refresh(self) -> None:
        self.refreshed = True


class FakeAccount:
    last_instance: FakeAccount | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.inbox = FakeInbox()
        self.version = "Build=15.2.1.2, API=Exchange2016"
        FakeAccount.last_instance = self


class FakeCredentials:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeConfiguration:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FailingAccount:
    error_type: type[Exception] = Exception

    def __init__(self, **kwargs: object) -> None:
        del kwargs

    @property
    def inbox(self) -> object:
        raise self.error_type("failure")


def test_client_performs_real_folder_refresh_at_adapter_boundary(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", FakeAccount)

    result = EwsClient().test_access(_profile(), SecretStr("top-secret"))

    account = FakeAccount.last_instance
    assert account is not None
    assert account.kwargs["primary_smtp_address"] == "agent@example.com"
    assert account.kwargs["autodiscover"] is False
    assert account.inbox.refreshed is True
    assert result.server_version == "Build=15.2.1.2, API=Exchange2016"
    assert result.inbox_total_count == 12
    assert result.inbox_unread_count == 3


@pytest.mark.parametrize(
    ("error_path", "expected_error"),
    [
        ("exchangelib.errors.UnauthorizedError", EwsAuthenticationError),
        ("exchangelib.errors.TransportError", EwsServiceError),
    ],
)
def test_client_translates_exchangelib_errors(
    monkeypatch: MonkeyPatch,
    error_path: str,
    expected_error: type[Exception],
) -> None:
    error_type = _load_error_type(error_path)
    FailingAccount.error_type = error_type
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", FailingAccount)

    with pytest.raises(expected_error):
        EwsClient().test_access(_profile(), SecretStr("top-secret"))


class FakeQuerySet:
    def __init__(self, items: Sequence[object]) -> None:
        self.items = list(items)
        self.only_fields: tuple[str, ...] = ()
        self.filters: dict[str, object] = {}
        self.order: str | None = None
        self.requested_slice: slice | None = None

    def only(self, *fields: str) -> FakeQuerySet:
        self.only_fields = fields
        return self

    def filter(self, **filters: object) -> FakeQuerySet:
        self.filters.update(filters)
        return self

    def order_by(self, field: str) -> FakeQuerySet:
        self.order = field
        return self

    def __getitem__(self, requested: slice) -> list[object]:
        self.requested_slice = requested
        return self.items[requested]


class MailFolder:
    supported_item_models = (Message,)
    DISTINGUISHED_FOLDER_ID = "inbox"
    folder_class = "IPF.Note"

    def __init__(
        self,
        folder_id: str,
        *,
        parent_id: str | None = None,
        queryset: FakeQuerySet | None = None,
    ) -> None:
        self.id = folder_id
        self.parent_folder_id = SimpleNamespace(id=parent_id) if parent_id is not None else None
        self.name = "Inbox"
        self.total_count = 3
        self.unread_count = 2
        self.queryset = queryset or FakeQuerySet([])

    def all(self) -> FakeQuerySet:
        return self.queryset


class CalendarFolder(MailFolder):
    supported_item_models = (object,)
    DISTINGUISHED_FOLDER_ID = "calendar"
    folder_class = "IPF.Appointment"


class ConversationFolder(MailFolder):
    DISTINGUISHED_FOLDER_ID = "conversationhistory"
    folder_class = None


class SyncIssuesFolder(MailFolder):
    DISTINGUISHED_FOLDER_ID = "syncissues"
    is_hidden = False


class HiddenMailFolder(MailFolder):
    DISTINGUISHED_FOLDER_ID = None
    is_hidden = True


class FakeRoot:
    def __init__(self, folders: list[MailFolder]) -> None:
        self.folders = folders

    def walk(self) -> list[MailFolder]:
        return self.folders


class MailboxAccount:
    folders: list[MailFolder] = []
    inbox_folder: MailFolder = MailFolder("inbox-id")
    fetched: list[object] = []
    last_instance: MailboxAccount | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.root = FakeRoot(self.folders)
        self.msg_folder_root = self.root
        self.inbox = self.inbox_folder
        self.fetch_args: dict[str, object] | None = None
        MailboxAccount.last_instance = self

    def fetch(self, **kwargs: object) -> list[object]:
        self.fetch_args = kwargs
        return self.fetched


def test_client_lists_only_mail_folders_and_preserves_returned_hierarchy(
    monkeypatch: MonkeyPatch,
) -> None:
    parent = MailFolder("parent")
    child = MailFolder("child", parent_id="parent")
    calendar = CalendarFolder("calendar", parent_id="parent")
    conversation = ConversationFolder("conversation", parent_id="parent")
    sync_issues = SyncIssuesFolder("sync-issues", parent_id="parent")
    hidden = HiddenMailFolder("hidden", parent_id="parent")
    MailboxAccount.folders = [
        parent,
        child,
        calendar,
        conversation,
        sync_issues,
        hidden,
    ]
    _set_mailbox_account(monkeypatch)

    folders = EwsClient().list_folders(_profile(), SecretStr("top-secret"))

    assert [folder.id for folder in folders] == ["parent", "child", "conversation"]
    assert folders[0].parent_id is None
    assert folders[1].parent_id == "parent"
    assert folders[0].well_known_name == "inbox"


def test_client_lists_messages_with_minimum_fields_filters_and_limit_plus_one(
    monkeypatch: MonkeyPatch,
) -> None:
    items = [_message("1"), _message("2"), _message("3")]
    queryset = FakeQuerySet(items)
    inbox = MailFolder("inbox-id", queryset=queryset)
    MailboxAccount.inbox_folder = inbox
    MailboxAccount.folders = [inbox]
    _set_mailbox_account(monkeypatch)
    query = MessageListQuery.model_validate(
        {
            "read_state": "unread",
            "sender": "sender@example.com",
            "subject_contains": "report",
            "body_contains": "deadline",
            "received_from": "2026-09-01T00:00:00+02:00",
            "received_before": "2026-10-01T00:00:00+02:00",
            "limit": 2,
        }
    )

    messages, has_more = EwsClient().list_messages(_profile(), SecretStr("top-secret"), query)

    assert [message.id for message in messages] == ["1", "2"]
    assert has_more is True
    assert queryset.order == "-datetime_received"
    assert queryset.requested_slice == slice(0, 3)
    assert set(queryset.only_fields) == {
        "parent_folder_id",
        "subject",
        "author",
        "datetime_received",
        "is_read",
        "has_attachments",
        "importance",
    }
    assert queryset.filters == {
        "is_read": False,
        "sender": "sender@example.com",
        "subject__icontains": "report",
        "body__icontains": "deadline",
        "datetime_received__gte": query.received_from,
        "datetime_received__lt": query.received_before,
    }


def test_client_resolves_opaque_folder_id_and_read_filter(monkeypatch: MonkeyPatch) -> None:
    queryset = FakeQuerySet([_message("1")])
    folder = MailFolder("opaque-id", queryset=queryset)
    MailboxAccount.folders = [folder]
    _set_mailbox_account(monkeypatch)

    messages, has_more = EwsClient().list_messages(
        _profile(),
        SecretStr("top-secret"),
        MessageListQuery(folder="opaque-id", read_state=ReadState.READ, limit=2),
    )

    assert len(messages) == 1
    assert has_more is False
    assert queryset.filters == {"is_read": True}


@pytest.mark.parametrize(
    ("folder", "expected_error"),
    [("calendar", InvalidFolderError), ("missing", FolderNotFoundError)],
)
def test_client_rejects_invalid_or_missing_folders(
    monkeypatch: MonkeyPatch, folder: str, expected_error: type[Exception]
) -> None:
    MailboxAccount.folders = [CalendarFolder("calendar")]
    _set_mailbox_account(monkeypatch)

    with pytest.raises(expected_error):
        EwsClient().list_messages(
            _profile(), SecretStr("top-secret"), MessageListQuery(folder=folder)
        )


class FakeMessage:
    id: object
    changekey: object
    parent_folder_id: object
    subject: object
    author: object
    sender: object
    datetime_received: object
    is_read: object
    has_attachments: object
    importance: object
    to_recipients: object
    cc_recipients: object
    bcc_recipients: object
    reply_to: object
    datetime_sent: object
    datetime_created: object
    message_id: object
    in_reply_to: object
    body: object
    headers: object
    attachments: object


class FakeHtmlBody:
    def __str__(self) -> str:
        return "<p>Body</p>"


class FakeFileAttachment:
    attachment_id: object
    name: object
    content_type: object
    size: object
    is_inline: object
    content_id: object


def test_client_gets_detailed_message_and_maps_headers_and_attachments(
    monkeypatch: MonkeyPatch,
) -> None:
    item = FakeMessage()
    for name, value in vars(_message("message-id")).items():
        setattr(item, name, value)
    item.sender = _mailbox("sender@example.com", "Sender")
    item.to_recipients = [_mailbox("to@example.com", "To")]
    item.cc_recipients = []
    item.bcc_recipients = None
    item.reply_to = [_mailbox("reply@example.com", None)]
    item.datetime_sent = datetime.fromisoformat("2026-09-11T11:59:00+02:00")
    item.datetime_created = datetime.fromisoformat("2026-09-11T11:58:00+02:00")
    item.message_id = "<internet-id@example.com>"
    item.in_reply_to = "<parent@example.com>"
    item.body = FakeHtmlBody()
    item.headers = [
        SimpleNamespace(name="X-Test", value="one"),
        SimpleNamespace(name="X-Test", value="two"),
    ]
    attachment = FakeFileAttachment()
    attachment.attachment_id = SimpleNamespace(id="attachment-id")
    attachment.name = "report.pdf"
    attachment.content_type = "application/pdf"
    attachment.size = 123
    attachment.is_inline = False
    attachment.content_id = None
    item.attachments = [attachment]
    MailboxAccount.fetched = [item]
    _set_mailbox_account(monkeypatch)
    monkeypatch.setattr("ews.exchange.client.Message", FakeMessage)
    monkeypatch.setattr("ews.exchange.client.HTMLBody", FakeHtmlBody)
    monkeypatch.setattr("ews.exchange.client.FileAttachment", FakeFileAttachment)

    detail = EwsClient().get_message(_profile(), SecretStr("top-secret"), "message-id")

    account = MailboxAccount.last_instance
    assert account is not None
    assert account.fetch_args is not None
    assert account.fetch_args["ids"] == [("message-id", None)]
    assert detail.body.model_dump() == {"content_type": "html", "content": "<p>Body</p>"}
    assert [header.value for header in detail.internet_headers] == ["one", "two"]
    assert detail.attachments[0].model_dump() == {
        "id": "attachment-id",
        "kind": "file",
        "name": "report.pdf",
        "content_type": "application/pdf",
        "size": 123,
        "is_inline": False,
        "content_id": None,
    }
    assert detail.to[0].address == "to@example.com"


def test_client_maps_missing_message(monkeypatch: MonkeyPatch) -> None:
    MailboxAccount.fetched = []
    _set_mailbox_account(monkeypatch)

    with pytest.raises(MessageNotFoundError):
        EwsClient().get_message(_profile(), SecretStr("top-secret"), "missing")


def _load_error_type(path: str) -> type[Exception]:
    if path.endswith("UnauthorizedError"):
        from exchangelib.errors import UnauthorizedError

        return UnauthorizedError

    from exchangelib.errors import TransportError

    return TransportError


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )


def _set_mailbox_account(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", MailboxAccount)


def _message(message_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        changekey=f"change-{message_id}",
        parent_folder_id=SimpleNamespace(id="inbox-id"),
        subject="Report",
        author=_mailbox("sender@example.com", "Sender"),
        sender=None,
        datetime_received=datetime.fromisoformat("2026-09-11T12:00:00+02:00"),
        is_read=False,
        has_attachments=True,
        importance="Normal",
    )


def _mailbox(address: str, name: str | None) -> SimpleNamespace:
    return SimpleNamespace(email_address=address, name=name)
