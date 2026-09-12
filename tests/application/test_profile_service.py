from pathlib import Path

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import SecretStr
from pytest import MonkeyPatch

from ews.application import ProfileApplicationService
from ews.config import PasswordStore, PasswordStoreError, ProfileStore
from ews.models import Profile


def _profile(mailbox: str, username: str, host: str = "mail.example.com") -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": f"https://{host}/EWS/Exchange.asmx"},
            "user": {"mailbox": mailbox, "username": username},
        }
    )


def _service(path: Path) -> ProfileApplicationService:
    return ProfileApplicationService(ProfileStore(path), PasswordStore())


def test_set_adds_profiles_and_replaces_one_mailbox(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    credentials: dict[tuple[str, str], str] = {}
    deleted: list[tuple[str, str]] = []

    def set_password(service: str, username: str, password: str) -> None:
        credentials[(service, username)] = password

    def delete_password(service: str, username: str) -> None:
        deleted.append((service, username))

    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    service = _service(tmp_path / "profiles.toml")
    first = _profile("alpha@example.com", "DOMAIN\\alpha")
    second = _profile("zeta@example.com", "DOMAIN\\zeta")
    replacement = _profile("ALPHA@example.com", "DOMAIN\\new-alpha", "new.example.com")

    service.set_profile(first, SecretStr("first-secret"))
    service.set_profile(second, SecretStr("second-secret"))
    service.set_profile(replacement, SecretStr("replacement-secret"))

    assert service.list_profiles() == (replacement, second)
    assert credentials[("taskseed.ews:new.example.com", "DOMAIN\\new-alpha")] == (
        "replacement-secret"
    )
    assert deleted == [("taskseed.ews:mail.example.com", "DOMAIN\\alpha")]


def test_delete_profile_tolerates_a_missing_password(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "profiles.toml"
    store = ProfileStore(path)
    profile = _profile("agent@example.com", "DOMAIN\\agent")
    store.save(profile)

    def missing_password(service: str, username: str) -> None:
        del service, username
        raise PasswordDeleteError("missing")

    monkeypatch.setattr(keyring, "delete_password", missing_password)

    assert _service(path).delete_profile("AGENT@example.com") == profile
    assert store.list() == ()


def test_delete_profile_preserves_profile_when_keyring_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "profiles.toml"
    store = ProfileStore(path)
    profile = _profile("agent@example.com", "DOMAIN\\agent")
    store.save(profile)

    def failing_delete(service: str, username: str) -> None:
        del service, username
        raise KeyringError("unavailable")

    monkeypatch.setattr(keyring, "delete_password", failing_delete)

    with pytest.raises(PasswordStoreError, match="Unable to delete"):
        _service(path).delete_profile("agent@example.com")
    assert store.list() == (profile,)
