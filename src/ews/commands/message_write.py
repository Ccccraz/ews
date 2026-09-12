import sys
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from ews.commands.context import CommandContext, fail, run_write
from ews.models import (
    DraftMessage,
    MessageDraftResult,
    MessageSendResult,
    OutgoingMessage,
    OutgoingReply,
)

ContentType = Literal["text", "html"]
BodyFile = Annotated[str, Parameter(allow_leading_hyphen=True)]
ContextArgument = Annotated[CommandContext, Parameter(parse=False, show=False)]


def send_message(
    *,
    body_file: BodyFile,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Send a new message and keep the EWS Sent copy."""
    try:
        body = _read_body(body_file)
    except (OSError, UnicodeDecodeError) as error:
        return fail("invalid_argument", f"Unable to read body file: {error}", 2)

    def operation(user: str) -> MessageSendResult:
        message = OutgoingMessage.model_validate(
            {
                "to": to or [],
                "cc": cc or [],
                "bcc": bcc or [],
                "subject": subject,
                "body": {"content_type": content_type, "content": body},
            }
        )
        return context.service.send_message(user, message)

    return run_write(context, operation)


def create_draft(
    *,
    body_file: BodyFile,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Save a new message in Drafts without sending it."""
    try:
        body = _read_body(body_file)
    except (OSError, UnicodeDecodeError) as error:
        return fail("invalid_argument", f"Unable to read body file: {error}", 2)

    def operation(user: str) -> MessageDraftResult:
        message = DraftMessage.model_validate(
            {
                "to": to or [],
                "cc": cc or [],
                "bcc": bcc or [],
                "subject": subject,
                "body": {"content_type": content_type, "content": body},
            }
        )
        return context.service.save_message_draft(user, message)

    return run_write(context, operation)


def reply_to_message(
    message_id: str,
    *,
    body_file: BodyFile,
    subject: str | None = None,
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Reply to one message and keep the EWS Sent copy."""
    try:
        body = _read_body(body_file)
    except (OSError, UnicodeDecodeError) as error:
        return fail("invalid_argument", f"Unable to read body file: {error}", 2)

    def operation(user: str) -> MessageSendResult:
        return context.service.reply_to_message(
            user, message_id, _reply(body, subject, content_type), reply_all=False
        )

    return run_write(context, operation)


def create_reply_draft(
    message_id: str,
    *,
    body_file: BodyFile,
    subject: str | None = None,
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Save a reply to one message in Drafts without sending it."""
    return _create_reply_draft(
        message_id,
        body_file=body_file,
        subject=subject,
        content_type=content_type,
        reply_all=False,
        context=context,
    )


def reply_all_to_message(
    message_id: str,
    *,
    body_file: BodyFile,
    subject: str | None = None,
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Reply to every recipient of one message and keep the EWS Sent copy."""
    try:
        body = _read_body(body_file)
    except (OSError, UnicodeDecodeError) as error:
        return fail("invalid_argument", f"Unable to read body file: {error}", 2)

    def operation(user: str) -> MessageSendResult:
        return context.service.reply_to_message(
            user, message_id, _reply(body, subject, content_type), reply_all=True
        )

    return run_write(context, operation)


def create_reply_all_draft(
    message_id: str,
    *,
    body_file: BodyFile,
    subject: str | None = None,
    content_type: ContentType = "text",
    context: ContextArgument,
) -> int:
    """Save a reply-all to one message in Drafts without sending it."""
    return _create_reply_draft(
        message_id,
        body_file=body_file,
        subject=subject,
        content_type=content_type,
        reply_all=True,
        context=context,
    )


def mark_read(
    message_id: str,
    *,
    unread: bool = False,
    context: ContextArgument,
) -> int:
    """Mark one message as read, or as unread with --unread."""
    return run_write(
        context,
        lambda user: context.service.set_read_state(user, message_id, is_read=not unread),
    )


def move_message(
    message_id: str,
    *,
    folder: str,
    context: ContextArgument,
) -> int:
    """Move one message into another folder."""
    return run_write(context, lambda user: context.service.move_message(user, message_id, folder))


def _reply(body: str, subject: str | None, content_type: ContentType) -> OutgoingReply:
    return OutgoingReply.model_validate(
        {
            "subject": subject,
            "body": {"content_type": content_type, "content": body},
        }
    )


def _create_reply_draft(
    message_id: str,
    *,
    body_file: str,
    subject: str | None,
    content_type: ContentType,
    reply_all: bool,
    context: CommandContext,
) -> int:
    try:
        body = _read_body(body_file)
    except (OSError, UnicodeDecodeError) as error:
        return fail("invalid_argument", f"Unable to read body file: {error}", 2)

    return run_write(
        context,
        lambda user: context.service.save_reply_draft(
            user,
            message_id,
            _reply(body, subject, content_type),
            reply_all=reply_all,
        ),
    )


def _read_body(body_file: str) -> str:
    """Read one message body from an explicit file; '-' reads standard input."""
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).read_text(encoding="utf-8")
