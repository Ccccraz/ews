from datetime import datetime

import pytest
from pydantic import ValidationError

from ews.models import (
    AttachmentMetadata,
    FlagStatus,
    Importance,
    MailboxAddress,
    MessageBody,
    MessageSummary,
    MessageThreadQuery,
    MessageThreadResult,
    Pagination,
    ThreadMessage,
)


def test_flag_status_values_are_stable() -> None:
    assert [status.value for status in FlagStatus] == ["none", "complete", "flagged"]


def test_message_summary_defaults_keep_backward_compatible_json() -> None:
    summary = MessageSummary.model_validate(
        {
            "id": "message-id",
            "change_key": "change-key",
            "parent_folder_id": "folder-id",
            "subject": "Report",
            "from_address": "sender@example.com",
            "received_at": "2026-09-12T10:30:00+02:00",
            "is_read": False,
            "has_attachments": False,
            "importance": "normal",
        }
    )

    assert summary.conversation_id is None
    assert summary.conversation_topic is None
    assert summary.conversation_index is None
    assert summary.conversation_depth is None
    assert summary.is_draft is False
    assert summary.categories == []
    assert summary.flag_status is FlagStatus.NONE
    assert summary.model_dump() == {
        "id": "message-id",
        "change_key": "change-key",
        "parent_folder_id": "folder-id",
        "subject": "Report",
        "from_address": "sender@example.com",
        "received_at": summary.received_at,
        "is_read": False,
        "has_attachments": False,
        "importance": "normal",
        "conversation_id": None,
        "conversation_topic": None,
        "conversation_index": None,
        "conversation_depth": None,
        "is_draft": False,
        "categories": [],
        "flag_status": "none",
    }


def test_thread_query_defaults_and_limits() -> None:
    assert MessageThreadQuery.model_validate({}).model_dump() == {"offset": 0, "limit": 20}

    with pytest.raises(ValidationError):
        MessageThreadQuery.model_validate({"limit": 0})
    with pytest.raises(ValidationError):
        MessageThreadQuery.model_validate({"limit": 201})
    with pytest.raises(ValidationError):
        MessageThreadQuery.model_validate({"offset": -1})


def test_thread_result_serializes_folder_depth_and_body() -> None:
    result = MessageThreadResult(
        user="DOMAIN\\agent",
        conversation_id="conversation-1",
        conversation_topic="mxbi project",
        message_count=15,
        messages=[
            ThreadMessage(
                id="message-id",
                change_key="change-key",
                parent_folder_id="folder-id",
                subject="mxbi project",
                from_address="sender@example.com",
                received_at=datetime.fromisoformat("2026-05-05T13:11:26+00:00"),
                is_read=True,
                has_attachments=False,
                importance=Importance.NORMAL,
                conversation_id="conversation-1",
                conversation_topic="mxbi project",
                conversation_index="0101dd4255558dafa9804f89e441a54f73c6ac15e9e9",
                conversation_depth=0,
                is_draft=False,
                categories=[],
                flag_status=FlagStatus.NONE,
                folder_name="longterm",
                to=[MailboxAddress(name="Agent", address="agent@example.com")],
                cc=[],
                attachments=[
                    AttachmentMetadata(
                        id="attachment-id",
                        kind="file",
                        name="report.pdf",
                        content_type="application/pdf",
                        size=7,
                        is_inline=False,
                        content_id=None,
                    )
                ],
                body=MessageBody(content_type="text", content="Body"),
                text_body="Body",
            )
        ],
        pagination=Pagination(offset=0, limit=20, has_more=False, next_offset=None),
    )

    data = result.model_dump()
    assert data["message_count"] == 15
    assert data["messages"][0]["folder_name"] == "longterm"
    assert data["messages"][0]["conversation_depth"] == 0
    assert data["messages"][0]["flag_status"] == "none"
    assert data["messages"][0]["text_body"] == "Body"
    assert data["messages"][0]["attachments"][0]["name"] == "report.pdf"
    assert data["pagination"] == {
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "next_offset": None,
    }
