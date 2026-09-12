from collections.abc import Callable

import keyring
import pytest
from keyring.errors import (
    InitError,
    KeyringError,
    KeyringLocked,
    NoKeyringError,
    PasswordDeleteError,
)
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.config import (
    PasswordBackendUnavailableError,
    PasswordNotFoundError,
    PasswordStore,
    PasswordStoreError,
    PasswordStoreLockedError,
)
from ews.models import Profile

PROFILE_DATA = {
    "server": {"endpoint": "https://MAIL.example.com/EWS/Exchange.asmx"},
    "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
}


class FakeKeyring:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.passwords[(service, username)]
        except KeyError as error:
            raise PasswordDeleteError("Password does not exist") from error


class FailingKeyring:
    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringError("Keyring is unavailable")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringError("Keyring is unavailable")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringError("Keyring is unavailable")


class ErrorKeyring:
    """A backend whose every operation raises one fixed error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def get_password(self, service: str, username: str) -> str | None:
        raise self._error

    def set_password(self, service: str, username: str, password: str) -> None:
        raise self._error

    def delete_password(self, service: str, username: str) -> None:
        raise self._error


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(PROFILE_DATA)


@pytest.fixture
def fake_keyring(monkeypatch: MonkeyPatch) -> FakeKeyring:
    backend = FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_password_round_trip_uses_profile_identity(
    profile: Profile, fake_keyring: FakeKeyring
) -> None:
    store = PasswordStore()

    store.set(profile, SecretStr("top-secret"))

    assert fake_keyring.passwords == {
        ("taskseed.ews:mail.example.com", "DOMAIN\\agent"): "top-secret"
    }
    password = store.get(profile)
    assert password.get_secret_value() == "top-secret"
    assert str(password) == "**********"

    store.delete(profile)
    assert fake_keyring.passwords == {}


def test_get_rejects_missing_password(profile: Profile, fake_keyring: FakeKeyring) -> None:
    with pytest.raises(PasswordNotFoundError, match="Password not found"):
        PasswordStore().get(profile)


def test_set_rejects_empty_password(profile: Profile, fake_keyring: FakeKeyring) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        PasswordStore().set(profile, SecretStr(""))


def test_delete_rejects_missing_password(profile: Profile, fake_keyring: FakeKeyring) -> None:
    with pytest.raises(PasswordNotFoundError, match="Password not found"):
        PasswordStore().delete(profile)


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_backend_errors_are_wrapped(
    profile: Profile, operation: str, monkeypatch: MonkeyPatch
) -> None:
    backend = FailingKeyring()
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    store = PasswordStore()
    operations: dict[str, Callable[[], object]] = {
        "get": lambda: store.get(profile),
        "set": lambda: store.set(profile, SecretStr("top-secret")),
        "delete": lambda: store.delete(profile),
    }

    with pytest.raises(PasswordStoreError, match="system keyring"):
        operations[operation]()


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
@pytest.mark.parametrize(
    "error", [NoKeyringError("no backend"), InitError("backend did not initialize")]
)
def test_unavailable_backend_errors_are_classified(
    profile: Profile, operation: str, error: Exception, monkeypatch: MonkeyPatch
) -> None:
    _install_backend(monkeypatch, ErrorKeyring(error))
    store = PasswordStore()

    with pytest.raises(PasswordBackendUnavailableError, match="No usable system keyring backend"):
        _operations(store, profile)[operation]()


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_locked_keyring_errors_are_classified(
    profile: Profile, operation: str, monkeypatch: MonkeyPatch
) -> None:
    _install_backend(monkeypatch, ErrorKeyring(KeyringLocked("Keyring is locked")))
    store = PasswordStore()

    with pytest.raises(PasswordStoreLockedError, match="locked"):
        _operations(store, profile)[operation]()


def _install_backend(monkeypatch: MonkeyPatch, backend: ErrorKeyring) -> None:
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)


def _operations(store: PasswordStore, profile: Profile) -> dict[str, Callable[[], object]]:
    return {
        "get": lambda: store.get(profile),
        "set": lambda: store.set(profile, SecretStr("top-secret")),
        "delete": lambda: store.delete(profile),
    }
