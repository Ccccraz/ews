from typing import Protocol

from pydantic import SecretStr

from ews.models import (
    ConnectionTestResult,
    Folder,
    MessageDetail,
    MessageListQuery,
    MessageSummary,
    Profile,
)


class MailboxGateway(Protocol):
    """Strict application boundary for mailbox operations."""

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult: ...

    def list_folders(self, profile: Profile, password: SecretStr) -> list[Folder]: ...

    def list_messages(
        self, profile: Profile, password: SecretStr, query: MessageListQuery
    ) -> tuple[list[MessageSummary], bool]: ...

    def get_message(
        self, profile: Profile, password: SecretStr, message_id: str
    ) -> MessageDetail: ...
