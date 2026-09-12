from pathlib import Path

import pytest
from pytest import MonkeyPatch

from ews_cli.config import (
    InvalidProfileError,
    ProfileNotFoundError,
    ProfileStore,
    default_profile_path,
)
from ews_cli.models import Profile


def _profile(mailbox: str, username: str, host: str = "mail.example.com") -> Profile:
    return Profile.model_validate(
        {
            "server": {"endpoint": f"https://{host}/EWS/Exchange.asmx"},
            "user": {"mailbox": mailbox, "username": username},
        }
    )


def test_default_profile_path() -> None:
    assert default_profile_path(Path("/Users/agent")) == Path(
        "/Users/agent/.config/taskseed/ews-cli/profiles.toml"
    )


def test_missing_file_lists_an_empty_collection(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")

    assert store.list() == ()
    with pytest.raises(ProfileNotFoundError, match="Profile not found for user"):
        store.load("agent@example.com")


def test_multiple_profiles_round_trip_in_mailbox_order(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "profiles.toml"
    store = ProfileStore(path)
    second = _profile("zeta@example.com", "DOMAIN\\zeta")
    first = _profile("Alpha@example.com", "DOMAIN\\alpha")

    store.save(second)
    store.save(first)

    assert store.list() == (first, second)
    assert store.load("alpha@EXAMPLE.com") == first
    assert store.load("domain\\ZETA") == second
    assert path.read_text(encoding="utf-8").count("[[profiles]]") == 2
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_save_replaces_the_same_mailbox(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    store.save(_profile("agent@example.com", "DOMAIN\\old"))
    replacement = _profile("AGENT@example.com", "DOMAIN\\new", "new.example.com")

    store.save(replacement)

    assert store.list() == (replacement,)


def test_save_rejects_cross_identity_aliases(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    original = _profile("agent@example.com", "DOMAIN\\agent")
    store.save(original)

    with pytest.raises(InvalidProfileError, match="conflicting identities"):
        store.save(_profile("other@example.com", "domain\\AGENT"))

    assert store.list() == (original,)


def test_save_rejects_a_mailbox_matching_another_username(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.toml")
    original = _profile("agent@example.com", "alias@example.com")
    store.save(original)

    with pytest.raises(InvalidProfileError, match="conflicting identities"):
        store.save(_profile("ALIAS@example.com", "DOMAIN\\other"))

    assert store.list() == (original,)


def test_delete_keeps_a_valid_empty_collection(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    store = ProfileStore(path)
    profile = _profile("agent@example.com", "DOMAIN\\agent")
    store.save(profile)

    assert store.delete("domain\\AGENT") == profile
    assert store.list() == ()
    assert path.read_text(encoding="utf-8") == "profiles = []\n"


def test_failed_atomic_replace_preserves_the_previous_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "profiles.toml"
    store = ProfileStore(path)
    original = _profile("agent@example.com", "DOMAIN\\agent")
    store.save(original)
    original_toml = path.read_text(encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("replace failed")

    monkeypatch.setattr("ews_cli.config.profile_store.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.save(_profile("other@example.com", "DOMAIN\\other"))
    assert path.read_text(encoding="utf-8") == original_toml
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_load_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text("not = [valid", encoding="utf-8")

    with pytest.raises(InvalidProfileError, match="invalid TOML"):
        ProfileStore(path).list()


def test_load_rejects_invalid_profile_data(tmp_path: Path) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text('[[profiles]]\nunknown = "value"\n', encoding="utf-8")

    with pytest.raises(InvalidProfileError, match="invalid data"):
        ProfileStore(path).list()


def test_legacy_single_profile_file_is_ignored(tmp_path: Path) -> None:
    legacy = tmp_path / "profile.toml"
    legacy.write_text('[user]\nmailbox = "agent@example.com"\n', encoding="utf-8")

    assert ProfileStore(tmp_path / "profiles.toml").list() == ()
