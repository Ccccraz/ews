import getpass
import json
import sys
from pathlib import Path
from typing import TextIO, cast

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import JsonValue
from pytest import CaptureFixture, MonkeyPatch

from ews.cli import app
from ews.config import ProfileStore
from ews.models import Profile


def test_auth_set_password_updates_keychain(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    stored: dict[tuple[str, str], str] = {}

    def store_password(service: str, username: str, password: str) -> None:
        stored[(service, username)] = password

    monkeypatch.setattr(getpass, "getpass", _password_prompt)
    monkeypatch.setattr(keyring, "set_password", store_password)

    exit_code, output, stderr = _invoke(capsys)

    assert exit_code == 0
    assert stderr == "Password: "
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {"username": "DOMAIN\\agent"},
    }
    assert stored == {("taskseed.ews:mail.example.com", "DOMAIN\\agent"): "new-secret"}
    assert "new-secret" not in json.dumps(output)


def test_auth_set_password_loads_profile_before_prompting(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(getpass, "getpass", _unexpected_password_prompt)

    exit_code, output, stderr = _invoke(capsys)

    assert exit_code == 4
    assert stderr == ""
    assert _error(output)["code"] == "profile_not_found"


@pytest.mark.parametrize("command", ["set-password", "status", "delete-password"])
def test_auth_commands_require_user(command: str, capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["auth", command])

    output = cast(dict[str, JsonValue], json.loads(capsys.readouterr().out))
    assert exit_info.value.code == 2
    assert _error(output)["code"] == "invalid_argument"


def test_auth_set_password_handles_invalid_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    profile_path = tmp_path / ".config" / "taskseed" / "ews" / "profiles.toml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("not valid toml", encoding="utf-8")
    monkeypatch.setattr(getpass, "getpass", _unexpected_password_prompt)

    exit_code, output, stderr = _invoke(capsys)

    assert exit_code == 2
    assert stderr == ""
    assert _error(output)["code"] == "configuration_error"


def test_auth_set_password_rejects_empty_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(getpass, "getpass", _empty_password_prompt)

    exit_code, output, stderr = _invoke(capsys)

    assert exit_code == 2
    assert stderr == "Password: "
    assert _error(output)["code"] == "configuration_error"


def test_auth_set_password_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(getpass, "getpass", _password_prompt)
    monkeypatch.setattr(keyring, "set_password", _failing_password_write)

    exit_code, output, stderr = _invoke(capsys)

    assert exit_code == 3
    assert stderr == "Password: "
    assert _error(output)["code"] == "authentication_error"


def test_auth_status_reports_available_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "get_password", _read_existing_password)

    exit_code, output, stderr = _invoke(capsys, command="status")

    assert exit_code == 0
    assert stderr == ""
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {"username": "DOMAIN\\agent", "password_set": True},
    }
    assert "existing-secret" not in json.dumps(output)


def test_auth_status_reports_missing_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "get_password", _read_missing_password)

    exit_code, output, stderr = _invoke(capsys, command="status")

    assert exit_code == 0
    assert stderr == ""
    assert output["data"] == {"username": "DOMAIN\\agent", "password_set": False}


def test_auth_status_handles_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output, stderr = _invoke(capsys, command="status")

    assert exit_code == 4
    assert stderr == ""
    assert _error(output)["code"] == "profile_not_found"


def test_auth_status_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "get_password", _failing_password_read)

    exit_code, output, stderr = _invoke(capsys, command="status")

    assert exit_code == 3
    assert stderr == ""
    assert _error(output)["code"] == "authentication_error"


def test_auth_delete_password_removes_keychain_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    deleted: list[tuple[str, str]] = []

    def delete_stored_password(service: str, username: str) -> None:
        deleted.append((service, username))

    monkeypatch.setattr(keyring, "delete_password", delete_stored_password)

    exit_code, output, stderr = _invoke(capsys, command="delete-password")

    assert exit_code == 0
    assert stderr == ""
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {"username": "DOMAIN\\agent"},
    }
    assert deleted == [("taskseed.ews:mail.example.com", "DOMAIN\\agent")]


def test_auth_delete_password_handles_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output, stderr = _invoke(capsys, command="delete-password")

    assert exit_code == 4
    assert stderr == ""
    assert _error(output)["code"] == "profile_not_found"


def test_auth_delete_password_handles_missing_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "delete_password", _delete_missing_password)

    exit_code, output, stderr = _invoke(capsys, command="delete-password")

    assert exit_code == 0
    assert stderr == ""
    assert output["data"] == {"username": "DOMAIN\\agent"}


def test_auth_delete_password_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "delete_password", _failing_password_delete)

    exit_code, output, stderr = _invoke(capsys, command="delete-password")

    assert exit_code == 3
    assert stderr == ""
    assert _error(output)["code"] == "authentication_error"


def _configure_profile(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ProfileStore().save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
            }
        )
    )


def _invoke(
    capsys: CaptureFixture[str], *, command: str = "set-password"
) -> tuple[int, dict[str, JsonValue], str]:
    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "agent@example.com", "auth", command])

    captured = capsys.readouterr()
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out)), captured.err


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])


def _password_prompt(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
    print(prompt, end="", file=sys.stderr if stream is None else stream, flush=True)
    return "new-secret"


def _empty_password_prompt(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
    print(prompt, end="", file=sys.stderr if stream is None else stream, flush=True)
    return ""


def _unexpected_password_prompt(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
    raise AssertionError("Password must not be requested before loading a valid profile")


def _failing_password_write(service: str, username: str, password: str) -> None:
    del service, username, password
    raise KeyringError("Keyring is unavailable")


def _read_existing_password(service: str, username: str) -> str:
    del service, username
    return "existing-secret"


def _read_missing_password(service: str, username: str) -> None:
    del service, username


def _failing_password_read(service: str, username: str) -> str | None:
    del service, username
    raise KeyringError("Keyring is unavailable")


def _delete_missing_password(service: str, username: str) -> None:
    del service, username
    raise PasswordDeleteError("Password does not exist")


def _failing_password_delete(service: str, username: str) -> None:
    del service, username
    raise KeyringError("Keyring is unavailable")
