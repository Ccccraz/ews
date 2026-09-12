from collections.abc import Sequence
from typing import Protocol

from pydantic import SecretStr

from ews.models import (
    ConnectionTestResult,
    FolderSyncResult,
    MessageDetail,
    MessageSyncResult,
    Profile,
)


class MailboxGateway(Protocol):
    """Strict application boundary for remote mailbox operations."""

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult: ...

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult: ...

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult: ...

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]: ...
