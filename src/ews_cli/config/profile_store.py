import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from ews_cli.models import Profile, Profiles


class ProfileNotFoundError(Exception):
    """Raised when no profile matches the selected user."""


class InvalidProfileError(Exception):
    """Raised when the profile file cannot produce a valid Profile."""


def default_profile_path(home: Path | None = None) -> Path:
    """Return the fixed path for the non-secret EWS profiles."""
    root = Path.home() if home is None else home
    return root / ".config" / "taskseed" / "ews-cli" / "profiles.toml"


class ProfileStore:
    """Read and atomically update the configured EWS profiles."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_profile_path() if path is None else path

    def list(self) -> tuple[Profile, ...]:
        """Return all profiles in stable mailbox order."""
        collection = self._read()
        return tuple(
            sorted(collection.profiles, key=lambda item: str(item.user.mailbox).casefold())
        )

    def load(self, user: str) -> Profile:
        """Select one profile by mailbox or NTLM username, case-insensitively."""
        expected = user.casefold()
        for profile in self.list():
            if expected in {
                str(profile.user.mailbox).casefold(),
                profile.user.username.casefold(),
            }:
                return profile
        raise ProfileNotFoundError(f"Profile not found for user: {user}")

    def _read(self) -> Profiles:
        try:
            with self.path.open("rb") as profile_file:
                data = tomllib.load(profile_file)
        except FileNotFoundError:
            return Profiles()
        except tomllib.TOMLDecodeError as error:
            raise InvalidProfileError(f"Profiles contain invalid TOML: {self.path}") from error

        try:
            return Profiles.model_validate(data)
        except ValidationError as error:
            raise InvalidProfileError(f"Profiles contain invalid data: {self.path}") from error

    def save(self, profile: Profile) -> None:
        """Add or replace a profile using mailbox as its stable identity."""
        mailbox = str(profile.user.mailbox).casefold()
        profiles = [item for item in self.list() if str(item.user.mailbox).casefold() != mailbox]
        profiles.append(profile)
        try:
            collection = Profiles(profiles=tuple(profiles))
        except ValidationError as error:
            raise InvalidProfileError("Profiles contain conflicting identities") from error
        self._write(collection)

    def delete(self, user: str) -> Profile:
        """Delete and return the uniquely selected profile."""
        selected = self.load(user)
        mailbox = str(selected.user.mailbox).casefold()
        remaining = tuple(
            item for item in self.list() if str(item.user.mailbox).casefold() != mailbox
        )
        self._write(Profiles(profiles=remaining))
        return selected

    def _write(self, profiles: Profiles) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
            ) as profile_file:
                temporary_path = Path(profile_file.name)
                tomli_w.dump(profiles.model_dump(mode="json"), profile_file)
                profile_file.flush()
                os.fsync(profile_file.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
