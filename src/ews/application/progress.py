from typing import Protocol

from ews.models import ContactSyncCounts, MailboxSyncResult, MessageSyncCounts


class SyncProgressReporter(Protocol):
    """Receive mailbox synchronization progress without depending on a UI."""

    def hierarchy_started(self) -> None:
        """Report that folder hierarchy synchronization started."""
        ...

    def hierarchy_completed(self, folder_total: int) -> None:
        """Report the number of visible folders to synchronize."""
        ...

    def folder_started(self, name: str, index: int, total: int) -> None:
        """Report that one folder started synchronizing."""
        ...

    def message_fetch_started(self, total: int) -> None:
        """Report how many complete messages need fetching."""
        ...

    def messages_fetched(self, count: int) -> None:
        """Report newly fetched complete messages."""
        ...

    def folder_completed(self, counts: MessageSyncCounts | ContactSyncCounts) -> None:
        """Report applied item changes for one folder."""
        ...

    def sync_completed(self, result: MailboxSyncResult) -> None:
        """Report that the mailbox synchronization completed."""
        ...
