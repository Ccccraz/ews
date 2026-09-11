from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ews.application import (
    FolderNotFoundError,
    InvalidFolderError,
    MailboxApplicationService,
    MessageNotFoundError,
    UserNotFoundError,
)
from ews.config import (
    InvalidProfileError,
    PasswordNotFoundError,
    PasswordStoreError,
    ProfileNotFoundError,
)
from ews.contracts import Error, ErrorEnvelope, SuccessEnvelope, write_contract
from ews.exchange import EwsAuthenticationError, EwsServiceError


@dataclass(frozen=True)
class CommandContext:
    """Explicit dependencies and selected global user for one invocation."""

    user: str | None
    service: MailboxApplicationService


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
    except (ProfileNotFoundError, InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    except (PasswordNotFoundError, PasswordStoreError, EwsAuthenticationError) as error:
        return fail("authentication_error", str(error), 3)
    except UserNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except (FolderNotFoundError, MessageNotFoundError) as error:
        return fail("resource_not_found", str(error), 4)
    except EwsServiceError as error:
        return fail("service_error", str(error), 5, retryable=True)

    write_contract(SuccessEnvelope(data=result))
    return 0


def fail(code: str, message: str, exit_code: int, *, retryable: bool = False) -> int:
    """Write one error envelope and return its process exit code."""
    write_contract(ErrorEnvelope(error=Error(code=code, message=message, retryable=retryable)))
    return exit_code
