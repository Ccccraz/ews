import sys
from types import TracebackType
from typing import Annotated

from cyclopts import Parameter
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn

from ews_cli.application import SyncProgressReporter
from ews_cli.commands.context import CommandContext, run_read
from ews_cli.models import ContactSyncCounts, MailboxSyncResult, MessageSyncCounts


def sync_mailbox(
    *,
    progress: bool = False,
    context: Annotated[CommandContext, Parameter(parse=False, show=False)],
) -> int:
    """Synchronize the remote mailbox into the local cache."""
    if progress:
        return _sync_with_progress(context)
    return run_read(context, context.service.sync)


def _sync_with_progress(context: CommandContext) -> int:
    def operation(user: str) -> MailboxSyncResult:
        with RichSyncProgress() as reporter:
            return context.service.sync(user, progress=reporter)

    return run_read(context, operation)


class RichSyncProgress(SyncProgressReporter):
    """Render explicitly requested synchronization progress to stdout."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=Console(file=sys.stdout),
        )
        self._hierarchy_task: TaskID | None = None
        self._folder_task: TaskID | None = None
        self._message_task: TaskID | None = None

    def __enter__(self) -> RichSyncProgress:
        self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._progress.stop()

    def hierarchy_started(self) -> None:
        self._hierarchy_task = self._progress.add_task("Synchronizing folder hierarchy", total=None)

    def hierarchy_completed(self, folder_total: int) -> None:
        if self._hierarchy_task is not None:
            self._progress.update(
                self._hierarchy_task,
                description=f"Found {folder_total} visible folders",
                total=1,
                completed=1,
            )
        self._folder_task = self._progress.add_task("Synchronizing folders", total=folder_total)

    def folder_started(self, name: str, index: int, total: int) -> None:
        if self._folder_task is not None:
            self._progress.update(
                self._folder_task,
                description=f"[{index}/{total}] {name}",
            )

    def message_fetch_started(self, total: int) -> None:
        if self._message_task is not None:
            self._progress.remove_task(self._message_task)
            self._message_task = None
        if total > 0:
            self._message_task = self._progress.add_task("Fetching messages", total=total)

    def messages_fetched(self, count: int) -> None:
        if self._message_task is not None:
            self._progress.advance(self._message_task, count)

    def folder_completed(self, counts: MessageSyncCounts | ContactSyncCounts) -> None:
        if self._message_task is not None:
            self._progress.remove_task(self._message_task)
            self._message_task = None
        if self._folder_task is not None:
            self._progress.advance(self._folder_task)
            self._progress.update(
                self._folder_task,
                description=_applied_description(counts),
            )

    def sync_completed(self, result: MailboxSyncResult) -> None:
        del result
        if self._folder_task is not None:
            self._progress.update(self._folder_task, description="Synchronization complete")


def _applied_description(counts: MessageSyncCounts | ContactSyncCounts) -> str:
    if isinstance(counts, ContactSyncCounts):
        return (
            "Applied contacts: "
            f"{counts.created} created, {counts.updated} updated, {counts.deleted} deleted"
        )
    return (
        "Applied messages: "
        f"{counts.created} created, {counts.updated} updated, "
        f"{counts.deleted} deleted, "
        f"{counts.read_state_changed} read-state changes"
    )
