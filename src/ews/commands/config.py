from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from ews.commands.context import CommandContext, fail
from ews.config import InvalidProfileError, PasswordStoreError, ProfileNotFoundError, ProfileStore
from ews.contracts import ContractModel, SuccessEnvelope, write_contract
from ews.models import Profile


class ConfigPathData(ContractModel):
    """Location of the non-secret EWS profiles."""

    profile_path: Path


class ConfigListData(ContractModel):
    """All configured non-secret EWS profiles."""

    profiles: tuple[Profile, ...]


class DeleteConfigData(ContractModel):
    """Identity of a deleted EWS profile."""

    username: str


def list_config(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """List every configured non-secret EWS profile."""
    try:
        profiles = context.profile_service.list_profiles()
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    write_contract(SuccessEnvelope(data=ConfigListData(profiles=profiles)))
    return 0


def show_config(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Show one configured non-secret EWS profile."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        profile = context.profile_service.get_profile(context.user)
    except ProfileNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    write_contract(SuccessEnvelope(data=profile))
    return 0


def delete_config(*, context: Annotated[CommandContext, Parameter(parse=False, show=False)]) -> int:
    """Delete one profile and its Keychain password, preserving mailbox cache."""
    if context.user is None:
        return fail("invalid_argument", "--user is required", 2)
    try:
        profile = context.profile_service.delete_profile(context.user)
    except ProfileNotFoundError as error:
        return fail("profile_not_found", str(error), 4)
    except PasswordStoreError as error:
        return fail("authentication_error", str(error), 3)
    except (InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)
    write_contract(SuccessEnvelope(data=DeleteConfigData(username=profile.user.username)))
    return 0


def show_config_path() -> int:
    """Show the path used for the non-secret EWS profiles."""
    write_contract(SuccessEnvelope(data=ConfigPathData(profile_path=ProfileStore().path)))
    return 0
