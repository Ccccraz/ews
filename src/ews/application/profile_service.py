from pathlib import Path

from pydantic import SecretStr

from ews.config import PasswordNotFoundError, PasswordStore, ProfileNotFoundError, ProfileStore
from ews.models import Profile


class ProfileApplicationService:
    """Coordinate profile configuration with its Keychain credential."""

    def __init__(self, profile_store: ProfileStore, password_store: PasswordStore) -> None:
        self._profile_store = profile_store
        self._password_store = password_store

    @property
    def path(self) -> Path:
        return self._profile_store.path

    def list_profiles(self) -> tuple[Profile, ...]:
        return self._profile_store.list()

    def get_profile(self, user: str) -> Profile:
        return self._profile_store.load(user)

    def set_profile(self, profile: Profile, password: SecretStr) -> None:
        """Store a profile and credential, removing an obsolete credential key."""
        try:
            previous = self._profile_store.load(str(profile.user.mailbox))
        except ProfileNotFoundError:
            previous = None

        self._profile_store.save(profile)
        self._password_store.set(profile, password)
        if previous is not None:
            old_credential = self._credential_identity(previous)
            if old_credential != self._credential_identity(profile):
                try:
                    self._password_store.delete(previous)
                except PasswordNotFoundError:
                    pass

    def set_password(self, user: str, password: SecretStr) -> Profile:
        profile = self._profile_store.load(user)
        self._password_store.set(profile, password)
        return profile

    def password_status(self, user: str) -> tuple[Profile, bool]:
        profile = self._profile_store.load(user)
        try:
            self._password_store.get(profile)
        except PasswordNotFoundError:
            return profile, False
        return profile, True

    def delete_password(self, user: str) -> Profile:
        profile = self._profile_store.load(user)
        try:
            self._password_store.delete(profile)
        except PasswordNotFoundError:
            pass
        return profile

    def delete_profile(self, user: str) -> Profile:
        profile = self._profile_store.load(user)
        try:
            self._password_store.delete(profile)
        except PasswordNotFoundError:
            pass
        self._profile_store.delete(user)
        return profile

    @staticmethod
    def _credential_identity(profile: Profile) -> tuple[str, str]:
        return str(profile.server.endpoint.host).casefold(), profile.user.username
