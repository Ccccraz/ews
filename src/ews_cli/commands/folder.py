from typing import Annotated

from cyclopts import Parameter

from ews_cli.commands.context import CommandContext, run_read


def list_folders(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """List all folders that can contain messages."""
    return run_read(context, context.service.list_folders)
