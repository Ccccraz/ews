import getpass
import sys

from pydantic import SecretStr

from ews.commands.context import fail
from ews.config import (
    InvalidProfileError,
    PasswordNotFoundError,
    PasswordStore,
    PasswordStoreError,
    ProfileNotFoundError,
    ProfileStore,
)
from ews.contracts import ContractModel, SuccessEnvelope, write_contract


class SetPasswordData(ContractModel):
    """Result of updating a profile password."""

    username: str


class AuthStatusData(ContractModel):
    """Password availability for the configured profile."""

    username: str
    password_set: bool


def set_password() -> int:
    """Interactively update the configured profile password."""
    try:
        profile = ProfileStore().load()
    except (ProfileNotFoundError, InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)

    try:
        password = SecretStr(getpass.getpass("Password: ", stream=sys.stderr))
        PasswordStore().set(profile, password)
    except (EOFError, ValueError) as error:
        return fail("configuration_error", str(error), 2)
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)

    write_contract(SuccessEnvelope(data=SetPasswordData(username=profile.user.username)))
    return 0


def password_status() -> int:
    """Report whether the configured profile has a password."""
    try:
        profile = ProfileStore().load()
    except (ProfileNotFoundError, InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)

    try:
        PasswordStore().get(profile)
        password_set = True
    except PasswordNotFoundError:
        password_set = False
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)

    write_contract(
        SuccessEnvelope(
            data=AuthStatusData(username=profile.user.username, password_set=password_set)
        )
    )
    return 0
