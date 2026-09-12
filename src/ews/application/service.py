from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr

from ews.application.errors import (
    AttachmentNotFoundError,
    FolderNotFoundError,
    InvalidSyncStateError,
    MessageNotFoundError,
    UnsupportedAttachmentError,
)
from ews.application.gateway import MailboxGateway
from ews.application.progress import SyncProgressReporter
from ews.application.tls import TlsProbe
from ews.config import PasswordStore, ProfileStore
from ews.models import (
    AttachmentSaveResult,
    ConnectionTestResult,
    DoctorResult,
    FolderListResult,
    MailboxSyncResult,
    MessageChangeKind,
    MessageDetail,
    MessageGetResult,
    MessageListQuery,
    MessageListResult,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    MessageSyncCounts,
    OutgoingMessage,
    OutgoingReply,
    Pagination,
    Profile,
)
from ews.storage import SqliteMailboxStore
from ews.system import SystemTlsProbe

_GET_ITEM_BATCH_SIZE = 10


class UserNotFoundError(Exception):
    """Raised when the selected user does not match the configured profile."""


class MailboxApplicationService:
    """Coordinate remote synchronization and local mailbox reads."""

    def __init__(
        self,
        profile_store: ProfileStore,
        password_store: PasswordStore,
        gateway: MailboxGateway,
        store: SqliteMailboxStore | None = None,
        tls_probe: TlsProbe | None = None,
    ) -> None:
        self._profile_store = profile_store
        self._password_store = password_store
        self._gateway = gateway
        self._store = SqliteMailboxStore() if store is None else store
        self._tls_probe = SystemTlsProbe() if tls_probe is None else tls_probe

    def test_access(self, selected_user: str) -> ConnectionTestResult:
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        return self._gateway.test_access(profile, password)

    def diagnose(self, selected_user: str | None = None) -> DoctorResult:
        """Verify profile, keychain, system TLS and EWS login in one run."""
        profile = self._profile_store.load()
        if selected_user is not None:
            _require_selected_user(profile, selected_user)
        password = self._password_store.get(profile)
        tls = self._tls_probe.probe(profile)
        return DoctorResult(
            profile_path=self._profile_store.path,
            endpoint=profile.server.endpoint,
            tls=tls,
            connection=self._gateway.test_access(profile, password),
        )

    def sync(
        self,
        selected_user: str,
        *,
        progress: SyncProgressReporter | None = None,
    ) -> MailboxSyncResult:
        """Synchronize hierarchy first, then every visible mail folder."""
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        mailbox = str(profile.user.mailbox)
        self._store.initialize()

        if progress is not None:
            progress.hierarchy_started()
        hierarchy_state = self._store.get_hierarchy_sync_state(mailbox)
        hierarchy_reset = hierarchy_state is None
        try:
            hierarchy = self._gateway.sync_hierarchy(profile, password, hierarchy_state)
        except InvalidSyncStateError:
            hierarchy = self._gateway.sync_hierarchy(profile, password, None)
            hierarchy_reset = True
        folder_counts = self._store.apply_folder_changes(
            mailbox,
            hierarchy.changes,
            hierarchy.sync_state,
            reset=hierarchy_reset,
            well_known_folder_ids=hierarchy.well_known_folder_ids,
        )

        folders = self._store.list_folders(mailbox)
        folder_total = len(folders)
        if progress is not None:
            progress.hierarchy_completed(folder_total)

        message_counts = MessageSyncCounts()
        for index, folder in enumerate(folders, start=1):
            if progress is not None:
                progress.folder_started(folder.name, index, folder_total)
            item_state = self._store.get_item_sync_state(mailbox, folder.id)
            item_reset = item_state is None
            try:
                item_result = self._gateway.sync_items(profile, password, folder.id, item_state)
            except InvalidSyncStateError:
                item_result = self._gateway.sync_items(profile, password, folder.id, None)
                item_reset = True

            message_ids = [
                (change.message_id, change.change_key)
                for change in item_result.changes
                if change.kind in {MessageChangeKind.CREATE, MessageChangeKind.UPDATE}
                and change.change_key is not None
            ]
            if progress is not None:
                progress.message_fetch_started(len(message_ids))
            fetched = self._fetch_in_batches(
                profile,
                password,
                message_ids,
                progress=progress,
            )
            applied = self._store.apply_message_changes(
                mailbox,
                folder.id,
                item_result.changes,
                {message.id: message for message in fetched},
                item_result.sync_state,
                reset=item_reset,
            )
            message_counts = _add_message_counts(message_counts, applied)
            if progress is not None:
                progress.folder_completed(applied)

        self._store.mark_ready(mailbox)
        result = MailboxSyncResult(
            user=profile.user.username,
            folders=folder_counts,
            messages=message_counts,
        )
        if progress is not None:
            progress.sync_completed(result)
        return result

    def list_folders(self, selected_user: str) -> FolderListResult:
        profile = self._load_profile(selected_user)
        mailbox = str(profile.user.mailbox)
        self._store.require_ready(mailbox)
        return FolderListResult(
            user=profile.user.username,
            folders=self._store.list_folders(mailbox),
        )

    def list_messages(self, selected_user: str, query: MessageListQuery) -> MessageListResult:
        profile = self._load_profile(selected_user)
        mailbox = str(profile.user.mailbox)
        self._store.require_ready(mailbox)
        if not self._store.folder_exists(mailbox, query.folder):
            raise FolderNotFoundError(f"Folder not found: {query.folder}")
        messages, has_more = self._store.list_messages(mailbox, query)
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
        mailbox = str(profile.user.mailbox)
        self._store.require_ready(mailbox)
        message = self._store.get_message(mailbox, message_id)
        if message is None:
            raise MessageNotFoundError(f"Message not found: {message_id}")
        return MessageGetResult(user=profile.user.username, message=message)

    def send_message(self, selected_user: str, message: OutgoingMessage) -> MessageSendResult:
        """Send one new message; the local cache is left for the next synchronization."""
        profile = self._load_profile(selected_user)
        password = self._password_store.get(profile)
        return self._gateway.send_message(profile, password, message)

    def reply_to_message(
        self,
        selected_user: str,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageSendResult:
        """Reply to one cached message without changing the local cache."""
        profile = self._load_profile(selected_user)
        self._require_cached_message(profile, message_id)
        password = self._password_store.get(profile)
        return self._gateway.reply_message(
            profile, password, message_id, reply, reply_all=reply_all
        )

    def set_read_state(
        self, selected_user: str, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        """Update one cached message's remote read state without changing the local cache."""
        profile = self._load_profile(selected_user)
        self._require_cached_message(profile, message_id)
        password = self._password_store.get(profile)
        return self._gateway.set_read_state(profile, password, message_id, is_read=is_read)

    def move_message(self, selected_user: str, message_id: str, folder: str) -> MessageMoveResult:
        """Move one cached message into a cached folder without changing the local cache."""
        profile = self._load_profile(selected_user)
        self._require_cached_message(profile, message_id)
        folder_id = self._store.resolve_folder_id(str(profile.user.mailbox), folder)
        if folder_id is None:
            raise FolderNotFoundError(f"Folder not found: {folder}")
        password = self._password_store.get(profile)
        return self._gateway.move_message(profile, password, message_id, folder_id)

    def save_attachment(
        self,
        selected_user: str,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        """Stream one cached message's file attachment to a local path."""
        profile = self._load_profile(selected_user)
        message = self._require_cached_message(profile, message_id)
        attachment = next(
            (item for item in message.attachments if item.id == attachment_id),
            None,
        )
        if attachment is None:
            raise AttachmentNotFoundError(f"Attachment not found: {attachment_id}")
        if attachment.kind != "file":
            raise UnsupportedAttachmentError(f"Only file attachments can be saved: {attachment_id}")
        password = self._password_store.get(profile)
        return self._gateway.save_attachment(
            profile, password, message_id, attachment_id, destination
        )

    def _require_cached_message(self, profile: Profile, message_id: str) -> MessageDetail:
        mailbox = str(profile.user.mailbox)
        self._store.require_ready(mailbox)
        message = self._store.get_message(mailbox, message_id)
        if message is None:
            raise MessageNotFoundError(f"Message not found: {message_id}")
        return message

    def _fetch_in_batches(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
        *,
        progress: SyncProgressReporter | None,
    ) -> list[MessageDetail]:
        fetched: list[MessageDetail] = []
        for start in range(0, len(message_ids), _GET_ITEM_BATCH_SIZE):
            batch = message_ids[start : start + _GET_ITEM_BATCH_SIZE]
            fetched.extend(self._gateway.fetch_messages(profile, password, batch))
            if progress is not None:
                progress.messages_fetched(len(batch))
        return fetched

    def _load_profile(self, selected_user: str) -> Profile:
        profile = self._profile_store.load()
        _require_selected_user(profile, selected_user)
        return profile


def _require_selected_user(profile: Profile, selected_user: str) -> None:
    expected = selected_user.casefold()
    if expected not in {
        profile.user.username.casefold(),
        str(profile.user.mailbox).casefold(),
    }:
        raise UserNotFoundError(f"Profile not found for user: {selected_user}")


def _add_message_counts(left: MessageSyncCounts, right: MessageSyncCounts) -> MessageSyncCounts:
    return MessageSyncCounts(
        created=left.created + right.created,
        updated=left.updated + right.updated,
        deleted=left.deleted + right.deleted,
        read_state_changed=left.read_state_changed + right.read_state_changed,
    )
