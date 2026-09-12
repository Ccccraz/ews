from pydantic import BaseModel, ConfigDict, model_validator

from ews.models.server import Server
from ews.models.user import User


class Profile(BaseModel):
    """A non-secret EWS login profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server: Server
    user: User


class Profiles(BaseModel):
    """The complete set of configured EWS login profiles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: tuple[Profile, ...] = ()

    @model_validator(mode="after")
    def identities_are_unique(self) -> Profiles:
        owners: dict[str, str] = {}
        for profile in self.profiles:
            mailbox = str(profile.user.mailbox)
            identities = {mailbox.casefold(), profile.user.username.casefold()}
            for key in identities:
                owner = owners.get(key)
                if owner is not None:
                    raise ValueError(f"Profile identity {key!r} conflicts with mailbox {owner!r}")
                owners[key] = mailbox
        return self
