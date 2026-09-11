import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from ews.models import Profile


class ProfileNotFoundError(Exception):
    """Raised when the profile file does not exist."""


class InvalidProfileError(Exception):
    """Raised when the profile file cannot produce a valid Profile."""


def default_profile_path(home: Path | None = None) -> Path:
    """Return the fixed path for the non-secret EWS profile."""
    root = Path.home() if home is None else home
    return root / ".config" / "taskseed" / "ews" / "profile.toml"


class ProfileStore:
    """Read and write a Profile as TOML."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_profile_path() if path is None else path

    def load(self) -> Profile:
        try:
            with self.path.open("rb") as profile_file:
                data = tomllib.load(profile_file)
        except FileNotFoundError as error:
            raise ProfileNotFoundError(f"Profile not found: {self.path}") from error
        except tomllib.TOMLDecodeError as error:
            raise InvalidProfileError(f"Profile contains invalid TOML: {self.path}") from error

        try:
            return Profile.model_validate(data)
        except ValidationError as error:
            raise InvalidProfileError(f"Profile contains invalid data: {self.path}") from error

    def save(self, profile: Profile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as profile_file:
            tomli_w.dump(profile.model_dump(mode="json"), profile_file)
