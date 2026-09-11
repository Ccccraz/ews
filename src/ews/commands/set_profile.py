import getpass
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic.types import SecretStr

from ews.config import PasswordStore, PasswordStoreError, ProfileStore
from ews.contracts import ContractModel, Error, ErrorEnvelope, SuccessEnvelope, write_contract
from ews.models import Profile


class SetProfileData(ContractModel):
    """Result of storing an EWS profile."""

    profile_path: Path


def set_profile() -> int:
    """Interactively configure the EWS login profile."""
    try:
        profile = _read_profile()
        password = SecretStr(getpass.getpass("Password: ", stream=sys.stderr))
    except (EOFError, ValidationError, ValueError) as error:
        return _fail("configuration_error", str(error))

    profile_store = ProfileStore()
    try:
        profile_store.save(profile)
        PasswordStore().set(profile, password)
    except (OSError, PasswordStoreError, ValueError) as error:
        return _fail("configuration_error", str(error))

    write_contract(SuccessEnvelope(data=SetProfileData(profile_path=profile_store.path)))
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
