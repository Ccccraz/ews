from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from ews_cli.commands.context import CommandContext, run_write


def save_attachment(
    message_id: str,
    attachment_id: str,
    *,
    path: str,
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """Stream one file attachment to an explicit local path."""
    destination = Path(path)
    return run_write(
        context,
        lambda user: context.service.save_attachment(user, message_id, attachment_id, destination),
    )
