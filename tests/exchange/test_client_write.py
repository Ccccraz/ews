# pyright: reportMissingTypeStubs=false

from collections.abc import Sequence
from typing import ClassVar, cast

import pytest
from exchangelib.errors import (
    ErrorFolderNotFound,
    ErrorInvalidRecipients,
    ErrorItemNotFound,
    ErrorMessageSizeExceeded,
    ErrorSendAsDenied,
    TransportError,
)
from exchangelib.properties import HTMLBody
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.exchange import (
    EwsAuthenticationError,
    EwsClient,
    EwsNotFoundError,
    EwsRejectedError,
    EwsServiceError,
)
from ews.models import DraftMessage, MailboxAddress, OutgoingMessage, OutgoingReply, Profile


class FakeCredentials:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeConfiguration:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeMailbox:
    def __init__(self, *, email_address: str, name: str | None = None) -> None:
        self.email_address = email_address
        self.name = name


class FakeItemId:
    def __init__(self, id: str) -> None:
        self.id = id


class FakeOutgoingMessage:
    instances: ClassVar[list[FakeOutgoingMessage]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.to_recipients = _fake_mailboxes(kwargs.get("to_recipients"))
        self.cc_recipients = _fake_mailboxes(kwargs.get("cc_recipients"))
        self.bcc_recipients = _fake_mailboxes(kwargs.get("bcc_recipients"))
        self.subject = kwargs.get("subject")
        self.body = kwargs.get("body")
        self.save_copies: list[bool] = []
        self.save_calls = 0
        self.id: str | None = None
        self.changekey: str | None = None
        FakeOutgoingMessage.instances.append(self)

    def send(self, *, save_copy: bool) -> None:
        if FakeOutgoingMessage.error is not None:
            raise FakeOutgoingMessage.error
        self.save_copies.append(save_copy)

    def save(self) -> FakeOutgoingMessage:
        if FakeOutgoingMessage.error is not None:
            raise FakeOutgoingMessage.error
        self.save_calls += 1
        self.id = "draft-id"
        self.changekey = "draft-change-1"
        return self


class FakeSavedDraft:
    def __init__(self, *, message_id: str | None = "draft-id") -> None:
        self.id = message_id
        self.changekey = None if message_id is None else "draft-change-1"


class FakeReply:
    def __init__(
        self,
        subject: str,
        body: object,
        *,
        to_recipients: Sequence[FakeMailbox] = (),
        cc_recipients: Sequence[FakeMailbox] = (),
    ) -> None:
        self.subject = subject
        self.body = body
        self.to_recipients = list(to_recipients)
        self.cc_recipients = list(cc_recipients)
        self.bcc_recipients: list[FakeMailbox] = []
        self.save_copies: list[bool] = []
        self.save_calls: list[object] = []

    def send(self, *, save_copy: bool) -> None:
        self.save_copies.append(save_copy)

    def save(self, folder: object) -> FakeSavedDraft:
        self.save_calls.append(folder)
        return FakeSavedDraft()


class FakeOriginal:
    def __init__(
        self,
        *,
        subject: str | None = "Report",
        author: FakeMailbox | None = None,
        to: Sequence[FakeMailbox] = (),
        cc: Sequence[FakeMailbox] = (),
    ) -> None:
        self.subject = subject
        self.author = author
        self.to_recipients = list(to)
        self.cc_recipients = list(cc)
        self.bcc_recipients: list[FakeMailbox] = []
        self.created: list[FakeReply] = []

    def create_reply(self, subject: str, body: object) -> FakeReply:
        reply = FakeReply(subject, body, to_recipients=[self.author] if self.author else [])
        self.created.append(reply)
        return reply

    def create_reply_all(self, subject: str, body: object) -> FakeReply:
        reply = FakeReply(
            subject,
            body,
            to_recipients=self.to_recipients,
            cc_recipients=self.cc_recipients,
        )
        self.created.append(reply)
        return reply


class FakeStoredItem:
    def __init__(
        self,
        *,
        message_id: str = "message-id",
        changekey: str = "change-1",
        is_read: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.id = message_id
        self.changekey = changekey
        self.is_read = is_read
        self.error = error
        self.save_calls: list[list[str]] = []
        self.move_calls: list[object] = []

    def save(self, *, update_fields: list[str]) -> None:
        if self.error is not None:
            raise self.error
        self.save_calls.append(update_fields)
        # The server bumps the change key, but the value that stays on this object does
        # not match it: only a re-read returns the authoritative one.
        self.changekey = "stale-change-key"
        FakeAccount.next_items = [
            FakeStoredItem(message_id=self.id, changekey="change-2", is_read=self.is_read)
        ]

    def move(self, to_folder: object) -> None:
        if self.error is not None:
            raise self.error
        self.move_calls.append(to_folder)
        self.id = "moved-id"
        self.changekey = "moved-change-1"


class FakeAccount:
    last_instance: ClassVar[FakeAccount | None] = None
    items: ClassVar[list[object]] = []
    next_items: ClassVar[list[object] | None] = None
    fetch_error: ClassVar[Exception | None] = None
    fetch_calls: ClassVar[list[tuple[list[FakeItemId], list[str]]]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.drafts = FakeItemId("drafts-id")
        FakeAccount.last_instance = self

    def fetch(self, *, ids: Sequence[FakeItemId], only_fields: Sequence[str]) -> list[object]:
        FakeAccount.fetch_calls.append((list(ids), list(only_fields)))
        if FakeAccount.fetch_error is not None:
            raise FakeAccount.fetch_error
        if FakeAccount.next_items is not None:
            items, FakeAccount.next_items = FakeAccount.next_items, None
            return items
        return list(FakeAccount.items)


def test_send_message_builds_a_message_and_keeps_the_sent_copy(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch)

    result = EwsClient().send_message(_profile(), SecretStr("secret"), _outgoing())

    outgoing = FakeOutgoingMessage.instances[-1]
    account = FakeAccount.last_instance
    assert account is not None
    assert outgoing.kwargs["account"] is account
    assert [mailbox.email_address for mailbox in outgoing.to_recipients] == ["to@example.com"]
    assert [mailbox.email_address for mailbox in outgoing.cc_recipients] == ["cc@example.com"]
    assert outgoing.bcc_recipients == []
    assert outgoing.subject == "Report"
    assert outgoing.body == "Body"
    assert outgoing.save_copies == [True]
    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "subject": "Report",
        "to": [{"name": None, "address": "to@example.com"}],
        "cc": [{"name": None, "address": "cc@example.com"}],
        "bcc": [],
    }


def test_send_message_uses_an_html_body(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch)
    message = OutgoingMessage.model_validate(
        {
            "to": ["to@example.com"],
            "body": {"content_type": "html", "content": "<p>Body</p>"},
        }
    )

    EwsClient().send_message(_profile(), SecretStr("secret"), message)

    body = FakeOutgoingMessage.instances[-1].body
    assert isinstance(body, HTMLBody)
    assert str(body) == "<p>Body</p>"


def test_save_message_draft_uses_save_only_and_returns_identifiers(
    monkeypatch: MonkeyPatch,
) -> None:
    _install(monkeypatch)
    draft = DraftMessage.model_validate(
        {
            "subject": "Draft",
            "body": {"content_type": "text", "content": "Body"},
        }
    )

    result = EwsClient().save_message_draft(_profile(), SecretStr("secret"), draft)

    outgoing = FakeOutgoingMessage.instances[-1]
    account = FakeAccount.last_instance
    assert account is not None
    assert outgoing.kwargs["folder"] is account.drafts
    assert outgoing.save_calls == 1
    assert outgoing.save_copies == []
    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "message_id": "draft-id",
        "change_key": "draft-change-1",
        "folder_id": "drafts-id",
        "subject": "Draft",
        "to": [],
        "cc": [],
        "bcc": [],
    }


@pytest.mark.parametrize(
    ("original_subject", "requested", "expected"),
    [
        ("Report", None, "RE: Report"),
        ("RE: Report", None, "RE: Report"),
        ("re: Report", None, "re: Report"),
        (None, None, ""),
        ("Report", "Custom", "Custom"),
    ],
)
def test_reply_message_derives_the_reply_subject(
    monkeypatch: MonkeyPatch,
    original_subject: str | None,
    requested: str | None,
    expected: str,
) -> None:
    author = FakeMailbox(email_address="author@example.com")
    original = FakeOriginal(subject=original_subject, author=author)
    _install(monkeypatch, items=[original])

    result = EwsClient().reply_message(
        _profile(),
        SecretStr("secret"),
        "message-id",
        _reply(requested),
        reply_all=False,
    )

    reply = original.created[0]
    assert reply.subject == expected
    assert reply.body == "Thanks"
    assert reply.save_copies == [True]
    assert result.subject == expected
    assert result.to == [MailboxAddress(name=None, address="author@example.com")]


def test_reply_message_reads_the_original_by_item_id(monkeypatch: MonkeyPatch) -> None:
    original = FakeOriginal(author=FakeMailbox(email_address="author@example.com"))
    _install(monkeypatch, items=[original])

    EwsClient().reply_message(
        _profile(), SecretStr("secret"), "message-id", _reply(None), reply_all=False
    )

    ids, only_fields = FakeAccount.fetch_calls[0]
    assert [item.id for item in ids] == ["message-id"]
    assert only_fields == ["subject", "author", "to_recipients", "cc_recipients", "bcc_recipients"]


def test_reply_all_reports_every_original_recipient(monkeypatch: MonkeyPatch) -> None:
    original = FakeOriginal(
        author=FakeMailbox(email_address="author@example.com"),
        to=[
            FakeMailbox(email_address="zoe@example.com"),
            FakeMailbox(email_address="amy@example.com"),
        ],
        cc=[FakeMailbox(email_address="cc@example.com")],
    )
    _install(monkeypatch, items=[original])

    result = EwsClient().reply_message(
        _profile(), SecretStr("secret"), "message-id", _reply(None), reply_all=True
    )

    reply = original.created[0]
    assert [mailbox.email_address for mailbox in reply.to_recipients] == [
        "zoe@example.com",
        "amy@example.com",
    ]
    assert [address.address for address in result.to] == [
        "amy@example.com",
        "zoe@example.com",
    ]
    assert [address.address for address in result.cc] == ["cc@example.com"]


@pytest.mark.parametrize("reply_all", [False, True])
def test_save_reply_draft_uses_save_only_and_returns_identifiers(
    monkeypatch: MonkeyPatch, reply_all: bool
) -> None:
    original = FakeOriginal(
        author=FakeMailbox(email_address="author@example.com"),
        to=[FakeMailbox(email_address="to@example.com")],
    )
    _install(monkeypatch, items=[original])

    result = EwsClient().save_reply_draft(
        _profile(), SecretStr("secret"), "message-id", _reply(None), reply_all=reply_all
    )

    account = FakeAccount.last_instance
    assert account is not None
    reply = original.created[0]
    assert reply.save_calls == [account.drafts]
    assert reply.save_copies == []
    assert result.message_id == "draft-id"
    assert result.change_key == "draft-change-1"
    expected = ["to@example.com"] if reply_all else ["author@example.com"]
    assert [address.address for address in result.to] == expected


def test_reply_message_rejects_an_original_without_a_sender(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch, items=[FakeOriginal(subject="Draft", author=None)])

    with pytest.raises(EwsRejectedError, match="no sender"):
        EwsClient().reply_message(
            _profile(), SecretStr("secret"), "message-id", _reply(None), reply_all=False
        )


def test_set_read_state_reports_the_change_key_read_back_from_the_server(
    monkeypatch: MonkeyPatch,
) -> None:
    item = FakeStoredItem(is_read=False)
    _install(monkeypatch, items=[item])

    result = EwsClient().set_read_state(_profile(), SecretStr("secret"), "message-id", is_read=True)

    assert [only_fields for _, only_fields in FakeAccount.fetch_calls] == [[], ["is_read"]]
    assert item.save_calls == [["is_read"]]
    assert item.changekey == "stale-change-key"
    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "message_id": "message-id",
        "change_key": "change-2",
        "is_read": True,
    }


def test_set_read_state_can_mark_a_message_unread(monkeypatch: MonkeyPatch) -> None:
    item = FakeStoredItem(is_read=True)
    _install(monkeypatch, items=[item])

    result = EwsClient().set_read_state(
        _profile(), SecretStr("secret"), "message-id", is_read=False
    )

    assert item.is_read is False
    assert result.is_read is False


def test_move_message_reports_the_destination_identifiers(monkeypatch: MonkeyPatch) -> None:
    item = FakeStoredItem()
    _install(monkeypatch, items=[item])
    folder = object()
    _patch_folder_from_id(monkeypatch, folder)

    result = EwsClient().move_message(_profile(), SecretStr("secret"), "message-id", "folder-id")

    assert item.move_calls == [folder]
    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "previous_message_id": "message-id",
        "message_id": "moved-id",
        "change_key": "moved-change-1",
        "folder_id": "folder-id",
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ErrorItemNotFound("gone"), EwsNotFoundError),
        (ErrorFolderNotFound("gone"), EwsNotFoundError),
        (ErrorInvalidRecipients("bad"), EwsRejectedError),
        (ErrorMessageSizeExceeded("too big"), EwsRejectedError),
        (ErrorSendAsDenied("denied"), EwsAuthenticationError),
        (TransportError("network"), EwsServiceError),
        (ValueError("not normalized"), EwsServiceError),
    ],
)
def test_write_operations_translate_ews_errors(
    monkeypatch: MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    _install(monkeypatch, items=[FakeStoredItem(error=error)])

    with pytest.raises(expected):
        EwsClient().set_read_state(_profile(), SecretStr("secret"), "message-id", is_read=True)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ErrorInvalidRecipients("bad"), EwsRejectedError),
        (ErrorSendAsDenied("denied"), EwsAuthenticationError),
        (TransportError("network"), EwsServiceError),
    ],
)
def test_send_message_translates_send_failures(
    monkeypatch: MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    _install(monkeypatch)
    FakeOutgoingMessage.error = error

    with pytest.raises(expected):
        EwsClient().send_message(_profile(), SecretStr("secret"), _outgoing())


@pytest.mark.parametrize(
    "items",
    [
        [],
        [ErrorItemNotFound("gone")],
    ],
)
def test_write_operations_reject_a_missing_original(
    monkeypatch: MonkeyPatch, items: list[object]
) -> None:
    _install(monkeypatch, items=items)

    with pytest.raises(EwsNotFoundError):
        EwsClient().set_read_state(_profile(), SecretStr("secret"), "message-id", is_read=True)


def test_write_operations_translate_original_fetch_failures(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch)
    FakeAccount.fetch_error = ErrorItemNotFound("gone")

    with pytest.raises(EwsNotFoundError):
        EwsClient().move_message(_profile(), SecretStr("secret"), "message-id", "folder-id")


def _install(monkeypatch: MonkeyPatch, *, items: Sequence[object] = ()) -> None:
    FakeAccount.items = list(items)
    FakeAccount.next_items = None
    FakeAccount.fetch_error = None
    FakeAccount.fetch_calls = []
    FakeAccount.last_instance = None
    FakeOutgoingMessage.instances = []
    FakeOutgoingMessage.error = None
    monkeypatch.setattr("ews.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews.exchange.client.Account", FakeAccount)
    monkeypatch.setattr("ews.exchange.client.ItemId", FakeItemId)
    monkeypatch.setattr("ews.exchange.client.Message", FakeOutgoingMessage)
    monkeypatch.setattr("ews.exchange.client.Mailbox", FakeMailbox)


def _fake_mailboxes(value: object) -> list[FakeMailbox]:
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [item for item in items if isinstance(item, FakeMailbox)]


def _patch_folder_from_id(monkeypatch: MonkeyPatch, folder: object) -> None:
    def folder_from_id(account: object, folder_id: str) -> object:
        del account
        assert folder_id == "folder-id"
        return folder

    monkeypatch.setattr("ews.exchange.client._folder_from_id", folder_from_id)


def _outgoing() -> OutgoingMessage:
    return OutgoingMessage.model_validate(
        {
            "to": ["to@example.com"],
            "cc": ["cc@example.com"],
            "subject": "Report",
            "body": {"content_type": "text", "content": "Body"},
        }
    )


def _reply(subject: str | None) -> OutgoingReply:
    return OutgoingReply.model_validate(
        {"subject": subject, "body": {"content_type": "text", "content": "Thanks"}}
    )


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
