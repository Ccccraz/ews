# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from exchangelib.items import Message
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews_cli.exchange import EwsClient, EwsServiceError
from ews_cli.exchange.client import _ensure_item_extensions  # pyright: ignore[reportPrivateUsage]
from ews_cli.models import FlagStatus, Profile

ROOT_INDEX = bytes.fromhex("0101dd4255558dafa9804f89e441a54f73c6ac15e9e9")
REPLY_INDEX = bytes.fromhex("0101dd4255558dafa9804f89e441a54f73c6ac15e9e9b6ca26effc")


class FakeCredentials:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeConfiguration:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeMessage:
    def __init__(self, **overrides: object) -> None:
        self.id = "message-id"
        self.changekey = "change-key"
        self.parent_folder_id = SimpleNamespace(id="folder-id")
        self.subject = "mxbi project"
        self.author = SimpleNamespace(email_address="sender@example.com")
        self.sender = SimpleNamespace(name="Sender", email_address="sender@example.com")
        self.datetime_received = datetime(2026, 9, 12, 10, tzinfo=UTC)
        self.is_read = False
        self.has_attachments = False
        self.importance = "Normal"
        self.to_recipients = []
        self.cc_recipients = []
        self.bcc_recipients = []
        self.reply_to = []
        self.datetime_sent = None
        self.datetime_created = None
        self.message_id = "<message@example.com>"
        self.in_reply_to = None
        self.body = "Body"
        self.headers = []
        self.attachments = []
        self.conversation_id = SimpleNamespace(id="conversation-1")
        self.conversation_topic = "mxbi project"
        self.conversation_index = ROOT_INDEX
        self.text_body = "Plain body"
        self.references = ""
        self.is_draft = False
        self.categories = None
        self.flag_status = None
        self.__dict__.update(overrides)


class FakeMeetingMessage:
    """A non-message item without any Message-only attributes."""

    def __init__(self) -> None:
        self.id = "meeting-id"
        self.changekey = "change-key"
        self.parent_folder_id = SimpleNamespace(id="folder-id")
        self.subject = "Meeting"
        self.author = None
        self.sender = None
        self.datetime_received = datetime(2026, 9, 12, 10, tzinfo=UTC)
        self.is_read = True
        self.has_attachments = False
        self.importance = "Normal"
        self.to_recipients = []
        self.cc_recipients = []
        self.bcc_recipients = []
        self.reply_to = []
        self.datetime_sent = None
        self.datetime_created = None
        self.message_id = None
        self.in_reply_to = None
        self.body = "Body"
        self.headers = []
        self.attachments = []


class FakeAccount:
    items: ClassVar[list[object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def fetch(self, *, ids: Sequence[object], only_fields: Sequence[str]) -> list[object]:
        del ids, only_fields
        return list(FakeAccount.items)


def test_fetch_messages_maps_conversation_fields(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch, [FakeMessage()])

    detail = EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("message-id", "ck")])[0]

    assert detail.conversation_id == "conversation-1"
    assert detail.conversation_topic == "mxbi project"
    assert detail.conversation_index == ROOT_INDEX.hex()
    assert detail.conversation_depth == 0
    assert detail.is_draft is False
    assert detail.categories == []
    assert detail.flag_status is FlagStatus.NONE
    assert detail.text_body == "Plain body"
    assert detail.references is None


def test_fetch_messages_normalizes_empty_optional_values(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch, [FakeMessage(conversation_topic="", text_body="", references="")])

    detail = EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("message-id", "ck")])[0]

    assert detail.conversation_topic is None
    assert detail.text_body is None
    assert detail.references is None


@pytest.mark.parametrize(
    ("index", "expected_depth"),
    [
        (ROOT_INDEX, 0),
        (REPLY_INDEX, 1),
        (None, None),
    ],
)
def test_fetch_messages_derives_conversation_depth(
    monkeypatch: MonkeyPatch, index: bytes | None, expected_depth: int | None
) -> None:
    _install(monkeypatch, [FakeMessage(conversation_index=index)])

    detail = EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("message-id", "ck")])[0]

    assert detail.conversation_depth == expected_depth
    assert detail.conversation_index == (None if index is None else index.hex())


def test_fetch_messages_maps_flag_status_and_categories(monkeypatch: MonkeyPatch) -> None:
    _install(
        monkeypatch,
        [FakeMessage(categories=["Project", "Urgent"], flag_status=2, is_draft=True)],
    )

    detail = EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("message-id", "ck")])[0]

    assert detail.categories == ["Project", "Urgent"]
    assert detail.flag_status is FlagStatus.FLAGGED
    assert detail.is_draft is True


def test_fetch_messages_tolerates_items_without_message_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    _install(monkeypatch, [FakeMeetingMessage()])

    detail = EwsClient().fetch_messages(_profile(), SecretStr("secret"), [("message-id", "ck")])[0]

    assert detail.conversation_id is None
    assert detail.conversation_topic is None
    assert detail.conversation_index is None
    assert detail.conversation_depth is None
    assert detail.categories == []
    assert detail.flag_status is FlagStatus.NONE
    assert detail.text_body is None
    assert detail.references is None


def test_ensure_item_extensions_registers_once() -> None:
    _ensure_item_extensions()
    _ensure_item_extensions()

    assert Message.get_field_by_fieldname("flag_status") is not None


def test_ensure_item_extensions_skips_a_replaced_item_class(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("ews_cli.exchange.client.Message", SimpleNamespace)

    _ensure_item_extensions()


def test_fetch_messages_rejects_an_incomplete_response(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch, [FakeMessage()])

    with pytest.raises(EwsServiceError, match="incomplete"):
        EwsClient().fetch_messages(
            _profile(), SecretStr("secret"), [("message-id", "ck"), ("other", "ck")]
        )


def _install(monkeypatch: MonkeyPatch, items: Sequence[object]) -> None:
    FakeAccount.items = list(items)
    monkeypatch.setattr("ews_cli.exchange.client.Credentials", FakeCredentials)
    monkeypatch.setattr("ews_cli.exchange.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("ews_cli.exchange.client.Account", FakeAccount)
    monkeypatch.setattr("ews_cli.exchange.client.Message", FakeMessage)
    monkeypatch.setattr("ews_cli.exchange.client.BaseMeetingItem", FakeMeetingMessage)


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
