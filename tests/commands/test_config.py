import json
from pathlib import Path
from typing import cast

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import JsonValue
from pytest import CaptureFixture, MonkeyPatch

from ews.cli import app
from ews.config import ProfileStore
from ews.models import Profile


def test_config_show_returns_non_secret_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ProfileStore().save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\\\agent"},
            }
        )
    )
    monkeypatch.setattr(keyring, "get_password", _unexpected_password_read)

    exit_code, output = _invoke(capsys)

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\\\agent"},
        },
    }
    assert "password" not in json.dumps(output).casefold()


def test_config_show_handles_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output = _invoke(capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_config_show_requires_user(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["config", "show"])

    output = cast(dict[str, JsonValue], json.loads(capsys.readouterr().out))
    assert exit_info.value.code == 2
    assert _error(output)["code"] == "invalid_argument"


def test_config_show_handles_invalid_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    profile_path = tmp_path / ".config" / "taskseed" / "ews" / "profiles.toml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("not valid toml", encoding="utf-8")

    exit_code, output = _invoke(capsys)

    assert exit_code == 2
    assert _error(output)["code"] == "configuration_error"


def test_config_path_returns_path_when_profile_does_not_exist(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output = _invoke(capsys, command="path")

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {"profile_path": str(tmp_path / ".config" / "taskseed" / "ews" / "profiles.toml")},
    }


def test_config_list_returns_sorted_profiles_without_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ProfileStore().save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "zeta@example.com", "username": "DOMAIN\\zeta"},
            }
        )
    )
    ProfileStore().save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "alpha@example.com", "username": "DOMAIN\\alpha"},
            }
        )
    )

    exit_code, output = _invoke(capsys, command="list")

    assert exit_code == 0
    data = cast(dict[str, JsonValue], output["data"])
    profiles = cast(list[dict[str, JsonValue]], data["profiles"])
    assert [cast(dict[str, JsonValue], profile["user"])["mailbox"] for profile in profiles] == [
        "alpha@example.com",
        "zeta@example.com",
    ]


def test_config_list_returns_empty_profiles_when_file_is_absent(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output = _invoke(capsys, command="list")

    assert exit_code == 0
    assert output["data"] == {"profiles": []}


def test_config_delete_keeps_profile_when_keyring_fails(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    profile = Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
    ProfileStore().save(profile)

    def failing_delete(service: str, username: str) -> None:
        del service, username
        raise KeyringError("unavailable")

    monkeypatch.setattr(keyring, "delete_password", failing_delete)

    exit_code, output = _invoke(capsys, command="delete")

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"
    assert ProfileStore().list() == (profile,)


def test_config_delete_removes_profile_when_password_is_absent(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    profile = Profile.model_validate(
        {
            "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
            "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
        }
    )
    ProfileStore().save(profile)

    def missing_delete(service: str, username: str) -> None:
        del service, username
        raise PasswordDeleteError("missing")

    monkeypatch.setattr(keyring, "delete_password", missing_delete)

    exit_code, output = _invoke(capsys, command="delete")

    assert exit_code == 0
    assert output["data"] == {"username": "DOMAIN\\agent"}
    assert ProfileStore().list() == ()


def _invoke(
    capsys: CaptureFixture[str], *, command: str = "show"
) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        arguments = ["config", command]
        if command in {"show", "delete"}:
            arguments = ["--user", "agent@example.com", *arguments]
        app(arguments)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])


def _unexpected_password_read(service: str, username: str) -> str | None:
    del service, username
    raise AssertionError("config show must not read a password")
