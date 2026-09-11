"""Local mailbox persistence."""

from ews.storage.sqlite import (
    MailboxStoreError,
    SqliteMailboxStore,
    UnsupportedCacheSchemaVersionError,
    default_cache_path,
)

__all__ = [
    "MailboxStoreError",
    "SqliteMailboxStore",
    "UnsupportedCacheSchemaVersionError",
    "default_cache_path",
]
