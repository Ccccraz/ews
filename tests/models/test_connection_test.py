import pytest
from pydantic import ValidationError

from ews_cli.models import ConnectionTestResult


def test_connection_test_result_contains_observable_ews_metadata() -> None:
    result = ConnectionTestResult(
        user="DOMAIN\\agent",
        mailbox="agent@example.com",
        server_version="Build=15.2.1.2, API=Exchange2016",
        inbox_total_count=12,
        inbox_unread_count=3,
    )

    assert result.inbox_total_count == 12
    assert result.inbox_unread_count == 3


def test_connection_test_result_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        ConnectionTestResult(
            user="DOMAIN\\agent",
            mailbox="agent@example.com",
            server_version="Exchange2016",
            inbox_total_count=-1,
            inbox_unread_count=0,
        )
