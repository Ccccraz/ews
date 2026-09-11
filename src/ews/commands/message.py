from typing import Annotated

from cyclopts import Parameter

from ews.commands.context import CommandContext, run_read
from ews.models import MessageListQuery, MessageListResult


def list_messages(
    *,
    folder: str = "inbox",
    read_state: str = "any",
    sender: str | None = None,
    subject_contains: str | None = None,
    body_contains: str | None = None,
    received_from: str | None = None,
    received_before: str | None = None,
    offset: str = "0",
    limit: str = "50",
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """List messages using structured AND filters."""

    def operation(user: str) -> MessageListResult:
        query = MessageListQuery.model_validate(
            {
                "folder": folder,
                "read_state": read_state,
                "sender": sender,
                "subject_contains": subject_contains,
                "body_contains": body_contains,
                "received_from": received_from,
                "received_before": received_before,
                "offset": offset,
                "limit": limit,
            }
        )
        return context.service.list_messages(user, query)

    return run_read(context, operation)


def get_message(
    message_id: str,
    *,
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """Get one message with body, headers, and attachment metadata."""
    return run_read(context, lambda user: context.service.get_message(user, message_id))
