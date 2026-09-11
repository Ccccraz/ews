import runpy
import sys

import pytest
from pytest import CaptureFixture, MonkeyPatch

from ews import __version__
from ews.cli import app, main


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
