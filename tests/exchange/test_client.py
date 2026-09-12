# pyright: reportMissingTypeStubs=false

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from exchangelib.errors import (
    ErrorFolderNotFound,
    ErrorInvalidSyncStateData,
    ErrorNameResolutionNoResults,
    TransportError,
    UnauthorizedError,
)
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import InvalidSyncStateError
from ews.exchange import EwsAuthenticationError, EwsClient, EwsServiceError
from ews.models import (
    ContactChangeKind,
    FolderChangeKind,
    FolderKind,
    MessageChangeKind,
    Profile,
)


class FakeCredentials:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeConfiguration:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class MailFolder:
    folder_class = "IPF.Note"
    is_hidden = False

    def __init__(self, folder_id: str, *, parent_id: str | None = None) -> None:
        self.id = folder_id
        self.changekey = f"{folder_id}-change-key"
        self.parent_folder_id = SimpleNamespace(id=parent_id) if parent_id is not None else None
        self.name = "Inbox"
        self.total_count = 3
        self.unread_count = 2
        self.item_sync_state: str | None = None
        self.sync_calls: list[tuple[str | None, list[str]]] = []
        self.changes: list[tuple[str, object]] = []

    def sync_items(self, *, sync_state: str | None, only_fields: list[str]) -> object:
        self.sync_calls.append((sync_state, only_fields))

        def generate() -> object:
            yield from self.changes
            self.item_sync_state = "item-state-2"

        return generate()


class CalendarFolder(MailFolder):
    folder_class = "IPF.Appointment"


class ContactFolder(MailFolder):
    folder_class = "IPF.Contact"


class HiddenFolder(MailFolder):
    is_hidden = True


class OtherClassFolder(MailFolder):
    """A folder whose class is not a mail class, used for well-known folders such as history."""

    folder_class = "IPF.Other"


class FakeRoot:
    def __init__(self) -> None:
        self.folder_sync_state: str | None = None
        self.changes: list[tuple[str, object]] = []
        self.folders: list[MailFolder] = []
        self.sync_calls: list[tuple[str | None, tuple[str, ...]]] = []

    def sync_hierarchy(self, *, sync_state: str | None, only_fields: tuple[str, ...]) -> object:
        self.sync_calls.append((sync_state, only_fields))

        def generate() -> object:
            yield from self.changes
            self.folder_sync_state = "hierarchy-state-2"

        return generate()

    def walk(self) -> list[MailFolder]:
        return self.folders


class FakeDistinguishedFolder:
    """A resolved distinguished folder, as exchangelib returns it from GetFolder."""

    def __init__(
        self, folder_id: str, name: str, *, total_count: int = 0, unread_count: int = 0
    ) -> None:
        self.id = folder_id
        self.name = name
        self.parent_folder_id = SimpleNamespace(id="msg-folder-root")
        self.total_count = total_count
        self.unread_count = unread_count


class FakeInbox(FakeDistinguishedFolder):
    def __init__(self) -> None:
        super().__init__("parent", "Inbox", total_count=12, unread_count=3)
        self.refreshed = False

    def refresh(self) -> None:
        self.refreshed = True


# Mirrors the distinguished mail folders the adapter resolves through Account attributes.
DISTINGUISHED_FOLDERS = {
    "sent": ("sent-folder", "Sent Items"),
    "drafts": ("drafts-folder", "Drafts"),
    "trash": ("deleteditems-folder", "Deleted Items"),
    "junk": ("junk-email-folder", "Junk Email"),
    "outbox": ("outbox-folder", "Outbox"),
    "notes": ("notes-folder", "Notes"),
    "conversation_history": ("conversation-history-folder", "Conversation History"),
    "sync_issues": ("sync-issues-folder", "Sync Issues"),
    "conflicts": ("conflicts-folder", "Conflicts"),
    "local_failures": ("local-failures-folder", "Local Failures"),
    "server_failures": ("server-failures-folder", "Server Failures"),
    "contacts": ("contacts-folder", "Contacts"),
}
# Named folders the fake root never reports, in resolution order.
OMITTED_NAMED_FOLDER_IDS = [
    "sent-folder",
    "drafts-folder",
    "deleteditems-folder",
    "junk-email-folder",
    "outbox-folder",
    "notes-folder",
    "conversation-history-folder",
    "sync-issues-folder",
    "conflicts-folder",
    "local-failures-folder",
    "server-failures-folder",
    "contacts-folder",
]


class FakeProtocol:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.result: list[object] = []
        self.truncate = False

    def resolve_names(
        self,
        names: object,
        parent_folders: object = None,
        return_full_contact_data: bool = False,
        search_scope: str | None = None,
        shape: str | None = None,
    ) -> list[object]:
        self.calls.append(
            {
                "names": list(names),  # type: ignore[call-overload]
                "full": return_full_contact_data,
                "scope": search_scope,
                "shape": shape,
            }
        )
        if self.truncate:
            import warnings

            warnings.warn("The ResolveNames service returns at most 100 candidates", stacklevel=1)
        return self.result


class FakeAccount:
    last_instance: FakeAccount | None = None
    root: ClassVar[FakeRoot] = FakeRoot()
    fetched: ClassVar[list[object]] = []
    protocol: ClassVar[FakeProtocol] = FakeProtocol()

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.inbox = FakeInbox()
        for attribute, (folder_id, name) in DISTINGUISHED_FOLDERS.items():
            setattr(self, attribute, FakeDistinguishedFolder(folder_id, name))
        self.version = "Exchange2019"
        self.msg_folder_root = type(self).root
        self.fetch_args: dict[str, object] | None = None
        FakeAccount.last_instance = self

    def fetch(self, **kwargs: object) -> list[object]:
        self.fetch_args = kwargs
        return self.fetched


class AccountWithoutSentItems(FakeAccount):
    """A mailbox where one distinguished folder cannot be resolved."""

    @property
    def sent(self) -> object:
        raise ErrorFolderNotFound("the mailbox has no Sent Items folder")

    @sent.setter
    def sent(self, value: object) -> None:
        del value


class FailingAccount:
    error: Exception = TransportError("failure")

    def __init__(self, **kwargs: object) -> None:
        del kwargs

    @property
    def inbox(self) -> object:
        raise self.error


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


class FakeMeetingItem(FakeMessage):
    pass


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


def test_access_builds_account_and_refreshes_inbox(monkeypatch: MonkeyPatch) -> None:
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().test_access(_profile(), SecretStr("secret"))

    account = FakeAccount.last_instance
    assert account is not None
    assert account.kwargs["primary_smtp_address"] == "agent@example.com"
    assert account.inbox.refreshed is True
    assert result.server_version == "Exchange2019"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UnauthorizedError("bad credentials"), EwsAuthenticationError),
        (TransportError("network"), EwsServiceError),
    ],
)
def test_access_translates_ews_errors(
    monkeypatch: MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    FailingAccount.error = error
    _patch_account(monkeypatch, FailingAccount)
    with pytest.raises(expected):
        EwsClient().test_access(_profile(), SecretStr("secret"))


def test_sync_hierarchy_consumes_generator_and_filters_visible_folders(
    monkeypatch: MonkeyPatch,
) -> None:
    root = FakeRoot()
    parent = MailFolder("parent")
    child = MailFolder("child", parent_id="parent")
    hidden = HiddenFolder("hidden")
    calendar = CalendarFolder("calendar")
    deleted = SimpleNamespace(id="deleted")
    root.changes = [
        ("create", parent),
        ("update", child),
        ("create", hidden),
        ("update", calendar),
        ("delete", deleted),
    ]
    FakeAccount.root = root
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().sync_hierarchy(_profile(), SecretStr("secret"), "old-state")

    assert result.sync_state == "hierarchy-state-2"
    assert [(change.kind, change.folder_id) for change in result.changes[:4]] == [
        (FolderChangeKind.CREATE, "parent"),
        (FolderChangeKind.UPDATE, "child"),
        (FolderChangeKind.DELETE, "calendar"),
        (FolderChangeKind.DELETE, "deleted"),
    ]
    ensured = result.changes[4:]
    assert [change.folder_id for change in ensured] == OMITTED_NAMED_FOLDER_IDS
    assert {change.kind for change in ensured} == {FolderChangeKind.CREATE}
    assert result.changes[1].folder is not None
    assert result.changes[1].folder.parent_id == "parent"
    assert result.changes[1].folder.well_known_name is None
    assert [change.folder.name for change in ensured if change.folder is not None] == [
        "Sent Items",
        "Drafts",
        "Deleted Items",
        "Junk Email",
        "Outbox",
        "Notes",
        "Conversation History",
        "Sync Issues",
        "Conflicts",
        "Local Failures",
        "Server Failures",
        "Contacts",
    ]
    assert result.well_known_folder_ids == {
        "inbox": "parent",
        "sentitems": "sent-folder",
        "drafts": "drafts-folder",
        "deleteditems": "deleteditems-folder",
        "junkemail": "junk-email-folder",
        "outbox": "outbox-folder",
        "notes": "notes-folder",
        "conversationhistory": "conversation-history-folder",
        "syncissues": "sync-issues-folder",
        "conflicts": "conflicts-folder",
        "localfailures": "local-failures-folder",
        "serverfailures": "server-failures-folder",
        "contacts": "contacts-folder",
    }
    assert root.sync_calls[0][0] == "old-state"
    assert "is_hidden" in root.sync_calls[0][1]


def test_sync_hierarchy_skips_well_known_folders_the_mailbox_lacks(
    monkeypatch: MonkeyPatch,
) -> None:
    FakeAccount.root = FakeRoot()
    _patch_account(monkeypatch, AccountWithoutSentItems)

    result = EwsClient().sync_hierarchy(_profile(), SecretStr("secret"), None)

    assert "sentitems" not in result.well_known_folder_ids
    assert result.well_known_folder_ids["inbox"] == "parent"


def test_sync_hierarchy_includes_a_well_known_folder_of_another_class(
    monkeypatch: MonkeyPatch,
) -> None:
    root = FakeRoot()
    root.changes = [
        ("create", OtherClassFolder("conversation-history-folder")),
        ("create", CalendarFolder("calendar-folder")),
    ]
    FakeAccount.root = root
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().sync_hierarchy(_profile(), SecretStr("secret"), None)

    reported = [(change.kind, change.folder_id) for change in result.changes]
    assert (FolderChangeKind.CREATE, "conversation-history-folder") in reported
    assert all(folder_id != "calendar-folder" for _, folder_id in reported)


def test_sync_items_maps_all_change_types_with_id_only(monkeypatch: MonkeyPatch) -> None:
    root = FakeRoot()
    folder = MailFolder("folder-id")
    folder.changes = [
        ("create", SimpleNamespace(id="created", changekey="ck-1")),
        ("update", SimpleNamespace(id="updated", changekey="ck-2")),
        ("delete", SimpleNamespace(id="deleted", changekey="ck-3")),
        (
            "read_flag_change",
            (SimpleNamespace(id="read", changekey="ck-4"), True),
        ),
    ]
    root.folders = [folder]
    FakeAccount.root = root
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.Message", SimpleNamespace)
    _patch_folder_from_id(monkeypatch, folder)

    result = EwsClient().sync_items(_profile(), SecretStr("secret"), "folder-id", "item-state-1")

    assert result.sync_state == "item-state-2"
    assert [change.kind for change in result.changes] == list(MessageChangeKind)
    assert result.changes[-1].is_read is True
    assert folder.sync_calls == [("item-state-1", [])]


def test_sync_items_binds_directly_to_the_hierarchy_folder_id(
    monkeypatch: MonkeyPatch,
) -> None:
    folder = MailFolder("opaque-id")
    FakeAccount.root = FakeRoot()
    _patch_account(monkeypatch, FakeAccount)
    _patch_folder_from_id(monkeypatch, folder)

    EwsClient().sync_items(_profile(), SecretStr("secret"), "opaque-id", None)

    assert folder.sync_calls == [(None, [])]


def test_invalid_sync_state_has_distinct_application_error(monkeypatch: MonkeyPatch) -> None:
    class InvalidRoot(FakeRoot):
        def sync_hierarchy(self, *, sync_state: str | None, only_fields: tuple[str, ...]) -> object:
            del sync_state, only_fields
            raise ErrorInvalidSyncStateData("invalid")

    FakeAccount.root = InvalidRoot()
    _patch_account(monkeypatch, FakeAccount)
    with pytest.raises(InvalidSyncStateError):
        EwsClient().sync_hierarchy(_profile(), SecretStr("secret"), "bad")


def test_fetch_messages_maps_full_detail_and_enforces_batch_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    item = _message("message-id")
    FakeAccount.fetched = [item]
    FakeAccount.root = FakeRoot()
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.Message", FakeMessage)
    monkeypatch.setattr("ews.exchange.client.HTMLBody", FakeHtmlBody)
    monkeypatch.setattr("ews.exchange.client.FileAttachment", FakeFileAttachment)

    messages = EwsClient().fetch_messages(
        _profile(), SecretStr("secret"), [("message-id", "change-key")]
    )

    assert messages[0].body.content == "<p>Body</p>"
    assert messages[0].received_at.tzinfo is UTC
    account = FakeAccount.last_instance
    assert account is not None
    assert account.fetch_args is not None
    assert account.fetch_args["ids"] == [("message-id", "change-key")]
    with pytest.raises(ValueError, match="at most 10"):
        EwsClient().fetch_messages(
            _profile(), SecretStr("secret"), [(str(index), "ck") for index in range(11)]
        )


def test_fetch_messages_accepts_meeting_items(monkeypatch: MonkeyPatch) -> None:
    item = _meeting_item("meeting-id")
    FakeAccount.fetched = [item]
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.BaseMeetingItem", FakeMeetingItem)

    messages = EwsClient().fetch_messages(
        _profile(), SecretStr("secret"), [("meeting-id", "change-key")]
    )

    assert messages[0].id == "meeting-id"
    assert messages[0].subject == "Report"


def test_fetch_messages_preserves_internal_exchange_addresses(
    monkeypatch: MonkeyPatch,
) -> None:
    item = _message("message-id")
    item.author = SimpleNamespace(email_address="dan")
    item.sender = SimpleNamespace(name="Dan", email_address="dan")
    item.to_recipients = [SimpleNamespace(name="Agent", email_address="DOMAIN\\agent")]
    FakeAccount.fetched = [item]
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.Message", FakeMessage)

    messages = EwsClient().fetch_messages(
        _profile(), SecretStr("secret"), [("message-id", "change-key")]
    )

    assert messages[0].from_address == "dan"
    assert messages[0].sender is not None
    assert messages[0].sender.address == "dan"
    assert messages[0].to[0].address == "DOMAIN\\agent"


def test_sync_items_ignores_unsupported_creates_and_removes_updates(
    monkeypatch: MonkeyPatch,
) -> None:
    root = FakeRoot()
    folder = MailFolder("folder-id")
    folder.changes = [
        ("create", SimpleNamespace(id="unsupported-create", changekey="ck-1")),
        ("update", SimpleNamespace(id="unsupported-update", changekey="ck-2")),
    ]
    root.folders = [folder]
    FakeAccount.root = root
    _patch_account(monkeypatch, FakeAccount)
    _patch_folder_from_id(monkeypatch, folder)

    result = EwsClient().sync_items(_profile(), SecretStr("secret"), "folder-id", "item-state-1")

    assert len(result.changes) == 1
    assert result.changes[0].kind is MessageChangeKind.DELETE
    assert result.changes[0].message_id == "unsupported-update"


def test_fetch_messages_rejects_incomplete_response(monkeypatch: MonkeyPatch) -> None:
    FakeAccount.fetched = []
    _patch_account(monkeypatch, FakeAccount)
    with pytest.raises(EwsServiceError, match="incomplete"):
        EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("id", "ck")])


def _patch_account(monkeypatch: MonkeyPatch, account_type: type[object]) -> None:
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", account_type)


def _patch_folder_from_id(monkeypatch: MonkeyPatch, folder: MailFolder) -> None:
    def folder_from_id(account: object, folder_id: str) -> MailFolder:
        del account
        assert folder_id == folder.id
        return folder

    monkeypatch.setattr("ews.exchange.client._folder_from_id", folder_from_id)


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )


def _message(message_id: str) -> FakeMessage:
    item = FakeMessage()
    item.id = message_id
    item.changekey = "change-key"
    item.parent_folder_id = SimpleNamespace(id="folder-id")
    item.subject = "Report"
    item.author = SimpleNamespace(email_address="author@example.com")
    item.sender = SimpleNamespace(name="Sender", email_address="sender@example.com")
    item.datetime_received = datetime(2026, 9, 12, 10, tzinfo=UTC)
    item.is_read = False
    item.has_attachments = True
    item.importance = "Normal"
    item.to_recipients = [SimpleNamespace(name="To", email_address="to@example.com")]
    item.cc_recipients = []
    item.bcc_recipients = None
    item.reply_to = []
    item.datetime_sent = datetime(2026, 9, 12, 9, tzinfo=UTC)
    item.datetime_created = None
    item.message_id = "<internet@example.com>"
    item.in_reply_to = None
    item.body = FakeHtmlBody()
    item.headers = [SimpleNamespace(name="X-Test", value="value")]
    attachment = FakeFileAttachment()
    attachment.attachment_id = SimpleNamespace(id="attachment-id")
    attachment.name = "report.pdf"
    attachment.content_type = "application/pdf"
    attachment.size = 123
    attachment.is_inline = False
    attachment.content_id = None
    item.attachments = [attachment]
    return item


def _meeting_item(message_id: str) -> FakeMeetingItem:
    message = _message(message_id)
    meeting = FakeMeetingItem()
    for name, value in vars(message).items():
        setattr(meeting, name, value)
    return meeting


class FakeContactItem:
    """A stand-in for an IdOnly contact item."""

    def __init__(self, contact_id: str, changekey: str = "ck") -> None:
        self.id = contact_id
        self.changekey = changekey


class FakeContact(FakeContactItem):
    def __init__(self, contact_id: str) -> None:
        super().__init__(contact_id, "contact-change")
        self.parent_folder_id = SimpleNamespace(id="contacts-folder")
        self.display_name = "Alice Zhang"
        self.file_as = "Zhang, Alice"
        self.given_name = "Alice"
        self.middle_name = None
        self.surname = "Zhang"
        self.nickname = None
        self.initials = "AZ"
        self.generation = None
        self.company_name = "Example"
        self.department = "Engineering"
        self.job_title = "Engineer"
        self.office = "3F"
        self.manager = "Bob"
        self.profession = None
        self.business_homepage = None
        self.email_addresses = [SimpleNamespace(label="EmailAddress1", email="alice@example.com")]
        self.phone_numbers = [SimpleNamespace(label="MobilePhone", phone_number="123")]
        self.physical_addresses = [
            SimpleNamespace(
                label="Business",
                street="1 Road",
                city="Shanghai",
                state=None,
                country="CN",
                zipcode="200000",
            )
        ]
        self.im_addresses = [SimpleNamespace(label="ImAddress1", im_address="alice.im")]
        self.categories = ["Team"]
        self.notes = "Notes"
        self.birthday = None
        self.has_picture = True


def _contact_item(contact_id: str) -> FakeContactItem:
    return FakeContactItem(contact_id, f"{contact_id}-change")


def test_sync_hierarchy_classifies_contact_folders(monkeypatch: MonkeyPatch) -> None:
    root = FakeRoot()
    root.changes = [("create", ContactFolder("contacts-raw"))]
    FakeAccount.root = root
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().sync_hierarchy(_profile(), SecretStr("secret"), None)

    created = next(change for change in result.changes if change.folder_id == "contacts-raw")
    assert created.folder_kind is FolderKind.CONTACTS


def test_sync_contacts_maps_changes_and_skips_unsupported_items(
    monkeypatch: MonkeyPatch,
) -> None:
    folder = ContactFolder("contacts-folder")
    folder.changes = [
        ("create", _contact_item("created")),
        ("update", _contact_item("updated")),
        ("delete", _contact_item("deleted")),
        ("update", SimpleNamespace(id="unsupported")),
    ]
    FakeAccount.root = FakeRoot()
    _patch_account(monkeypatch, FakeAccount)
    _patch_folder_from_id(monkeypatch, folder)
    monkeypatch.setattr("ews.exchange.client.EwsContact", FakeContactItem)

    result = EwsClient().sync_contacts(_profile(), SecretStr("secret"), "contacts-folder", None)

    assert result.sync_state == "item-state-2"
    assert [(change.kind, change.contact_id) for change in result.changes] == [
        (ContactChangeKind.CREATE, "created"),
        (ContactChangeKind.UPDATE, "updated"),
        (ContactChangeKind.DELETE, "deleted"),
        (ContactChangeKind.DELETE, "unsupported"),
    ]
    assert result.changes[0].change_key == "created-change"


def test_fetch_contacts_maps_common_fields_and_enforces_batch_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    FakeAccount.fetched = [FakeContact("contact-id")]
    FakeAccount.root = FakeRoot()
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.EwsContact", FakeContact)

    contacts = EwsClient().fetch_contacts(
        _profile(), SecretStr("secret"), [("contact-id", "contact-change")]
    )

    assert contacts[0].display_name == "Alice Zhang"
    assert contacts[0].folder_name is None
    assert contacts[0].emails[0].address == "alice@example.com"
    assert contacts[0].phones[0].number == "123"
    assert contacts[0].addresses[0].postal_code == "200000"
    assert contacts[0].im_addresses[0].address == "alice.im"
    assert contacts[0].categories == ["Team"]
    assert contacts[0].has_picture is True
    account = FakeAccount.last_instance
    assert account is not None
    assert account.fetch_args is not None
    assert account.fetch_args["ids"] == [("contact-id", "contact-change")]
    with pytest.raises(ValueError, match="at most 10"):
        EwsClient().fetch_contacts(
            _profile(), SecretStr("secret"), [(str(index), "ck") for index in range(11)]
        )


def test_fetch_contacts_rejects_incomplete_and_wrong_type(monkeypatch: MonkeyPatch) -> None:
    FakeAccount.fetched = []
    _patch_account(monkeypatch, FakeAccount)
    monkeypatch.setattr("ews.exchange.client.EwsContact", FakeContact)
    with pytest.raises(EwsServiceError, match="incomplete"):
        EwsClient().fetch_contacts(_profile(), SecretStr("secret"), [("contact-id", "ck")])

    FakeAccount.fetched = [FakeContactItem("not-a-contact")]
    _patch_account(monkeypatch, FakeAccount)
    with pytest.raises(EwsServiceError, match="non-contact"):
        EwsClient().fetch_contacts(_profile(), SecretStr("secret"), [("contact-id", "ck")])


def test_search_directory_maps_resolved_contacts(monkeypatch: MonkeyPatch) -> None:
    FakeAccount.protocol.calls = []
    FakeAccount.protocol.truncate = False
    FakeAccount.protocol.result = [(_directory_mailbox(), _directory_contact())]
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().search_directory(_profile(), SecretStr("secret"), "abel")

    assert result.user == "DOMAIN\\agent"
    assert result.query == "abel"
    assert result.truncated is False
    contact = result.contacts[0]
    assert contact.display_name == "Abel, Jacqueline"
    assert contact.email_address == "jabel@dpz.eu"
    assert contact.mailbox_type == "Mailbox"
    assert contact.department == "Tierhaltung"
    assert contact.emails[0].address == "jabel@dpz.eu"
    assert contact.phones[0].number == "+49 551 3851-0"
    assert contact.addresses[0].city == "Göttingen"
    assert FakeAccount.protocol.calls[0] == {
        "names": ["abel"],
        "full": True,
        "scope": "ActiveDirectory",
        "shape": "AllProperties",
    }


def test_search_directory_skips_no_results_and_empty_result(monkeypatch: MonkeyPatch) -> None:
    FakeAccount.protocol.calls = []
    FakeAccount.protocol.truncate = False
    FakeAccount.protocol.result = [ErrorNameResolutionNoResults("no results")]
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().search_directory(_profile(), SecretStr("secret"), "zzz")

    assert result.contacts == []
    assert result.truncated is False


def test_search_directory_marks_truncation(monkeypatch: MonkeyPatch) -> None:
    FakeAccount.protocol.calls = []
    FakeAccount.protocol.truncate = True
    FakeAccount.protocol.result = []
    _patch_account(monkeypatch, FakeAccount)

    result = EwsClient().search_directory(_profile(), SecretStr("secret"), "s")

    assert result.truncated is True


def _directory_mailbox() -> SimpleNamespace:
    return SimpleNamespace(
        name="Abel, Jacqueline",
        email_address="EX:/o=DPZ/cn=Abel",
        mailbox_type="Mailbox",
    )


def _directory_contact() -> SimpleNamespace:
    return SimpleNamespace(
        display_name="Abel, Jacqueline",
        given_name="Jacqueline",
        surname="Abel",
        company_name="Deutsches Primatenzentrum GmbH",
        department="Tierhaltung",
        job_title="Mitarbeiterin",
        email_alias="jabel",
        directory_id="<GUID=82035e82-399d-4bad-9b8d-b8b210ba8aa4>",
        email_addresses=[SimpleNamespace(label="EmailAddress1", email="SMTP:jabel@dpz.eu")],
        phone_numbers=[SimpleNamespace(label="BusinessPhone", phone_number="+49 551 3851-0")],
        physical_addresses=[
            SimpleNamespace(
                label="Business",
                street="Kellnerweg 4",
                city="Göttingen",
                state="Niedersachsen",
                country="Deutschland",
                zipcode="37077",
            )
        ],
    )
