import json
import logging
import runpy
import sys
from typing import cast

import pytest
from pydantic import JsonValue
from pytest import CaptureFixture, MonkeyPatch

from ews import __version__
from ews.application import MailboxApplicationService
from ews.cli import app, main
from ews.commands.context import CommandContext
from ews.models import FolderListResult


class StubService:
    """Stand-in service that also emits one third-party diagnostic."""

    def list_folders(self, selected_user: str) -> FolderListResult:
        logging.getLogger("exchangelib").debug("stub exchange call")
        return FolderListResult(user=selected_user, folders=[])


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
    monkeypatch.setattr(sys, "argv", ["ews", "--version"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("ews", run_name="__main__")

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


def test_log_level_selects_the_stderr_diagnostic_level(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    def build_context(user: str | None) -> CommandContext:
        service = cast(MailboxApplicationService, StubService())
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)

    with pytest.raises(SystemExit) as exit_info:
        app(["--user", "AGENT@EXAMPLE.COM", "--log-level", "DEBUG", "folder", "list"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    output = cast(dict[str, JsonValue], json.loads(captured.out))
    assert output["ok"] is True
    assert "stub exchange call" in captured.err


def test_log_level_hides_diagnostics_by_default(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    def build_context(user: str | None) -> CommandContext:
        service = cast(MailboxApplicationService, StubService())
        return CommandContext(user=user, service=service)

    monkeypatch.setattr("ews.cli._build_context", build_context)

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
