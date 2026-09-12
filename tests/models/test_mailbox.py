# pyright: reportMissingTypeStubs=false

from datetime import UTC, datetime

import pytest
from exchangelib import EWSDateTime, EWSTimeZone
from pydantic import ValidationError

from ews_cli.models import MessageListQuery, MessageSummary, ReadState


def test_message_list_query_parses_filters_and_boundaries() -> None:
    query = MessageListQuery.model_validate(
        {
            "read_state": "unread",
            "sender": "sender@example.com",
            "received_from": "2026-09-01T00:00:00+02:00",
            "received_before": "2026-10-01T00:00:00+02:00",
            "limit": 200,
        }
    )

    assert query.folder == "inbox"
    assert query.read_state is ReadState.UNREAD
    assert query.received_from == datetime.fromisoformat("2026-09-01T00:00:00+02:00")
    assert query.limit == 200


@pytest.mark.parametrize(
    "data",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"read_state": "unknown"},
        {"received_from": "2026-09-01T00:00:00"},
        {
            "received_from": "2026-10-01T00:00:00+02:00",
            "received_before": "2026-09-01T00:00:00+02:00",
        },
    ],
)
def test_message_list_query_rejects_invalid_input(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MessageListQuery.model_validate(data)


def test_message_time_normalization_accepts_ews_datetime() -> None:
    ews_datetime = EWSDateTime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=EWSTimeZone("Europe/Berlin"),
    )

    message = MessageSummary.model_validate(
        {
            "id": "message-id",
            "change_key": "change-key",
            "parent_folder_id": "folder-id",
            "subject": "Report",
            "from_address": "sender@example.com",
            "received_at": ews_datetime,
            "is_read": False,
            "has_attachments": False,
            "importance": "normal",
        }
    )

    assert type(message.received_at) is datetime
    assert message.received_at == datetime(2026, 9, 12, 8, 30, tzinfo=UTC)


def test_sender_filter_accepts_internal_exchange_identifier() -> None:
    query = MessageListQuery(sender="dan")

    assert query.sender == "dan"
