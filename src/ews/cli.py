import sys
from collections.abc import Sequence

from cyclopts import App

from ews import __version__
from ews.commands import set_profile, test_access

app = App(
    name="ews",
    help="Connect AI agents to an Exchange Web Services mailbox.",
    version=__version__,
)
app.command(set_profile, name="set")
app.command(test_access, name="test")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    app(sys.argv[1:] if argv is None else argv)
