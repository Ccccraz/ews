import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import keyring
import pytest
from keyring.errors import InitError, KeyringError, KeyringLocked, NoKeyringError
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture, MonkeyPatch

from ews.cli import app
from ews.config import ProfileStore
from ews.exchange import EwsAuthenticationError, EwsServiceError
from ews.models import ConnectionTestResult, Profile, TlsCheckResult
from ews.system import TlsProbeError

PROFILE_PATH = Path(".config") / "taskseed" / "ews" / "profiles.toml"


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


class SuccessfulTlsProbe:
    def probe(self, profile: Profile) -> TlsCheckResult:
        assert str(profile.server.endpoint) == "https://mail.example.com/EWS/Exchange.asmx"
        return TlsCheckResult(
            host="mail.example.com",
            protocol="TLSv1.3",
            cipher="TLS_AES_256_GCM_SHA384",
            certificate_subject="commonName=mail.example.com",
            certificate_issuer="commonName=Example Issuing CA",
            certificate_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )


class AuthenticationFailingClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise EwsAuthenticationError("EWS rejected the configured credentials or mailbox")


class ServiceFailingClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise EwsServiceError("Unable to access the EWS service")


class FailingTlsProbe:
    def probe(self, profile: Profile) -> TlsCheckResult:
        del profile
        raise TlsProbeError("TLS verification failed for mail.example.com:443")


class UnexpectedTlsProbe:
    def probe(self, profile: Profile) -> TlsCheckResult:
        del profile
        raise AssertionError("doctor must not verify TLS before the earlier checks pass")


class UnexpectedClient:
    def test_access(self, profile: Profile, password: SecretStr) -> ConnectionTestResult:
        del profile, password
        raise AssertionError("doctor must not log in before the earlier checks pass")


def test_doctor_reports_a_complete_diagnosis(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, SuccessfulClient, SuccessfulTlsProbe)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 0
    assert output == {
        "schema_version": 1,
        "ok": True,
        "data": {
            "profile_path": str(tmp_path / PROFILE_PATH),
            "endpoint": "https://mail.example.com/EWS/Exchange.asmx",
            "tls": {
                "host": "mail.example.com",
                "protocol": "TLSv1.3",
                "cipher": "TLS_AES_256_GCM_SHA384",
                "certificate_subject": "commonName=mail.example.com",
                "certificate_issuer": "commonName=Example Issuing CA",
                "certificate_expires_at": "2027-01-01T00:00:00Z",
            },
            "connection": {
                "user": "DOMAIN\\agent",
                "mailbox": "agent@example.com",
                "server_version": "Build=15.2.1.2, API=Exchange2016",
                "inbox_total_count": 12,
                "inbox_unread_count": 3,
            },
        },
    }
    assert "top-secret" not in json.dumps(output)


def test_doctor_requires_user(capsys: CaptureFixture[str]) -> None:
    exit_code, output = _invoke(capsys, "doctor")

    assert exit_code == 2
    assert _error(output)["code"] == "invalid_argument"


def test_doctor_accepts_the_configured_user_case_insensitively(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, SuccessfulClient, SuccessfulTlsProbe)

    exit_code, _ = _invoke(capsys, "--user", "AGENT@EXAMPLE.COM", "doctor")

    assert exit_code == 0


def test_doctor_rejects_an_unconfigured_user_before_reading_the_password(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(keyring, "get_password", _unexpected_password_read)

    exit_code, output = _invoke(capsys, "--user", "other", "doctor")

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"
    assert _check(output) == "configuration"


def test_doctor_handles_missing_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 4
    assert _error(output)["code"] == "profile_not_found"
    assert _check(output) == "configuration"


def test_doctor_handles_invalid_profile(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    profile_path = tmp_path / PROFILE_PATH
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("not valid toml", encoding="utf-8")

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 2
    assert _error(output)["code"] == "configuration_error"
    assert _check(output) == "configuration"


def test_doctor_handles_missing_password_before_verifying_tls(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch, password=None)
    _set_dependencies(monkeypatch, UnexpectedClient, UnexpectedTlsProbe)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"
    assert _check(output) == "keyring"
    assert _reason(output) == "missing"


def test_doctor_handles_keyring_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, UnexpectedClient, UnexpectedTlsProbe)
    monkeypatch.setattr(keyring, "get_password", _failing_password_read)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"
    assert _check(output) == "keyring"
    assert _reason(output) == "error"


def test_doctor_handles_locked_keyring(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, UnexpectedClient, UnexpectedTlsProbe)
    monkeypatch.setattr(keyring, "get_password", _locked_password_read)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"
    assert _check(output) == "keyring"
    assert _reason(output) == "locked"
    assert "unlock" in str(_error(output)["message"])


@pytest.mark.parametrize(
    "error",
    [NoKeyringError("no backend"), InitError("backend failed to initialize")],
)
def test_doctor_handles_unavailable_keyring(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    error: Exception,
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, UnexpectedClient, UnexpectedTlsProbe)

    def unavailable_password_read(service: str, username: str) -> str | None:
        del service, username
        raise error

    monkeypatch.setattr(keyring, "get_password", unavailable_password_read)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 3
    assert _error(output)["code"] == "authentication_error"
    assert _check(output) == "keyring"
    assert _reason(output) == "backend_unavailable"


def test_doctor_handles_tls_failure_before_logging_in(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, UnexpectedClient, FailingTlsProbe)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == 5
    assert _error(output)["code"] == "tls_error"
    assert _error(output)["retryable"] is False
    assert _check(output) == "system_tls"


@pytest.mark.parametrize(
    ("client_type", "expected_code", "expected_exit", "retryable"),
    [
        (AuthenticationFailingClient, "authentication_error", 3, False),
        (ServiceFailingClient, "service_error", 5, True),
    ],
)
def test_doctor_maps_ews_failures(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    client_type: type[AuthenticationFailingClient] | type[ServiceFailingClient],
    expected_code: str,
    expected_exit: int,
    retryable: bool,
) -> None:
    _configure(tmp_path, monkeypatch)
    _set_dependencies(monkeypatch, client_type, SuccessfulTlsProbe)

    exit_code, output = _invoke(capsys, "--user", "agent@example.com", "doctor")

    assert exit_code == expected_exit
    assert _error(output)["code"] == expected_code
    assert _error(output)["retryable"] is retryable
    assert _check(output) == "ews_login"


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


def _set_dependencies(
    monkeypatch: MonkeyPatch, client_type: type[object], probe_type: type[object]
) -> None:
    monkeypatch.setattr("ews.cli.EwsClient", client_type)
    monkeypatch.setattr("ews.cli.SystemTlsProbe", probe_type)


def _invoke(capsys: CaptureFixture[str], *arguments: str) -> tuple[int, dict[str, JsonValue]]:
    with pytest.raises(SystemExit) as exit_info:
        app([*arguments])

    captured = capsys.readouterr()
    assert captured.err == ""
    assert isinstance(exit_info.value.code, int)
    return exit_info.value.code, cast(dict[str, JsonValue], json.loads(captured.out))


def _error(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], output["error"])


def _check(output: dict[str, JsonValue]) -> JsonValue:
    return cast(dict[str, JsonValue], _error(output)["details"])["check"]


def _reason(output: dict[str, JsonValue]) -> JsonValue:
    return cast(dict[str, JsonValue], _error(output)["details"])["reason"]


def _unexpected_password_read(service: str, username: str) -> str | None:
    del service, username
    raise AssertionError("doctor must not read a password for a mismatched user")


def _failing_password_read(service: str, username: str) -> str | None:
    del service, username
    raise KeyringError("Keyring is unavailable")


def _locked_password_read(service: str, username: str) -> str | None:
    del service, username
    raise KeyringLocked("Keyring is locked")
