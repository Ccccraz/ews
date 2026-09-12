from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, JsonValue, ValidationError

from ews_cli.application import (
    AttachmentNotFoundError,
    ContactNotFoundError,
    DestinationExistsError,
    FolderNotFoundError,
    InvalidDestinationError,
    InvalidFolderError,
    MailboxApplicationService,
    MessageNotFoundError,
    ProfileApplicationService,
    UnsupportedAttachmentError,
    UserNotFoundError,
)
from ews_cli.config import (
    InvalidProfileError,
    PasswordNotFoundError,
    PasswordStoreError,
    ProfileNotFoundError,
)
from ews_cli.contracts import Error, ErrorEnvelope, SuccessEnvelope, write_contract
from ews_cli.exchange import (
    EwsAuthenticationError,
    EwsNotFoundError,
    EwsRejectedError,
    EwsServiceError,
)
from ews_cli.storage import MailboxCacheNotReadyError, MailboxStoreError


@dataclass(frozen=True)
class CommandContext:
    """Explicit dependencies and selected global user for one invocation."""

    user: str | None
    service: MailboxApplicationService
    profiles: ProfileApplicationService | None = None

    @property
    def profile_service(self) -> ProfileApplicationService:
        if self.profiles is None:
            raise RuntimeError("Profile service is not configured")
        return self.profiles


def run_read[ResultT: BaseModel](
    context: CommandContext, operation: Callable[[str], ResultT]
) -> int:
    """Run a read use case and map expected failures to the CLI contract."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        result = operation(context.user)
    except (ValidationError, InvalidFolderError) as error:
        return fail("invalid_argument", str(error), 2)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    except (PasswordNotFoundError, PasswordStoreError, EwsAuthenticationError) as error:
        return fail("authentication_error", str(error), 3)
    except (ProfileNotFoundError, UserNotFoundError) as error:
        return fail("profile_not_found", str(error), 4)
    except MailboxCacheNotReadyError:
        return fail(
            "cache_not_ready",
            f"Mailbox cache is not ready; run ews-cli --user {context.user} sync",
            4,
        )
    except (FolderNotFoundError, MessageNotFoundError, ContactNotFoundError) as error:
        return fail("resource_not_found", str(error), 4)
    except MailboxStoreError as error:
        return fail("cache_error", str(error), 2)
    except EwsServiceError as error:
        return fail("service_error", str(error), 5, retryable=True)

    write_contract(SuccessEnvelope(data=result))
    return 0


def run_write[ResultT: BaseModel](
    context: CommandContext, operation: Callable[[str], ResultT]
) -> int:
    """Run a mutating use case, either remote or a local download, and map failures."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        result = operation(context.user)
    except (
        ValidationError,
        EwsRejectedError,
        UnsupportedAttachmentError,
        InvalidDestinationError,
    ) as error:
        return fail("invalid_argument", str(error), 2)
    except DestinationExistsError as error:
        return fail("destination_exists", str(error), 2)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    except (PasswordNotFoundError, PasswordStoreError, EwsAuthenticationError) as error:
        return fail("authentication_error", str(error), 3)
    except (ProfileNotFoundError, UserNotFoundError) as error:
        return fail("profile_not_found", str(error), 4)
    except MailboxCacheNotReadyError:
        return fail(
            "cache_not_ready",
            f"Mailbox cache is not ready; run ews-cli --user {context.user} sync",
            4,
        )
    except (
        FolderNotFoundError,
        MessageNotFoundError,
        AttachmentNotFoundError,
        EwsNotFoundError,
    ) as error:
        return fail("resource_not_found", str(error), 4)
    except MailboxStoreError as error:
        return fail("cache_error", str(error), 2)
    except EwsServiceError as error:
        return fail("service_error", str(error), 5, retryable=True)

    write_contract(SuccessEnvelope(data=result))
    return 0


def fail(
    code: str,
    message: str,
    exit_code: int,
    *,
    retryable: bool = False,
    details: dict[str, JsonValue] | None = None,
) -> int:
    """Write one error envelope and return its process exit code."""
    write_contract(
        ErrorEnvelope(
            error=Error(
                code=code,
                message=message,
                details={} if details is None else details,
                retryable=retryable,
            )
        )
    )
    return exit_code
