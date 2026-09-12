class FolderNotFoundError(Exception):
    """Raised when a selected folder does not exist."""


class InvalidFolderError(Exception):
    """Raised when a selected folder cannot contain mail."""


class MessageNotFoundError(Exception):
    """Raised when a selected message does not exist."""


class InvalidSyncStateError(Exception):
    """Raised when EWS rejects a stored synchronization state."""


class AttachmentNotFoundError(Exception):
    """Raised when a selected attachment does not exist."""


class UnsupportedAttachmentError(Exception):
    """Raised when a selected attachment cannot be read or written as a file."""


class DestinationExistsError(Exception):
    """Raised when an attachment destination already exists."""


class InvalidDestinationError(Exception):
    """Raised when an attachment destination cannot be created."""
