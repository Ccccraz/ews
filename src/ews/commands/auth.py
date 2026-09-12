import getpass
import sys
from typing import Annotated

from cyclopts import Parameter
from pydantic import SecretStr

from ews.commands.context import CommandContext, fail
from ews.config import InvalidProfileError, PasswordStoreError, ProfileNotFoundError
from ews.contracts import ContractModel, SuccessEnvelope, write_contract


class SetPasswordData(ContractModel):
    username: str


class AuthStatusData(ContractModel):
    username: str
    password_set: bool


class DeletePasswordData(ContractModel):
    username: str


def set_password(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Interactively update one profile password."""
    user = _required_user(context)
    if user is None:
        return 2
    try:
        profile = context.profile_service.get_profile(user)
    except ProfileNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    try:
        password = SecretStr(getpass.getpass("Password: ", stream=sys.stderr))
        context.profile_service.set_password(user, password)
    except (EOFError, ValueError) as error:
        return fail("configuration_error", str(error), 2)
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)
    write_contract(SuccessEnvelope(data=SetPasswordData(username=profile.user.username)))
    return 0


def password_status(
    *, context: Annotated[CommandContext, Parameter(parse=False, show=False)]
) -> int:
    """Report whether one profile has a password."""
    user = _required_user(context)
    if user is None:
        return 2
    try:
        profile, password_set = context.profile_service.password_status(user)
    except ProfileNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)
    write_contract(
        SuccessEnvelope(
            data=AuthStatusData(username=profile.user.username, password_set=password_set)
        )
    )
    return 0


def delete_password(
    *, context: Annotated[CommandContext, Parameter(parse=False, show=False)]
) -> int:
    """Delete one profile password; an already absent password is successful."""
    user = _required_user(context)
    if user is None:
        return 2
    try:
        profile = context.profile_service.delete_password(user)
    except ProfileNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)
    write_contract(SuccessEnvelope(data=DeletePasswordData(username=profile.user.username)))
    return 0


def _required_user(context: CommandContext) -> str | None:
    if context.user is None:
        fail("invalid_argument", "--user is required", 2)
        return None
    return context.user
