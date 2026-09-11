from pydantic import BaseModel, ConfigDict

from ews.models.server import Server
from ews.models.user import User


class Profile(BaseModel):
    """A non-secret EWS login profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server: Server
    user: User
