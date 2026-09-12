from ews.contracts import ContractModel
from ews.models.mailbox import MailboxAddress


class MessageSendResult(ContractModel):
    """Recipients and subject accepted for one sent message."""

    user: str
    subject: str
    to: list[MailboxAddress]
    cc: list[MailboxAddress]
    bcc: list[MailboxAddress]


class MessageDraftResult(ContractModel):
    """Server-confirmed identifiers and recipients of one saved draft."""

    user: str
    message_id: str
    change_key: str
    folder_id: str
    subject: str
    to: list[MailboxAddress]
    cc: list[MailboxAddress]
    bcc: list[MailboxAddress]


class MessageReadStateResult(ContractModel):
    """Server-confirmed read state of one updated message."""

    user: str
    message_id: str
    change_key: str
    is_read: bool


class MessageMoveResult(ContractModel):
    """Server-confirmed identifiers of one moved message."""

    user: str
    previous_message_id: str
    message_id: str
    change_key: str
    folder_id: str
