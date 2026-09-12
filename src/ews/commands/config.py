from pathlib import Path

from ews.commands.context import fail
from ews.config import InvalidProfileError, ProfileNotFoundError, ProfileStore
from ews.contracts import ContractModel, SuccessEnvelope, write_contract


class ConfigPathData(ContractModel):
    """Location of the non-secret EWS profile."""

    profile_path: Path


def show_config() -> int:
    """Show the configured non-secret EWS profile."""
    try:
        profile = ProfileStore().load()
    except (ProfileNotFoundError, InvalidProfileError, OSError) as error:
        return fail("configuration_error", str(error), 2)

    write_contract(SuccessEnvelope(data=profile))
    return 0


def show_config_path() -> int:
    """Show the path used for the non-secret EWS profile."""
    write_contract(SuccessEnvelope(data=ConfigPathData(profile_path=ProfileStore().path)))
    return 0
