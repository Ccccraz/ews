import json
import logging
import runpy
import sys
from typing import cast

import pytest
from pydantic import JsonValue
from pytest import CaptureFixture, MonkeyPatch

from ews_cli import __version__
from ews_cli.application import MailboxApplicationService
from ews_cli.cli import app, main
from ews_cli.commands.context import CommandContext
from ews_cli.models import FolderListResult


class StubService:
    """Stand-in service that also emits one third-party diagnostic."""

    def list_folders(self, selected_user: str) -> FolderListResult:
        logging.getLogger("exchangelib").debug("stub exchange call")
        return FolderListResult(user=selected_user, folders=[])


class FailingService:
    """Stand-in service that fails in a way no layer expects."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def list_folders(self, selected_user: str) -> FolderListResult:
        del selected_user
        raise self._error


def test_help(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["--help"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert "Exchange Web Services" in captured.out
    assert captured.err == ""


def test_version(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert __version__ in captured.out
    assert captured.err == ""


def test_module_entrypoint(capsys: CaptureFixture[str], monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ews-cli", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("ews_cli", run_name="__main__")

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert __version__ in captured.out
    assert captured.err == ""


def test_legacy_positional_test_user_is_not_supported(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["test", "DOMAIN\\agent"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "Unused Tokens" in captured.err


def _use_stub_service(monkeypatch: MonkeyPatch, service: object | None = None) -> None:
    def build_context(user: str | None) -> CommandContext:
        chosen = StubService() if service is None else service
        return CommandContext(user=user, service=cast(MailboxApplicationService, chosen))

    monkeypatch.setattr("ews_cli.cli._build_context", build_context)


def test_log_level_selects_the_stderr_diagnostic_level(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _use_stub_service(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "AGENT@EXAMPLE.COM", "--log-level", "DEBUG", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    output = cast(dict[str, JsonValue], json.loads(captured.out))
    assert output["ok"] is True
    (event,) = [cast(dict[str, JsonValue], json.loads(line)) for line in captured.err.splitlines()]
    assert event["event"] == "stub exchange call"
    assert event["logger"] == "exchangelib"


def test_log_level_hides_diagnostics_by_default(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _use_stub_service(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "AGENT@EXAMPLE.COM", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert captured.err == ""


def test_log_level_rejects_unknown_values(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["--log-level", "LOUD", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "LOUD" in captured.err
    assert captured.out == ""


def test_log_format_console_renders_human_readable_diagnostics(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _use_stub_service(monkeypatch)

    with pytest.raises(SystemExit) as exit_info:
        app(
            [
                "--user",
                "AGENT@EXAMPLE.COM",
                "--log-level",
                "DEBUG",
                "--log-format",
                "console",
                "folder",
                "list",
            ]
        )

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert "stub exchange call" in captured.err
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.err)


def test_log_format_rejects_unknown_values(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(["--log-format", "yaml", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "yaml" in captured.err
    assert captured.out == ""


def test_unexpected_command_errors_return_the_internal_error_envelope(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    _use_stub_service(monkeypatch, FailingService(RuntimeError("boom")))

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "AGENT@EXAMPLE.COM", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    output = cast(dict[str, JsonValue], json.loads(captured.out))
    assert output["schema_version"] == 1
    assert output["ok"] is False
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "internal_error"
    assert error["message"] == "Unexpected internal error"
    assert error["retryable"] is False
    assert error["details"] == {"type": "RuntimeError"}
    assert "Traceback" not in captured.out
    assert "Unhandled internal error" in captured.err
    assert "RuntimeError: boom" in captured.err


def test_unexpected_context_errors_return_the_internal_error_envelope(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    def build_context(user: str | None) -> CommandContext:
        del user
        raise KeyError("profile store")

    monkeypatch.setattr("ews_cli.cli._build_context", build_context)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "AGENT@EXAMPLE.COM", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    output = cast(dict[str, JsonValue], json.loads(captured.out))
    assert output["ok"] is False
    error = cast(dict[str, JsonValue], output["error"])
    assert error["code"] == "internal_error"
    assert error["details"] == {"type": "KeyError"}
