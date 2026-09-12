from typing import Annotated

from cyclopts import Parameter

from ews.application import UserNotFoundError
from ews.commands.context import CommandContext, fail
from ews.config import (
    InvalidProfileError,
    PasswordNotFoundError,
    PasswordStoreError,
    ProfileNotFoundError,
)
from ews.contracts import SuccessEnvelope, write_contract
from ews.exchange import EwsAuthenticationError, EwsServiceError
from ews.models import DoctorCheck
from ews.system import TlsProbeError


def doctor(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Verify configuration, keychain, system TLS and EWS login."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        result = context.service.diagnose(context.user)
    except (InvalidProfileError, OSError) as error:
        return _fail(DoctorCheck.CONFIGURATION, "configuration_error", str(error), 2)
    except (ProfileNotFoundError, UserNotFoundError) as error:
        return _fail(DoctorCheck.CONFIGURATION, "profile_not_found", str(error), 4)
    except (PasswordNotFoundError, PasswordStoreError) as error:
        return _fail(DoctorCheck.KEYCHAIN, "authentication_error", str(error), 3)
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
) -> int:
    """Report the first failed diagnosis stage with its stable error mapping."""
    return fail(
        code,
        message,
        exit_code,
        retryable=retryable,
        details={"check": check},
    )
