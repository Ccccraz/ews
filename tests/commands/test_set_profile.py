import getpass
import json
import sys
from io import StringIO
from pathlib import Path
from typing import TextIO

import keyring
import pytest
from keyring.errors import KeyringError
from pytest import CaptureFixture, MonkeyPatch

from ews.cli import app
from ews.config import ProfileStore


class FakeKeyring:
    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.passwords[(service, username)]


def test_set_stores_profile_and_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    backend = FakeKeyring()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("https://mail.example.com/EWS/Exchange.asmx\nagent@example.com\nDOMAIN\\agent\n"),
    )
    monkeypatch.setattr(getpass, "getpass", _password_prompt)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)

    with pytest.raises(SystemExit) as exit_info:
        app(["set"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert captured.err == "EWS endpoint: Mailbox: NTLM username: Password: "
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "ok": True,
        "data": {"profile_path": str(tmp_path / ".config" / "taskseed" / "ews" / "profiles.toml")},
    }
    profile = ProfileStore().load("agent@example.com")
    assert profile.user.mailbox == "agent@example.com"
    assert backend.passwords == {("taskseed.ews:mail.example.com", "DOMAIN\\agent"): "top-secret"}


def test_set_rejects_invalid_profile_before_password_prompt(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("http://mail.example.com/EWS/Exchange.asmx\nagent@example.com\nDOMAIN\\agent\n"),
    )
    monkeypatch.setattr(getpass, "getpass", _unexpected_password_prompt)

    with pytest.raises(SystemExit) as exit_info:
        app(["set"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert json.loads(captured.out)["error"]["code"] == "configuration_error"
    assert not (tmp_path / ".config" / "taskseed" / "ews" / "profiles.toml").exists()


def test_set_handles_incomplete_input(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO(""))

    with pytest.raises(SystemExit) as exit_info:
        app(["set"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert json.loads(captured.out)["error"]["code"] == "configuration_error"


def test_set_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("https://mail.example.com/EWS/Exchange.asmx\nagent@example.com\nDOMAIN\\agent\n"),
    )
    monkeypatch.setattr(getpass, "getpass", _password_prompt)
    monkeypatch.setattr(keyring, "set_password", _failing_set_password)

    with pytest.raises(SystemExit) as exit_info:
        app(["set"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert json.loads(captured.out)["error"]["code"] == "configuration_error"


def _password_prompt(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
    print(prompt, end="", file=sys.stderr if stream is None else stream, flush=True)
    return "top-secret"


def _unexpected_password_prompt(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
    raise AssertionError("Password must not be requested for an invalid profile")


def _failing_set_password(service: str, username: str, password: str) -> None:
    raise KeyringError("Keyring is unavailable")
