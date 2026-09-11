from ews.application.gateway import MailboxGateway
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    ConnectionTestResult,
    FolderListResult,
    MessageGetResult,
    MessageListQuery,
    MessageListResult,
    Pagination,
    Profile,
)


class UserNotFoundError(Exception):
    """Raised when the selected user does not match the configured profile."""


class MailboxApplicationService:
    """Load profile context and coordinate mailbox read use cases."""

    def __init__(
        self,
        profile_store: ProfileStore,
        password_store: PasswordStore,
        gateway: MailboxGateway,
    ) -> None:
        self._profile_store = profile_store
        self._password_store = password_store
        self._gateway = gateway

    def test_access(self, selected_user: str) -> ConnectionTestResult:
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        return self._gateway.test_access(profile, password)

    def list_folders(self, selected_user: str) -> FolderListResult:
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        return FolderListResult(
            user=profile.user.username,
            folders=self._gateway.list_folders(profile, password),
        )

    def list_messages(self, selected_user: str, query: MessageListQuery) -> MessageListResult:
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        messages, has_more = self._gateway.list_messages(profile, password, query)
        return MessageListResult(
            user=profile.user.username,
            messages=messages,
            pagination=Pagination(
                offset=query.offset,
                limit=query.limit,
                has_more=has_more,
                next_offset=query.offset + query.limit if has_more else None,
            ),
        )

    def get_message(self, selected_user: str, message_id: str) -> MessageGetResult:
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        return MessageGetResult(
            user=profile.user.username,
            message=self._gateway.get_message(profile, password, message_id),
        )

    def _load_profile(self, selected_user: str) -> Profile:
        profile = self._profile_store.load()
        expected = selected_user.casefold()
        if expected not in {
            profile.user.username.casefold(),
            str(profile.user.mailbox).casefold(),
        }:
            raise UserNotFoundError(f"Profile not found for user: {selected_user}")
        return profile
