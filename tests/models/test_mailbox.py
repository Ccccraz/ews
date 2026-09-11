from datetime import datetime

import pytest
from pydantic import ValidationError

from ews.models import MessageListQuery, ReadState


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
