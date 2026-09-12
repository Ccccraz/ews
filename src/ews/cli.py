import sys
from collections.abc import Sequence
from typing import Annotated

import structlog
from cyclopts import App, Parameter
from truststore import inject_into_ssl

from ews import __version__
from ews.application import MailboxApplicationService, ProfileApplicationService
from ews.commands import (
    create_draft,
    create_reply_all_draft,
    create_reply_draft,
    delete_config,
    delete_password,
    doctor,
    get_contact,
    get_message,
    get_thread,
    list_config,
    list_contacts,
    list_folders,
    list_messages,
    mark_read,
    move_message,
    password_status,
    reply_all_to_message,
    reply_to_message,
    save_attachment,
    search_directory,
    send_message,
    set_password,
    set_profile,
    show_config,
    show_config_path,
    sync_mailbox,
    test_access,
)
from ews.commands.context import CommandContext, fail
from ews.config import PasswordStore, ProfileStore
from ews.exchange import EwsClient
from ews.system import LogFormat, LogLevel, SystemTlsProbe, configure_logging

logger = structlog.get_logger()

commands = App(
    name="ews",
    help="Connect AI agents to an Exchange Web Services mailbox.",
    version=__version__,
)
folder = App(name="folder", help="Read mail folders.")
message = App(name="message", help="Read and write messages.")
contact = App(name="contact", help="Read cached contacts.")
draft = App(name="draft", help="Save messages in Drafts without sending them.")
attachment = App(name="attachment", help="Read message attachments.")
config = App(name="config", help="Inspect configuration.")
auth = App(name="auth", help="Manage authentication.")

commands.command(set_profile, name="set")
commands.command(test_access, name="test")
commands.command(sync_mailbox, name="sync")
commands.command(doctor, name="doctor")
commands.command(folder)
commands.command(message)
commands.command(contact)
commands.command(attachment)
commands.command(config)
commands.command(auth)
folder.command(list_folders, name="list")
contact.command(list_contacts, name="list")
contact.command(get_contact, name="get")
contact.command(search_directory, name="search")
message.command(list_messages, name="list")
message.command(draft)
message.command(get_message, name="get")
message.command(get_thread, name="thread")
message.command(send_message, name="send")
message.command(reply_to_message, name="reply")
message.command(reply_all_to_message, name="reply-all")
message.command(mark_read, name="mark-read")
message.command(move_message, name="move")
draft.command(create_draft, name="create")
draft.command(create_reply_draft, name="reply")
draft.command(create_reply_all_draft, name="reply-all")
attachment.command(save_attachment, name="save")
config.command(show_config, name="show")
config.command(list_config, name="list")
config.command(delete_config, name="delete")
config.command(show_config_path, name="path")
auth.command(set_password, name="set-password")
auth.command(password_status, name="status")
auth.command(delete_password, name="delete-password")

app = commands.meta
app.help = commands.help
app.version = __version__


@app.default
def launch(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    user: str | None = None,
    log_level: LogLevel = LogLevel.WARNING,
    log_format: LogFormat = LogFormat.JSON,
) -> int:
    """Select a profile user, configure diagnostics and dispatch a resource command."""
    configure_logging(log_level, log_format)
    try:
        command, bound, ignored = commands.parse_args(tokens)
        if "context" in ignored:
            bound.arguments["context"] = _build_context(user)
        result = command(*bound.args, **bound.kwargs)
    except Exception as error:
        # Stdout must never degrade into a traceback: report the internal error as the
        # regular envelope and keep the traceback on the diagnostics channel.
        logger.exception("Unhandled internal error")
        return fail(
            "internal_error",
            "Unexpected internal error",
            1,
            details={"type": type(error).__name__},
        )
    return result if isinstance(result, int) else 0


def _build_context(user: str | None) -> CommandContext:
    profile_store = ProfileStore()
    password_store = PasswordStore()
    return CommandContext(
        user=user,
        service=MailboxApplicationService(
            profile_store,
            password_store,
            EwsClient(),
            tls_probe=SystemTlsProbe(),
        ),
        profiles=ProfileApplicationService(profile_store, password_store),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line application."""
    inject_into_ssl()
    app(sys.argv[1:] if argv is None else argv)
