import sys
from collections.abc import Sequence
from typing import Annotated

from cyclopts import App, Parameter
from truststore import inject_into_ssl

from ews import __version__
from ews.application import MailboxApplicationService
from ews.commands import (
    get_message,
    list_folders,
    list_messages,
    set_profile,
    sync_mailbox,
    test_access,
)
from ews.commands.context import CommandContext
from ews.config import PasswordStore, ProfileStore
from ews.exchange import EwsClient

commands = App(
    name="ews",
    help="Connect AI agents to an Exchange Web Services mailbox.",
    version=__version__,
)
folder = App(name="folder", help="Read mail folders.")
message = App(name="message", help="Read messages.")

commands.command(set_profile, name="set")
commands.command(test_access, name="test")
commands.command(sync_mailbox, name="sync")
commands.command(folder)
commands.command(message)
folder.command(list_folders, name="list")
message.command(list_messages, name="list")
message.command(get_message, name="get")

app = commands.meta
app.help = commands.help
app.version = __version__


@app.default
def launch(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    user: str | None = None,
) -> int:
    """Select a profile user and dispatch a resource command."""
    command, bound, ignored = commands.parse_args(tokens)
    if "context" in ignored:
        bound.arguments["context"] = _build_context(user)
    result = command(*bound.args, **bound.kwargs)
    return result if isinstance(result, int) else 0


def _build_context(user: str | None) -> CommandContext:
    return CommandContext(
        user=user,
        service=MailboxApplicationService(ProfileStore(), PasswordStore(), EwsClient()),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    inject_into_ssl()
    app(sys.argv[1:] if argv is None else argv)
