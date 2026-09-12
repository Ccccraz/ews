import pytest
from pydantic import ValidationError

from ews.models import (
    MailboxAddress,
    MessageBody,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    OutgoingMessage,
    OutgoingReply,
)


def test_outgoing_message_accepts_any_recipient_group() -> None:
    message = OutgoingMessage.model_validate(
        {"cc": ["cc@example.com"], "body": _body("text", "Body")}
    )

    assert message.to == []
    assert message.bcc == []
    assert message.subject == ""
    assert [str(address) for address in message.cc] == ["cc@example.com"]


def test_outgoing_message_requires_a_recipient() -> None:
    with pytest.raises(ValidationError, match="At least one of to, cc or bcc is required"):
        OutgoingMessage.model_validate({"subject": "Report", "body": _body("text", "Body")})


def test_outgoing_message_rejects_an_invalid_address() -> None:
    with pytest.raises(ValidationError):
        OutgoingMessage.model_validate({"to": ["not-an-address"], "body": _body("text", "Body")})


def test_outgoing_message_rejects_an_unknown_content_type() -> None:
    with pytest.raises(ValidationError):
        OutgoingMessage.model_validate(
            {"to": ["to@example.com"], "body": _body("markdown", "Body")}
        )


def test_outgoing_reply_omits_the_subject_by_default() -> None:
    reply = OutgoingReply.model_validate({"body": _body("html", "<p>Body</p>")})

    assert reply.subject is None
    assert reply.body == MessageBody(content_type="html", content="<p>Body</p>")


def test_send_result_serializes_the_confirmed_recipients() -> None:
    result = MessageSendResult(
        user="DOMAIN\\agent",
        subject="Report",
        to=[MailboxAddress(address="to@example.com")],
        cc=[],
        bcc=[MailboxAddress(name="Blind", address="bcc@example.com")],
    )

    assert result.model_dump() == {
        "user": "DOMAIN\\agent",
        "subject": "Report",
        "to": [{"name": None, "address": "to@example.com"}],
        "cc": [],
        "bcc": [{"name": "Blind", "address": "bcc@example.com"}],
    }


def test_write_results_serialize_server_confirmed_identifiers() -> None:
    read_state = MessageReadStateResult(
        user="DOMAIN\\agent",
        message_id="message-id",
        change_key="change-2",
        is_read=True,
    )
    moved = MessageMoveResult(
        user="DOMAIN\\agent",
        previous_message_id="message-id",
        message_id="moved-id",
        change_key="moved-change-1",
        folder_id="sent-id",
    )

    assert read_state.model_dump() == {
        "user": "DOMAIN\\agent",
        "message_id": "message-id",
        "change_key": "change-2",
        "is_read": True,
    }
    assert moved.model_dump() == {
        "user": "DOMAIN\\agent",
        "previous_message_id": "message-id",
        "message_id": "moved-id",
        "change_key": "moved-change-1",
        "folder_id": "sent-id",
    }


def _body(content_type: str, content: str) -> dict[str, str]:
    return {"content_type": content_type, "content": content}
