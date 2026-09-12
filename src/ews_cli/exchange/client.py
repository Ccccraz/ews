# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import shutil
import warnings
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials
from exchangelib.attachments import FileAttachment
from exchangelib.errors import (
    ErrorAccessDenied,
    ErrorAttachmentSizeLimitExceeded,
    ErrorFolderNotFound,
    ErrorInvalidAttachmentId,
    ErrorInvalidFolderId,
    ErrorInvalidIdEmpty,
    ErrorInvalidIdMalformed,
    ErrorInvalidRecipients,
    ErrorInvalidSyncStateData,
    ErrorItemNotFound,
    ErrorMessageSizeExceeded,
    ErrorNameResolutionNoResults,
    ErrorNonExistentMailbox,
    ErrorSendAsDenied,
    EWSError,
    UnauthorizedError,
)
from exchangelib.extended_properties import ExtendedProperty
from exchangelib.fields import InvalidField
from exchangelib.folders import Folder as EwsFolder
from exchangelib.items import Contact as EwsContact
from exchangelib.items import Message
from exchangelib.items.calendar_item import BaseMeetingItem
from exchangelib.properties import HTMLBody, ItemId, Mailbox
from pydantic import EmailStr, SecretStr, ValidationError

from ews_cli.application import (
    AttachmentNotFoundError,
    DestinationExistsError,
    InvalidDestinationError,
    InvalidSyncStateError,
    UnsupportedAttachmentError,
)
from ews_cli.models import (
    AttachmentMetadata,
    AttachmentSaveResult,
    ConnectionTestResult,
    Contact,
    ContactAddress,
    ContactChange,
    ContactChangeKind,
    ContactEmail,
    ContactIm,
    ContactPhone,
    ContactSyncResult,
    DirectoryContact,
    DirectorySearchResult,
    DraftMessage,
    FlagStatus,
    Folder,
    FolderChange,
    FolderChangeKind,
    FolderKind,
    FolderSyncResult,
    Importance,
    InternetHeader,
    MailboxAddress,
    MessageBody,
    MessageChange,
    MessageChangeKind,
    MessageDetail,
    MessageDraftResult,
    MessageMoveResult,
    MessageReadStateResult,
    MessageSendResult,
    MessageSyncResult,
    OutgoingMessage,
    OutgoingReply,
    Profile,
)

DETAIL_FIELDS = (
    "parent_folder_id",
    "subject",
    "author",
    "datetime_received",
    "is_read",
    "has_attachments",
    "importance",
    "sender",
    "to_recipients",
    "cc_recipients",
    "bcc_recipients",
    "reply_to",
    "datetime_sent",
    "datetime_created",
    "message_id",
    "in_reply_to",
    "body",
    "headers",
    "attachments",
    "conversation_id",
    "conversation_topic",
    "conversation_index",
    "text_body",
    "references",
    "is_draft",
    "categories",
    "flag_status",
)
FOLDER_SYNC_FIELDS = ("parent_folder_id", "total_count", "unread_count", "is_hidden")
REPLY_FIELDS = ("subject", "author", "to_recipients", "cc_recipients", "bcc_recipients")
MAIL_FOLDER_CLASSES = {"IPF.Note", "IPF.Note.OutlookHomepage", "IPF.StickyNote"}
CONTACT_FOLDER_CLASS = "IPF.Contact"
CONTACT_FIELDS = (
    "parent_folder_id",
    "display_name",
    "file_as",
    "given_name",
    "middle_name",
    "surname",
    "nickname",
    "initials",
    "generation",
    "company_name",
    "department",
    "job_title",
    "office",
    "manager",
    "profession",
    "business_homepage",
    "email_addresses",
    "phone_numbers",
    "physical_addresses",
    "im_addresses",
    "categories",
    "notes",
    "birthday",
    "has_picture",
)
# Distinguished mail folders, keyed by the name the cache stores and mapped to the Account
# attribute that resolves them. A hierarchy synchronization response carries no
# distinguished id, so these are resolved through the Account and matched by folder id; a
# folder that resolves to none of these ids keeps a null name.
WELL_KNOWN_MAIL_FOLDERS = {
    "inbox": "inbox",
    "sentitems": "sent",
    "drafts": "drafts",
    "deleteditems": "trash",
    "junkemail": "junk",
    "outbox": "outbox",
    "notes": "notes",
    "conversationhistory": "conversation_history",
    "syncissues": "sync_issues",
    "conflicts": "conflicts",
    "localfailures": "local_failures",
    "serverfailures": "server_failures",
}
# The only distinguished personal-contacts folder. Subfolders inherit the class instead.
WELL_KNOWN_CONTACT_FOLDERS = {"contacts": "contacts"}
CONVERSATION_ROOT_INDEX_LENGTH = 22
CONVERSATION_REPLY_INDEX_LENGTH = 5


class IsHidden(ExtendedProperty):
    """PidTagAttributeHidden (0x10F4) folder property."""

    property_tag = 0x10F4
    property_type = "Boolean"


class FlagStatusProperty(ExtendedProperty):
    """PidTagFlagStatus (0x1090) message property."""

    property_tag = 0x1090
    property_type = "Integer"


class EwsAuthenticationError(Exception):
    """Raised when EWS rejects the configured identity or credentials."""


class EwsNotFoundError(Exception):
    """Raised when EWS cannot find a message or folder named by the caller."""


class EwsRejectedError(Exception):
    """Raised when EWS rejects the requested mailbox change as invalid."""


class EwsServiceError(Exception):
    """Raised when EWS cannot complete a mailbox operation."""


class EwsClient:
    """Perform synchronous remote operations against one EWS profile."""

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        def operation(account: Any) -> ConnectionTestResult:
            inbox = account.inbox
            inbox.refresh()
            return ConnectionTestResult(
                user=profile.user.username,
                mailbox=profile.user.mailbox,
                server_version=str(account.version),
                inbox_total_count=inbox.total_count,
                inbox_unread_count=inbox.unread_count,
            )

        return self._run(profile, password, operation)

    def sync_hierarchy(
        self, profile: Profile, password: SecretStr, sync_state: str | None
    ) -> FolderSyncResult:
        """Fully consume SyncFolderHierarchy and return its new state."""

        def operation(account: Any) -> FolderSyncResult:
            root = account.msg_folder_root
            named_mail_folders = _distinguished_mail_folders(account)
            named_contact_folders = _distinguished_contact_folders(account)
            mail_well_known_ids = {str(folder.id) for folder in named_mail_folders.values()}
            raw_changes = list(
                root.sync_hierarchy(sync_state=sync_state, only_fields=FOLDER_SYNC_FIELDS)
            )
            changes = [
                change
                for kind, folder in raw_changes
                if (change := _folder_change(kind, folder, mail_well_known_ids)) is not None
            ]
            changes.extend(_unreported_named_folders(named_mail_folders, changes, FolderKind.MAIL))
            changes.extend(
                _unreported_named_folders(named_contact_folders, changes, FolderKind.CONTACTS)
            )
            new_state = root.folder_sync_state
            if not new_state:
                raise EwsServiceError("EWS did not return a folder synchronization state")
            return FolderSyncResult(
                changes=changes,
                sync_state=str(new_state),
                well_known_folder_ids={
                    name: str(folder.id)
                    for name, folder in {**named_mail_folders, **named_contact_folders}.items()
                },
            )

        return self._run(profile, password, operation)

    def sync_items(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> MessageSyncResult:
        """Fully consume IdOnly SyncFolderItems for one visible mail folder."""

        def operation(account: Any) -> MessageSyncResult:
            folder = _folder_from_id(account, folder_id)
            raw_changes = list(folder.sync_items(sync_state=sync_state, only_fields=[]))
            changes = [
                change
                for kind, item in raw_changes
                if (change := _message_change(kind, item)) is not None
            ]
            new_state = folder.item_sync_state
            if not new_state:
                raise EwsServiceError("EWS did not return an item synchronization state")
            return MessageSyncResult(changes=changes, sync_state=str(new_state))

        return self._run(profile, password, operation)

    def fetch_messages(
        self,
        profile: Profile,
        password: SecretStr,
        message_ids: Sequence[tuple[str, str]],
    ) -> list[MessageDetail]:
        """Fetch at most ten complete messages with GetItem."""
        if len(message_ids) > 10:
            raise ValueError("GetItem batches must contain at most 10 messages")
        if not message_ids:
            return []

        def operation(account: Any) -> list[MessageDetail]:
            details: list[MessageDetail] = []
            for item in account.fetch(ids=message_ids, only_fields=DETAIL_FIELDS):
                if isinstance(item, Exception):
                    raise item
                if not _is_supported_message_item(item):
                    raise EwsServiceError("EWS returned a non-message GetItem result")
                details.append(_message_detail(item))
            if len(details) != len(message_ids):
                raise EwsServiceError("EWS returned an incomplete GetItem response")
            return details

        return self._run(profile, password, operation)

    def sync_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        folder_id: str,
        sync_state: str | None,
    ) -> ContactSyncResult:
        """Fully consume IdOnly SyncFolderItems for one contact folder."""

        def operation(account: Any) -> ContactSyncResult:
            folder = _folder_from_id(account, folder_id)
            raw_changes = list(folder.sync_items(sync_state=sync_state, only_fields=[]))
            changes = [
                change
                for kind, item in raw_changes
                if (change := _contact_change(kind, item)) is not None
            ]
            new_state = folder.item_sync_state
            if not new_state:
                raise EwsServiceError("EWS did not return an item synchronization state")
            return ContactSyncResult(changes=changes, sync_state=str(new_state))

        return self._run(profile, password, operation)

    def fetch_contacts(
        self,
        profile: Profile,
        password: SecretStr,
        contact_ids: Sequence[tuple[str, str]],
    ) -> list[Contact]:
        """Fetch at most ten complete contacts with GetItem."""
        if len(contact_ids) > 10:
            raise ValueError("GetItem batches must contain at most 10 contacts")
        if not contact_ids:
            return []

        def operation(account: Any) -> list[Contact]:
            contacts: list[Contact] = []
            for item in account.fetch(ids=contact_ids, only_fields=CONTACT_FIELDS):
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, EwsContact):
                    raise EwsServiceError("EWS returned a non-contact GetItem result")
                contacts.append(_contact_detail(item))
            if len(contacts) != len(contact_ids):
                raise EwsServiceError("EWS returned an incomplete GetItem response")
            return contacts

        return self._run(profile, password, operation)

    def search_directory(
        self, profile: Profile, password: SecretStr, query: str
    ) -> DirectorySearchResult:
        """Search the Exchange directory (GAL) for people and distribution lists."""

        def operation(account: Any) -> DirectorySearchResult:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                raw = account.protocol.resolve_names(
                    [query],
                    return_full_contact_data=True,
                    search_scope="ActiveDirectory",
                    shape="AllProperties",
                )
            truncated = any("at most 100" in str(warning.message) for warning in caught)
            contacts: list[DirectoryContact] = []
            for item in raw:
                if isinstance(item, ErrorNameResolutionNoResults):
                    continue
                if isinstance(item, Exception):
                    raise item
                contacts.append(_directory_contact(item))
            return DirectorySearchResult(
                user=profile.user.username,
                query=query,
                contacts=contacts,
                truncated=truncated,
            )

        return self._run(profile, password, operation)

    def send_message(
        self, profile: Profile, password: SecretStr, message: OutgoingMessage
    ) -> MessageSendResult:
        """Send a new message and keep the EWS Sent copy."""

        def operation(account: Any) -> MessageSendResult:
            outgoing = Message(
                account=account,
                to_recipients=_mailboxes(message.to),
                cc_recipients=_mailboxes(message.cc),
                bcc_recipients=_mailboxes(message.bcc),
                subject=message.subject,
                body=_outgoing_body(message.body),
            )
            outgoing.send(save_copy=True)
            return MessageSendResult(
                user=profile.user.username,
                subject=message.subject,
                to=_mail_addresses_from(message.to),
                cc=_mail_addresses_from(message.cc),
                bcc=_mail_addresses_from(message.bcc),
            )

        return self._run_write(profile, password, operation)

    def save_message_draft(
        self, profile: Profile, password: SecretStr, message: DraftMessage
    ) -> MessageDraftResult:
        """Save a new message in the EWS Drafts folder without sending it."""

        def operation(account: Any) -> MessageDraftResult:
            drafts = account.drafts
            outgoing = Message(
                account=account,
                folder=drafts,
                to_recipients=_mailboxes(message.to),
                cc_recipients=_mailboxes(message.cc),
                bcc_recipients=_mailboxes(message.bcc),
                subject=message.subject,
                body=_outgoing_body(message.body),
            )
            saved = outgoing.save()
            return _draft_result(
                profile=profile,
                saved=saved,
                outgoing=outgoing,
                folder_id=drafts.id,
                subject=message.subject,
            )

        return self._run_write(profile, password, operation)

    def reply_message(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageSendResult:
        """Reply to one message and keep the EWS Sent copy."""

        def operation(account: Any) -> MessageSendResult:
            original = _fetch_item(account, message_id, REPLY_FIELDS)
            subject = _reply_subject(original.subject, reply.subject)
            body = _outgoing_body(reply.body)
            if reply_all:
                outgoing = original.create_reply_all(subject, body)
            else:
                if original.author is None:
                    raise EwsRejectedError("The original message has no sender to reply to")
                outgoing = original.create_reply(subject, body)
            outgoing.send(save_copy=True)
            return MessageSendResult(
                user=profile.user.username,
                subject=subject,
                to=_sorted_mail_addresses(outgoing.to_recipients),
                cc=_sorted_mail_addresses(outgoing.cc_recipients),
                bcc=_sorted_mail_addresses(outgoing.bcc_recipients),
            )

        return self._run_write(profile, password, operation)

    def save_reply_draft(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        reply: OutgoingReply,
        *,
        reply_all: bool,
    ) -> MessageDraftResult:
        """Save a reply in the EWS Drafts folder without sending it."""

        def operation(account: Any) -> MessageDraftResult:
            original = _fetch_item(account, message_id, REPLY_FIELDS)
            subject = _reply_subject(original.subject, reply.subject)
            body = _outgoing_body(reply.body)
            if reply_all:
                outgoing = original.create_reply_all(subject, body)
            else:
                if original.author is None:
                    raise EwsRejectedError("The original message has no sender to reply to")
                outgoing = original.create_reply(subject, body)
            drafts = account.drafts
            saved = outgoing.save(drafts)
            return _draft_result(
                profile=profile,
                saved=saved,
                outgoing=outgoing,
                folder_id=drafts.id,
                subject=subject,
            )

        return self._run_write(profile, password, operation)

    def set_read_state(
        self, profile: Profile, password: SecretStr, message_id: str, *, is_read: bool
    ) -> MessageReadStateResult:
        """Update the read state of one message."""

        def operation(account: Any) -> MessageReadStateResult:
            item = _fetch_item(account, message_id, ())
            item.is_read = is_read
            item.save(update_fields=["is_read"])
            # Reading the state back is the only reliable way to report the change key:
            # the value exchangelib leaves on the local item after save() does not match
            # the change key the server stores for the updated message.
            updated = _fetch_item(account, message_id, ("is_read",))
            return MessageReadStateResult(
                user=profile.user.username,
                message_id=str(updated.id),
                change_key=str(updated.changekey),
                is_read=bool(updated.is_read),
            )

        return self._run_write(profile, password, operation)

    def move_message(
        self, profile: Profile, password: SecretStr, message_id: str, folder_id: str
    ) -> MessageMoveResult:
        """Move one message into another folder."""

        def operation(account: Any) -> MessageMoveResult:
            item = _fetch_item(account, message_id, ())
            item.move(_folder_from_id(account, folder_id))
            return MessageMoveResult(
                user=profile.user.username,
                previous_message_id=message_id,
                message_id=str(item.id),
                change_key=str(item.changekey),
                folder_id=folder_id,
            )

        return self._run_write(profile, password, operation)

    def save_attachment(
        self,
        profile: Profile,
        password: SecretStr,
        message_id: str,
        attachment_id: str,
        destination: Path,
    ) -> AttachmentSaveResult:
        """Stream one file attachment to an explicit local path."""

        def operation(account: Any) -> AttachmentSaveResult:
            _require_destination(destination)
            message = _fetch_item(account, message_id, ("attachments",))
            attachment = _find_file_attachment(message, attachment_id)
            try:
                target = destination.open("xb")
            except FileExistsError as error:
                raise DestinationExistsError(
                    f"Destination already exists: {destination}"
                ) from error
            except OSError as error:
                raise InvalidDestinationError(
                    f"Unable to create destination: {destination}: {error}"
                ) from error
            try:
                with target, attachment.fp as source:
                    shutil.copyfileobj(source, target)
            except EWSError:
                destination.unlink(missing_ok=True)
                raise
            except OSError as error:
                destination.unlink(missing_ok=True)
                raise EwsServiceError("Unable to download the attachment") from error
            return AttachmentSaveResult(
                user=profile.user.username,
                message_id=message_id,
                attachment_id=attachment_id,
                name=str(attachment.name or ""),
                content_type=None
                if attachment.content_type is None
                else str(attachment.content_type),
                path=destination,
                bytes_written=destination.stat().st_size,
            )

        return self._run_write(profile, password, operation)

    @staticmethod
    def _run[ResultT](
        profile: Profile,
        password: SecretStr,
        operation: Callable[[Any], ResultT],
    ) -> ResultT:
        try:
            return _with_account(profile, password, operation)
        except ErrorInvalidSyncStateData as error:
            raise InvalidSyncStateError("EWS synchronization state is no longer valid") from error
        except (UnauthorizedError, ErrorAccessDenied, ErrorNonExistentMailbox) as error:
            raise EwsAuthenticationError(
                "EWS rejected the configured credentials or mailbox"
            ) from error
        except (ErrorItemNotFound, ErrorInvalidIdMalformed) as error:
            raise EwsServiceError("EWS could not fetch a synchronized message") from error
        except EWSError as error:
            raise EwsServiceError("Unable to access the EWS service") from error
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise EwsServiceError("EWS returned data that could not be normalized") from error

    @staticmethod
    def _run_write[ResultT](
        profile: Profile,
        password: SecretStr,
        operation: Callable[[Any], ResultT],
    ) -> ResultT:
        try:
            return _with_account(profile, password, operation)
        except (
            UnauthorizedError,
            ErrorAccessDenied,
            ErrorNonExistentMailbox,
            ErrorSendAsDenied,
        ) as error:
            raise EwsAuthenticationError(
                "EWS rejected the configured credentials or mailbox"
            ) from error
        except (
            ErrorItemNotFound,
            ErrorInvalidIdMalformed,
            ErrorInvalidIdEmpty,
            ErrorInvalidAttachmentId,
            ErrorFolderNotFound,
            ErrorInvalidFolderId,
        ) as error:
            raise EwsNotFoundError("EWS could not find the requested mailbox resource") from error
        except (
            ErrorInvalidRecipients,
            ErrorMessageSizeExceeded,
            ErrorAttachmentSizeLimitExceeded,
        ) as error:
            raise EwsRejectedError("EWS rejected the requested mailbox change") from error
        except EWSError as error:
            raise EwsServiceError("Unable to access the EWS service") from error
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise EwsServiceError("EWS returned data that could not be normalized") from error


def _with_account[ResultT](
    profile: Profile,
    password: SecretStr,
    operation: Callable[[Any], ResultT],
) -> ResultT:
    _ensure_folder_extensions()
    _ensure_item_extensions()
    credentials = Credentials(
        username=profile.user.username,
        password=password.get_secret_value(),
    )
    configuration = Configuration(
        service_endpoint=str(profile.server.endpoint),
        credentials=credentials,
        auth_type=NTLM,
    )
    account = Account(
        primary_smtp_address=str(profile.user.mailbox),
        config=configuration,
        autodiscover=False,
        access_type=DELEGATE,
    )
    return operation(account)


def _fetch_item(account: Any, message_id: str, only_fields: Sequence[str]) -> Any:
    for item in account.fetch(ids=[ItemId(id=message_id)], only_fields=list(only_fields)):
        if isinstance(item, Exception):
            raise item
        return item
    raise EwsNotFoundError("EWS did not return the requested message")


def _find_file_attachment(message: Any, attachment_id: str) -> Any:
    for attachment in message.attachments or []:
        if str(attachment.attachment_id.id) != attachment_id:
            continue
        if not isinstance(attachment, FileAttachment):
            raise UnsupportedAttachmentError(f"Only file attachments can be saved: {attachment_id}")
        return attachment
    raise AttachmentNotFoundError(f"Attachment not found: {attachment_id}")


def _require_destination(destination: Path) -> None:
    if not destination.parent.is_dir():
        raise InvalidDestinationError(f"Destination directory does not exist: {destination.parent}")
    if destination.is_dir():
        raise InvalidDestinationError(f"Destination is a directory: {destination}")


def _mailboxes(addresses: Sequence[EmailStr]) -> list[Any]:
    return [Mailbox(email_address=str(address)) for address in addresses]


def _outgoing_body(body: MessageBody) -> Any:
    return HTMLBody(body.content) if body.content_type == "html" else body.content


def _reply_subject(original_subject: Any, requested: str | None) -> str:
    if requested is not None:
        return requested
    subject = "" if original_subject is None else str(original_subject)
    if not subject:
        return ""
    return subject if subject.casefold().startswith("re:") else f"RE: {subject}"


def _mail_addresses_from(addresses: Sequence[EmailStr]) -> list[MailboxAddress]:
    return [MailboxAddress(address=str(address)) for address in addresses]


def _sorted_mail_addresses(mailboxes: Iterable[Any] | None) -> list[MailboxAddress]:
    addresses = _mail_addresses(mailboxes)
    return sorted(addresses, key=lambda address: address.address.casefold())


def _draft_result(
    *,
    profile: Profile,
    saved: Any,
    outgoing: Any,
    folder_id: Any,
    subject: str,
) -> MessageDraftResult:
    if saved.id is None or saved.changekey is None:
        raise EwsServiceError("EWS did not return identifiers for the saved draft")
    return MessageDraftResult(
        user=profile.user.username,
        message_id=str(saved.id),
        change_key=str(saved.changekey),
        folder_id=str(folder_id),
        subject=subject,
        to=_sorted_mail_addresses(outgoing.to_recipients),
        cc=_sorted_mail_addresses(outgoing.cc_recipients),
        bcc=_sorted_mail_addresses(outgoing.bcc_recipients),
    )


def _folder_change(
    kind: str, raw_folder: Any, mail_well_known_ids: Collection[str]
) -> FolderChange | None:
    folder_id = str(raw_folder.id)
    if kind == FolderChangeKind.DELETE:
        change_key = getattr(raw_folder, "changekey", None)
        return FolderChange(
            kind=FolderChangeKind.DELETE,
            folder_id=folder_id,
            change_key=None if change_key is None else str(change_key),
        )
    folder_kind = _folder_kind(raw_folder, mail_well_known_ids)
    if folder_kind is None:
        if kind == FolderChangeKind.UPDATE:
            return FolderChange(kind=FolderChangeKind.DELETE, folder_id=folder_id)
        return None
    return FolderChange(
        kind=FolderChangeKind(kind),
        folder_id=folder_id,
        change_key=_change_key(raw_folder),
        folder=_folder_model(raw_folder),
        folder_kind=folder_kind,
    )


def _message_change(kind: str, raw_item: Any) -> MessageChange | None:
    change_kind = MessageChangeKind(kind)
    if change_kind is MessageChangeKind.READ_FLAG_CHANGE:
        item_id, is_read = raw_item
        return MessageChange(
            kind=change_kind,
            message_id=str(item_id.id),
            change_key=_change_key(item_id),
            is_read=bool(is_read),
        )
    if change_kind in {MessageChangeKind.CREATE, MessageChangeKind.UPDATE}:
        if not _is_supported_message_item(raw_item):
            if change_kind is MessageChangeKind.UPDATE:
                return MessageChange(
                    kind=MessageChangeKind.DELETE,
                    message_id=str(raw_item.id),
                )
            return None
    return MessageChange(
        kind=change_kind,
        message_id=str(raw_item.id),
        change_key=(
            _change_key(raw_item)
            if change_kind in {MessageChangeKind.CREATE, MessageChangeKind.UPDATE}
            else None
        ),
    )


def _contact_change(kind: str, raw_item: Any) -> ContactChange | None:
    change_kind = ContactChangeKind(kind)
    if change_kind in {ContactChangeKind.CREATE, ContactChangeKind.UPDATE}:
        if not isinstance(raw_item, EwsContact):
            if change_kind is ContactChangeKind.UPDATE:
                return ContactChange(
                    kind=ContactChangeKind.DELETE,
                    contact_id=str(raw_item.id),
                )
            return None
        return ContactChange(
            kind=change_kind,
            contact_id=str(raw_item.id),
            change_key=_change_key(raw_item),
        )
    return ContactChange(kind=change_kind, contact_id=str(raw_item.id))


def _is_supported_message_item(item: Any) -> bool:
    return isinstance(item, (Message, BaseMeetingItem))


def _change_key(item: Any) -> str:
    value = getattr(item, "changekey", None)
    if value is None:
        value = getattr(item, "change_key", None)
    if value is None:
        raise EwsServiceError("EWS item change is missing a change key")
    return str(value)


def _folder_kind(folder: Any, mail_well_known_ids: Collection[str]) -> FolderKind | None:
    if getattr(folder, "is_hidden", False) is True:
        return None
    if str(folder.id) in mail_well_known_ids or folder.folder_class in MAIL_FOLDER_CLASSES:
        return FolderKind.MAIL
    if folder.folder_class == CONTACT_FOLDER_CLASS:
        return FolderKind.CONTACTS
    return None


def _ensure_folder_extensions() -> None:
    try:
        EwsFolder.get_field_by_fieldname("is_hidden")
    except InvalidField:
        EwsFolder.register("is_hidden", IsHidden)


def _ensure_item_extensions() -> None:
    try:
        Message.get_field_by_fieldname("flag_status")
    except InvalidField:
        Message.register("flag_status", FlagStatusProperty)
    except AttributeError:
        # A test double replaced the EWS item class; there is nothing to register.
        return


def _folder_model(raw_folder: Any) -> Folder:
    return Folder(
        id=str(raw_folder.id),
        parent_id=(
            str(raw_folder.parent_folder_id.id) if raw_folder.parent_folder_id is not None else None
        ),
        name=str(raw_folder.name),
        well_known_name=None,
        total_count=int(raw_folder.total_count or 0),
        unread_count=int(raw_folder.unread_count or 0),
    )


def _distinguished_mail_folders(account: Any) -> dict[str, Any]:
    """Resolve distinguished mail folders, skipping folders this mailbox does not have."""
    resolved: dict[str, Any] = {}
    for name, attribute in WELL_KNOWN_MAIL_FOLDERS.items():
        try:
            resolved[name] = getattr(account, attribute)
        except ErrorFolderNotFound:
            continue
    return resolved


def _distinguished_contact_folders(account: Any) -> dict[str, Any]:
    """Resolve the distinguished contacts folder when the mailbox has one."""
    resolved: dict[str, Any] = {}
    for name, attribute in WELL_KNOWN_CONTACT_FOLDERS.items():
        try:
            resolved[name] = getattr(account, attribute)
        except ErrorFolderNotFound:
            continue
    return resolved


def _unreported_named_folders(
    named_folders: Mapping[str, Any], changes: Sequence[FolderChange], folder_kind: FolderKind
) -> list[FolderChange]:
    """Report distinguished folders this hierarchy synchronization did not mention.

    The synchronization state has already moved past a folder that was filtered out or
    never cached, so EWS will not report it again. Re-reporting it keeps the cached folder
    tree complete without forcing a full re-synchronization.
    """
    reported = {change.folder_id for change in changes}
    return [
        FolderChange(
            kind=FolderChangeKind.CREATE,
            folder_id=str(folder.id),
            folder=_folder_model(folder),
            folder_kind=folder_kind,
        )
        for folder in named_folders.values()
        if str(folder.id) not in reported
    ]


def _folder_from_id(account: Any, folder_id: str) -> Any:
    return EwsFolder(root=account.msg_folder_root, id=folder_id)


def _message_detail(item: Any) -> MessageDetail:
    author = item.author or item.sender
    body = item.body
    conversation_index = getattr(item, "conversation_index", None)
    return MessageDetail(
        id=str(item.id),
        change_key=str(item.changekey),
        parent_folder_id=str(item.parent_folder_id.id),
        subject=item.subject,
        from_address=author.email_address if author is not None else None,
        received_at=item.datetime_received,
        is_read=bool(item.is_read),
        has_attachments=bool(item.has_attachments),
        importance=Importance(str(item.importance).casefold()),
        conversation_id=_conversation_id(getattr(item, "conversation_id", None)),
        conversation_topic=getattr(item, "conversation_topic", None) or None,
        conversation_index=_conversation_index(conversation_index),
        conversation_depth=_conversation_depth(conversation_index),
        is_draft=bool(getattr(item, "is_draft", False)),
        categories=[str(category) for category in getattr(item, "categories", None) or []],
        flag_status=_flag_status(getattr(item, "flag_status", None)),
        sender=_mail_address(item.sender),
        to=_mail_addresses(item.to_recipients),
        cc=_mail_addresses(item.cc_recipients),
        bcc=_mail_addresses(item.bcc_recipients),
        reply_to=_mail_addresses(item.reply_to),
        sent_at=item.datetime_sent,
        created_at=item.datetime_created,
        internet_message_id=item.message_id,
        in_reply_to=item.in_reply_to,
        body=MessageBody(
            content_type="html" if isinstance(body, HTMLBody) else "text",
            content=str(body or ""),
        ),
        internet_headers=[
            InternetHeader(name=str(header.name), value=str(header.value))
            for header in item.headers or []
        ],
        attachments=[_attachment_metadata(attachment) for attachment in item.attachments or []],
        text_body=getattr(item, "text_body", None) or None,
        references=getattr(item, "references", None) or None,
    )


def _contact_detail(item: Any) -> Contact:
    return Contact(
        id=str(item.id),
        change_key=str(item.changekey),
        parent_folder_id=str(item.parent_folder_id.id),
        display_name=_optional_text(item.display_name) or "",
        file_as=_optional_text(item.file_as),
        given_name=_optional_text(item.given_name),
        middle_name=_optional_text(item.middle_name),
        surname=_optional_text(item.surname),
        nickname=_optional_text(item.nickname),
        initials=_optional_text(item.initials),
        generation=_optional_text(item.generation),
        company_name=_optional_text(item.company_name),
        department=_optional_text(item.department),
        job_title=_optional_text(item.job_title),
        office=_optional_text(item.office),
        manager=_optional_text(item.manager),
        profession=_optional_text(item.profession),
        business_homepage=_optional_text(item.business_homepage),
        emails=_contact_emails(item),
        phones=_contact_phones(item),
        addresses=_contact_addresses(item),
        im_addresses=_contact_im_addresses(item),
        categories=[str(category) for category in item.categories or []],
        notes=_optional_text(item.notes),
        birthday=item.birthday,
        has_picture=bool(item.has_picture),
    )


def _contact_emails(item: Any) -> list[ContactEmail]:
    emails: list[ContactEmail] = []
    for entry in item.email_addresses or []:
        address = _optional_text(entry.email)
        if address is None:
            continue
        emails.append(ContactEmail(label=_optional_text(entry.label), address=address))
    return emails


def _contact_phones(item: Any) -> list[ContactPhone]:
    phones: list[ContactPhone] = []
    for entry in item.phone_numbers or []:
        number = _optional_text(entry.phone_number)
        if number is None:
            continue
        phones.append(ContactPhone(label=_optional_text(entry.label), number=number))
    return phones


def _contact_addresses(item: Any) -> list[ContactAddress]:
    return [
        ContactAddress(
            label=_optional_text(entry.label),
            street=_optional_text(entry.street),
            city=_optional_text(entry.city),
            state=_optional_text(entry.state),
            country=_optional_text(entry.country),
            postal_code=_optional_text(entry.zipcode),
        )
        for entry in item.physical_addresses or []
    ]


def _contact_im_addresses(item: Any) -> list[ContactIm]:
    addresses: list[ContactIm] = []
    for entry in item.im_addresses or []:
        address = _optional_text(entry.im_address)
        if address is None:
            continue
        addresses.append(ContactIm(label=_optional_text(entry.label), address=address))
    return addresses


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value) or None


def _directory_contact(item: Any) -> DirectoryContact:
    mailbox, contact = item
    emails = _directory_emails(contact)
    primary = _optional_text(getattr(mailbox, "email_address", None))
    if primary is None or "@" not in primary:
        primary = next((email.address for email in emails if "@" in email.address), None)
    display_name = _optional_text(getattr(mailbox, "name", None)) or _optional_text(
        getattr(contact, "display_name", None)
    )
    return DirectoryContact(
        display_name=display_name or "",
        email_address=primary,
        mailbox_type=_optional_text(getattr(mailbox, "mailbox_type", None)) or "Unknown",
        given_name=_optional_text(getattr(contact, "given_name", None)),
        surname=_optional_text(getattr(contact, "surname", None)),
        company_name=_optional_text(getattr(contact, "company_name", None)),
        department=_optional_text(getattr(contact, "department", None)),
        job_title=_optional_text(getattr(contact, "job_title", None)),
        email_alias=_optional_text(getattr(contact, "email_alias", None)),
        directory_id=_optional_text(getattr(contact, "directory_id", None)),
        emails=emails,
        phones=_contact_phones(contact) if contact is not None else [],
        addresses=_contact_addresses(contact) if contact is not None else [],
    )


def _directory_emails(contact: Any) -> list[ContactEmail]:
    emails: list[ContactEmail] = []
    for entry in getattr(contact, "email_addresses", None) or []:
        address = _optional_text(entry.email)
        if address is None:
            continue
        emails.append(
            ContactEmail(label=_optional_text(entry.label), address=_strip_routing_prefix(address))
        )
    return emails


def _strip_routing_prefix(value: str) -> str:
    prefix, separator, rest = value.partition(":")
    if separator and prefix.casefold() in {"smtp", "ex"}:
        return rest
    return value


def _conversation_id(value: Any) -> str | None:
    return None if value is None else str(value.id)


def _conversation_index(value: Any) -> str | None:
    return None if value is None else bytes(value).hex()


def _conversation_depth(value: Any) -> int | None:
    if value is None:
        return None
    return max(
        0, (len(bytes(value)) - CONVERSATION_ROOT_INDEX_LENGTH) // CONVERSATION_REPLY_INDEX_LENGTH
    )


def _flag_status(value: Any) -> FlagStatus:
    return {1: FlagStatus.COMPLETE, 2: FlagStatus.FLAGGED}.get(value, FlagStatus.NONE)


def _mail_address(mailbox: Any | None) -> MailboxAddress | None:
    if mailbox is None or mailbox.email_address is None:
        return None
    return MailboxAddress(name=mailbox.name, address=mailbox.email_address)


def _mail_addresses(mailboxes: Iterable[Any] | None) -> list[MailboxAddress]:
    return [address for mailbox in mailboxes or [] if (address := _mail_address(mailbox))]


def _attachment_metadata(attachment: Any) -> AttachmentMetadata:
    content_type = attachment.content_type
    content_id = attachment.content_id
    return AttachmentMetadata(
        id=str(attachment.attachment_id.id),
        kind="file" if isinstance(attachment, FileAttachment) else "item",
        name=str(attachment.name or ""),
        content_type=None if content_type is None else str(content_type),
        size=int(str(attachment.size or 0)),
        is_inline=bool(attachment.is_inline),
        content_id=None if content_id is None else str(content_id),
    )
