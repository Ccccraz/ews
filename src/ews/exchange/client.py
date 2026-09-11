# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from collections.abc import Callable, Iterable
from typing import Any

from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials
from exchangelib.attachments import FileAttachment
from exchangelib.errors import (
    ErrorAccessDenied,
    ErrorInvalidIdMalformed,
    ErrorItemNotFound,
    ErrorNonExistentMailbox,
    EWSError,
    UnauthorizedError,
)
from exchangelib.extended_properties import ExtendedProperty
from exchangelib.fields import InvalidField
from exchangelib.folders import Folder as EwsFolder
from exchangelib.items import Message
from exchangelib.properties import HTMLBody
from pydantic import SecretStr

from ews.application import FolderNotFoundError, InvalidFolderError, MessageNotFoundError
from ews.models import (
    AttachmentMetadata,
    ConnectionTestResult,
    Folder,
    Importance,
    InternetHeader,
    MessageBody,
    MessageDetail,
    MessageListQuery,
    MessageSummary,
    Profile,
    ReadState,
)
from ews.models.mailbox import MailboxAddress

LIST_FIELDS = (
    "parent_folder_id",
    "subject",
    "author",
    "datetime_received",
    "is_read",
    "has_attachments",
    "importance",
)
DETAIL_FIELDS = LIST_FIELDS + (
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
)
MAIL_FOLDER_CLASSES = {"IPF.Note", "IPF.Note.OutlookHomepage", "IPF.StickyNote"}
MAIL_FOLDER_WELL_KNOWN_NAMES = {"conversationhistory"}
MAIL_NAVIGATION_EXCLUDED_WELL_KNOWN_NAMES = {
    "conflicts",
    "localfailures",
    "serverfailures",
    "syncissues",
}


class IsHidden(ExtendedProperty):
    """PidTagAttributeHidden (0x10F4) folder property."""

    property_tag = 0x10F4
    property_type = "Boolean"


class EwsAuthenticationError(Exception):
    """Raised when EWS rejects the configured identity or credentials."""


class EwsServiceError(Exception):
    """Raised when EWS cannot complete a mailbox operation."""


class EwsClient:
    """Perform synchronous operations against one EWS profile."""

    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        """Authenticate and fetch Inbox metadata through EWS."""

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

    def list_folders(self, profile: Profile, password: SecretStr) -> list[Folder]:
        """Return all folders that support messages."""

        def operation(account: Any) -> list[Folder]:
            folders = list(account.msg_folder_root.walk())
            mail_folders = [folder for folder in folders if _is_mail_folder(folder)]
            mail_folder_ids = {str(folder.id) for folder in mail_folders}
            return [
                Folder(
                    id=str(folder.id),
                    parent_id=(
                        str(folder.parent_folder_id.id)
                        if folder.parent_folder_id is not None
                        and str(folder.parent_folder_id.id) in mail_folder_ids
                        else None
                    ),
                    name=str(folder.name),
                    well_known_name=_well_known_name(folder),
                    total_count=int(folder.total_count or 0),
                    unread_count=int(folder.unread_count or 0),
                )
                for folder in mail_folders
            ]

        return self._run(profile, password, operation)

    def list_messages(
        self, profile: Profile, password: SecretStr, query: MessageListQuery
    ) -> tuple[list[MessageSummary], bool]:
        """Return one reverse-chronological page using EWS-side filters."""

        def operation(account: Any) -> tuple[list[MessageSummary], bool]:
            folder = _resolve_folder(account, query.folder)
            queryset = folder.all().only(*LIST_FIELDS)
            if query.read_state is ReadState.READ:
                queryset = queryset.filter(is_read=True)
            elif query.read_state is ReadState.UNREAD:
                queryset = queryset.filter(is_read=False)
            if query.sender is not None:
                queryset = queryset.filter(sender=str(query.sender))
            if query.subject_contains is not None:
                queryset = queryset.filter(subject__icontains=query.subject_contains)
            if query.body_contains is not None:
                queryset = queryset.filter(body__icontains=query.body_contains)
            if query.received_from is not None:
                queryset = queryset.filter(datetime_received__gte=query.received_from)
            if query.received_before is not None:
                queryset = queryset.filter(datetime_received__lt=query.received_before)
            stop = query.offset + query.limit + 1
            items = list(queryset.order_by("-datetime_received")[query.offset : stop])
            return (
                [_message_summary(item) for item in items[: query.limit]],
                len(items) > query.limit,
            )

        return self._run(profile, password, operation)

    def get_message(self, profile: Profile, password: SecretStr, message_id: str) -> MessageDetail:
        """Fetch one message using a separate GetItem request."""

        def operation(account: Any) -> MessageDetail:
            fetched = iter(account.fetch(ids=[(message_id, None)], only_fields=DETAIL_FIELDS))
            try:
                item = next(fetched)
            except StopIteration as error:
                raise MessageNotFoundError(f"Message not found: {message_id}") from error
            if isinstance(item, (ErrorItemNotFound, ErrorInvalidIdMalformed)):
                raise MessageNotFoundError(f"Message not found: {message_id}") from item
            if isinstance(item, Exception):
                raise item
            if not isinstance(item, Message):
                raise MessageNotFoundError(f"Message not found: {message_id}")
            return _message_detail(item)

        try:
            return self._run(profile, password, operation)
        except (ErrorItemNotFound, ErrorInvalidIdMalformed) as error:
            raise MessageNotFoundError(f"Message not found: {message_id}") from error

    @staticmethod
    def _run[ResultT](
        profile: Profile,
        password: SecretStr,
        operation: Callable[[Any], ResultT],
    ) -> ResultT:
        try:
            _ensure_folder_extensions()
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
        except (UnauthorizedError, ErrorAccessDenied, ErrorNonExistentMailbox) as error:
            raise EwsAuthenticationError(
                "EWS rejected the configured credentials or mailbox"
            ) from error
        except EWSError as error:
            raise EwsServiceError("Unable to access the EWS service") from error


def _is_mail_folder(folder: Any) -> bool:
    if getattr(folder, "is_hidden", False) is True:
        return False
    well_known_name = _well_known_name(folder)
    if well_known_name in MAIL_NAVIGATION_EXCLUDED_WELL_KNOWN_NAMES:
        return False
    return (
        folder.folder_class in MAIL_FOLDER_CLASSES
        or well_known_name in MAIL_FOLDER_WELL_KNOWN_NAMES
    )


def _ensure_folder_extensions() -> None:
    try:
        EwsFolder.get_field_by_fieldname("is_hidden")
    except InvalidField:
        EwsFolder.register("is_hidden", IsHidden)


def _well_known_name(folder: Any) -> str | None:
    return getattr(type(folder), "DISTINGUISHED_FOLDER_ID", None)


def _resolve_folder(account: Any, folder_id: str) -> Any:
    if folder_id.casefold() == "inbox":
        return account.inbox

    for folder in account.msg_folder_root.walk():
        if str(folder.id) != folder_id:
            continue
        if not _is_mail_folder(folder):
            raise InvalidFolderError(f"Folder cannot contain messages: {folder_id}")
        return folder
    raise FolderNotFoundError(f"Folder not found: {folder_id}")


def _message_summary(item: Any) -> MessageSummary:
    author = item.author or item.sender
    return MessageSummary(
        id=str(item.id),
        change_key=str(item.changekey),
        parent_folder_id=str(item.parent_folder_id.id),
        subject=item.subject,
        from_address=author.email_address if author is not None else None,
        received_at=item.datetime_received,
        is_read=bool(item.is_read),
        has_attachments=bool(item.has_attachments),
        importance=Importance(str(item.importance).casefold()),
    )


def _message_detail(item: Any) -> MessageDetail:
    summary = _message_summary(item)
    body = item.body
    return MessageDetail(
        id=summary.id,
        change_key=summary.change_key,
        parent_folder_id=summary.parent_folder_id,
        subject=summary.subject,
        from_address=summary.from_address,
        received_at=summary.received_at,
        is_read=summary.is_read,
        has_attachments=summary.has_attachments,
        importance=summary.importance,
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
    )


def _mail_address(mailbox: Any | None) -> MailboxAddress | None:
    if mailbox is None or mailbox.email_address is None:
        return None
    return MailboxAddress(name=mailbox.name, address=mailbox.email_address)


def _mail_addresses(mailboxes: Iterable[Any] | None) -> list[MailboxAddress]:
    return [address for mailbox in mailboxes or [] if (address := _mail_address(mailbox))]


def _attachment_metadata(attachment: Any) -> AttachmentMetadata:
    raw_attachment = attachment
    attachment_id = raw_attachment.attachment_id
    return AttachmentMetadata(
        id=str(attachment_id.id),
        kind="file" if isinstance(attachment, FileAttachment) else "item",
        name=str(raw_attachment.name or ""),
        content_type=raw_attachment.content_type,
        size=int(raw_attachment.size or 0),
        is_inline=bool(raw_attachment.is_inline),
        content_id=raw_attachment.content_id,
    )
