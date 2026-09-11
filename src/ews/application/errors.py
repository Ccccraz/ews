class FolderNotFoundError(Exception):
    """Raised when a selected folder does not exist."""


class InvalidFolderError(Exception):
    """Raised when a selected folder cannot contain mail."""


class MessageNotFoundError(Exception):
    """Raised when a selected message does not exist."""
