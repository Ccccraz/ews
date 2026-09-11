from typing import cast

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import SecretStr

from ews.models import Profile


class PasswordStoreError(Exception):
    """Raised when the system keyring cannot complete an operation."""


class PasswordNotFoundError(PasswordStoreError):
    """Raised when a profile has no password in the system keyring."""


class PasswordStore:
    """Store profile passwords in the system keyring."""

    def get(self, profile: Profile) -> SecretStr:
        service, username = self._credential_key(profile)
        try:
            password = keyring.get_password(service, username)
        except KeyringError as error:
            raise PasswordStoreError("Unable to read password from the system keyring") from error
        if not password:
            raise PasswordNotFoundError(f"Password not found for {username}")
        return SecretStr(password)

    def set(self, profile: Profile, password: SecretStr) -> None:
        secret = password.get_secret_value()
        if not secret:
            raise ValueError("Password must not be empty")

        service, username = self._credential_key(profile)
        try:
            keyring.set_password(service, username, secret)
        except KeyringError as error:
            raise PasswordStoreError("Unable to write password to the system keyring") from error

    def delete(self, profile: Profile) -> None:
        service, username = self._credential_key(profile)
        try:
            keyring.delete_password(service, username)
        except PasswordDeleteError as error:
            raise PasswordNotFoundError(f"Password not found for {username}") from error
        except KeyringError as error:
            raise PasswordStoreError("Unable to delete password from the system keyring") from error

    @staticmethod
    def _credential_key(profile: Profile) -> tuple[str, str]:
        host = cast(str, profile.server.endpoint.host)
        return f"taskseed.ews:{host.casefold()}", profile.user.username
