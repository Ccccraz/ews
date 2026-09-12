import getpass
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from pydantic import ValidationError
from pydantic.types import SecretStr

from ews.commands.context import CommandContext
from ews.config import InvalidProfileError, PasswordStoreError
from ews.contracts import ContractModel, Error, ErrorEnvelope, SuccessEnvelope, write_contract
from ews.models import Profile


class SetProfileData(ContractModel):
    """Result of storing an EWS profile."""

    profile_path: Path


def set_profile(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Interactively configure the EWS login profile."""
    try:
        profile = _read_profile()
        password = SecretStr(getpass.getpass("Password: ", stream=sys.stderr))
    except (EOFError, ValidationError, ValueError) as error:
        return _fail("configuration_error", str(error))

    try:
        context.profile_service.set_profile(profile, password)
    except (InvalidProfileError, OSError, PasswordStoreError, ValueError) as error:
        return _fail("configuration_error", str(error))

    write_contract(SuccessEnvelope(data=SetProfileData(profile_path=context.profile_service.path)))
    return 0


def _read_profile() -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": _prompt("EWS endpoint: ")},
            "user": {
                "mailbox": _prompt("Mailbox: "),
                "username": _prompt("NTLM username: "),
            },
        }
    )


def _prompt(message: str) -> str:
    print(message, end="", file=sys.stderr, flush=True)
    value = sys.stdin.readline()
    if value == "":
        raise EOFError("Input ended before the profile was complete")
    return value.rstrip("\r\n")


def _fail(code: str, message: str) -> int:
    write_contract(ErrorEnvelope(error=Error(code=code, message=message)))
    return 2
