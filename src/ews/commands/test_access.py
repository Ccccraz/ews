from typing import Annotated

from cyclopts import Parameter

from ews.commands.context import CommandContext, run_read


def test_access(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Test authenticated EWS access for the configured user."""
    return run_read(context, context.service.test_access)
