from pathlib import Path

import pytest

from ews.config import (
    InvalidProfileError,
    ProfileNotFoundError,
    ProfileStore,
    default_profile_path,
)
from ews.models import Profile

PROFILE_DATA = {
    "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
    "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
}


def test_default_profile_path() -> None:
    assert default_profile_path(Path("/Users/agent")) == Path(
        "/Users/agent/.config/taskseed/ews/profile.toml"
    )


def test_profile_round_trip_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "profile.toml"
    store = ProfileStore(path)
    profile = Profile.model_validate(PROFILE_DATA)

    store.save(profile)

    assert store.load() == profile
    assert path.read_text(encoding="utf-8") == (
        '[server]\nendpoint = "https://mail.example.com/EWS/Exchange.asmx"\n\n'
        '[user]\nmailbox = "agent@example.com"\nusername = "DOMAIN\\\\agent"\n'
    )


def test_load_rejects_missing_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profile.toml")

    with pytest.raises(ProfileNotFoundError, match="Profile not found"):
        store.load()


def test_load_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    path.write_text("not = [valid", encoding="utf-8")

    with pytest.raises(InvalidProfileError, match="invalid TOML"):
        ProfileStore(path).load()


def test_load_rejects_invalid_profile_data(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    path.write_text('[server]\nendpoint = "http://mail.example.com"\n', encoding="utf-8")

    with pytest.raises(InvalidProfileError, match="invalid data"):
        ProfileStore(path).load()
