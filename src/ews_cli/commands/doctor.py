from typing import Annotated

from cyclopts import Parameter
from pydantic import JsonValue

from ews_cli.application import UserNotFoundError
from ews_cli.commands.context import CommandContext, fail
from ews_cli.config import (
    InvalidProfileError,
    PasswordBackendUnavailableError,
    PasswordNotFoundError,
    PasswordStoreError,
    PasswordStoreLockedError,
    ProfileNotFoundError,
)
from ews_cli.contracts import SuccessEnvelope, write_contract
from ews_cli.exchange import EwsAuthenticationError, EwsServiceError
from ews_cli.models import DoctorCheck
from ews_cli.system import TlsProbeError


def doctor(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Verify configuration, system keyring, system TLS and EWS login."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        result = context.service.diagnose(context.user)
    except (InvalidProfileError, OSError) as error:
        return _fail(DoctorCheck.CONFIGURATION, "configuration_error", str(error), 2)
    except (ProfileNotFoundError, UserNotFoundError) as error:
        return _fail(DoctorCheck.CONFIGURATION, "profile_not_found", str(error), 4)
    except PasswordNotFoundError:
        return _fail(
            DoctorCheck.KEYRING,
            "authentication_error",
            f"No password is stored for this profile; run ews-cli --user {context.user} "
            "auth set-password",
            3,
            reason="missing",
        )
    except PasswordBackendUnavailableError as error:
        return _fail(
            DoctorCheck.KEYRING,
            "authentication_error",
            f"{error}; use a desktop session with a system keyring backend "
            "(macOS Keychain, Windows Credential Manager or Linux Secret Service)",
            3,
            reason="backend_unavailable",
        )
    except PasswordStoreLockedError as error:
        return _fail(
            DoctorCheck.KEYRING,
            "authentication_error",
            f"{error}; unlock the system keyring and retry",
            3,
            reason="locked",
        )
    except PasswordStoreError as error:
        return _fail(
            DoctorCheck.KEYRING,
            "authentication_error",
            str(error),
            3,
            reason="error",
        )
    except TlsProbeError as error:
        return _fail(DoctorCheck.SYSTEM_TLS, "tls_error", str(error), 5)
    except EwsAuthenticationError as error:
        return _fail(DoctorCheck.EWS_LOGIN, "authentication_error", str(error), 3)
    except EwsServiceError as error:
        return _fail(DoctorCheck.EWS_LOGIN, "service_error", str(error), 5, retryable=True)

    write_contract(SuccessEnvelope(data=result))
    return 0


def _fail(
    check: DoctorCheck,
    code: str,
    message: str,
    exit_code: int,
    *,
    retryable: bool = False,
    reason: str | None = None,
) -> int:
    """Report the first failed diagnosis stage with its stable error mapping."""
    details: dict[str, JsonValue] = {"check": check}
    if reason is not None:
        details["reason"] = reason
    return fail(code, message, exit_code, retryable=retryable, details=details)
