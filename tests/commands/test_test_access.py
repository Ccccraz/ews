import json
from pathlib import Path
from typing import cast

import keyring
import pytest
from keyring.errors import KeyringError
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture, MonkeyPatch

from ews_cli.cli import app
from ews_cli.config import ProfileStore
from ews_cli.exchange import EwsAuthenticationError, EwsServiceError
from ews_cli.models import ConnectionTestResult, Profile


class SuccessfulClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        assert password.get_secret_value() == "top-secret"
        return ConnectionTestResult(
            user=profile.user.username,
            mailbox=profile.user.mailbox,
            server_version="Build=15.2.1.2, API=Exchange2016",
            inbox_total_count=12,
            inbox_unread_count=3,
        )


class AuthenticationFailingClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise EwsAuthenticationError("EWS rejected the configured credentials or mailbox")


class ServiceFailingClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise EwsServiceError("Unable to access the EWS service")


def test_test_accepts_username_and_returns_ews_metadata(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_client(monkeypatch, SuccessfulClient)

    exit_code, output = _invoke("DOMAIN\\agent", capsys)

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "user": "DOMAIN\\agent",
            "mailbox": "agent@example.com",
            "server_version": "Build=15.2.1.2, API=Exchange2016",
            "inbox_total_count": 12,
            "inbox_unread_count": 3,
        },
    }


def test_test_accepts_mailbox_case_insensitively(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_client(monkeypatch, SuccessfulClient)

    exit_code, _ = _invoke("AGENT@EXAMPLE.COM", capsys)

    assert exit_code == 0


def test_test_rejects_an_unconfigured_user(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)

    exit_code, output = _invoke("other", capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_test_handles_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output = _invoke("DOMAIN\\agent", capsys)

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"


def test_test_handles_missing_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch, password=None)

    exit_code, output = _invoke("DOMAIN\\agent", capsys)

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"


def test_test_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "get_password", _failing_password_read)

    exit_code, output = _invoke("DOMAIN\\agent", capsys)

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"


@pytest.mark.parametrize(
    ("client_type", "expected_code", "expected_exit", "retryable"),
    [
        (AuthenticationFailingClient, "authentication_error", 3, False),
        (ServiceFailingClient, "service_error", 5, True),
    ],
)
def test_test_maps_ews_failures(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    client_type: type[AuthenticationFailingClient] | type[ServiceFailingClient],
    expected_code: str,
    expected_exit: int,
    retryable: bool,
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_client(monkeypatch, client_type)

    exit_code, output = _invoke("DOMAIN\\agent", capsys)

    assert exit_code == expected_exit
    assert _error(output)["code"] == expected_code
    assert _error(output)["retryable"] is retryable


def _configure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    password: str | None = "top-secret",
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ProfileStore().save(
        Profile.model_validate(
            {
                "server": {"endpoint": "https://mail.example.com/EWS/Exchange.asmx"},
                "user": {"mailbox": "agent@example.com", "username": "DOMAIN\\agent"},
            }
        )
    )

    def get_password(service: str, username: str) -> str | None:
        del service, username
        return password

    monkeypatch.setattr(keyring, "get_password", get_password)


def _invoke(user: str, capsys: CaptureFixture[str]) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app(["--user", user, "test"])

    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])


def _set_client(monkeypatch: MonkeyPatch, client_type: type[object]) -> None:
    monkeypatch.setattr("ews_cli.cli.EwsClient", client_type)


def _failing_password_read(service: str, username: str) -> str | None:
    del service, username
    raise KeyringError("Keyring is unavailable")
