import json
import logging
from typing import cast

import pytest
import structlog
from pydantic import JsonValue, SecretStr
from pytest import CaptureFixture

from ews.system.logging import LogFormat, LogLevel, configure_logging


def _events(stderr: str) -> list[dict[str, JsonValue]]:
    return [cast(dict[str, JsonValue], json.loads(line)) for line in stderr.splitlines()]


def test_diagnostics_are_structured_json_on_stderr(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)

    structlog.get_logger("ews.demo").info("cache rebuilt", messages=15)

    captured = capsys.readouterr()
    assert captured.out == ""
    (event,) = _events(captured.err)
    assert event["event"] == "cache rebuilt"
    assert event["level"] == "info"
    assert event["logger"] == "ews.demo"
    assert event["messages"] == 15
    assert "timestamp" in event


def test_level_filters_lower_severity_records(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.WARNING)
    log = structlog.get_logger("ews.demo")

    log.info("quiet")
    log.warning("loud")

    captured = capsys.readouterr()
    assert [event["event"] for event in _events(captured.err)] == ["loud"]


def test_console_format_is_an_explicit_opt_in(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO, LogFormat.CONSOLE)

    structlog.get_logger("ews.demo").info("cache rebuilt")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cache rebuilt" in captured.err
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.err)


def test_third_party_logging_shares_the_handler(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)

    logging.getLogger("exchangelib.protocol").warning("cannot autodiscover %s", "now")

    captured = capsys.readouterr()
    assert captured.out == ""
    (event,) = _events(captured.err)
    assert event["logger"] == "exchangelib.protocol"
    assert event["level"] == "warning"
    assert event["event"] == "cannot autodiscover now"


def test_third_party_debug_stays_hidden_at_the_default_level(
    capsys: CaptureFixture[str],
) -> None:
    configure_logging()

    logging.getLogger("exchangelib").debug("ntlm challenge header sent")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_unknown_standard_library_levels_still_render(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.DEBUG)
    record = logging.LogRecord("exchangelib", 7, __file__, 1, "custom level record", (), None)

    logging.getLogger("exchangelib").handle(record)

    captured = capsys.readouterr()
    (event,) = _events(captured.err)
    assert event["event"] == "custom level record"
    assert event["level"] == "level 7"


def test_configuring_twice_keeps_a_single_handler(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)
    configure_logging(LogLevel.INFO)

    structlog.get_logger("ews.demo").info("single line")

    captured = capsys.readouterr()
    assert captured.err.count("single line") == 1


def test_secret_values_are_masked(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.DEBUG)

    structlog.get_logger("ews.demo").debug("authenticating with %s", SecretStr("ntlm-secret-value"))

    captured = capsys.readouterr()
    assert "ntlm-secret-value" not in captured.err
    assert "**********" in captured.err


@pytest.mark.parametrize("log_format", [LogFormat.JSON, LogFormat.CONSOLE])
def test_tracebacks_never_dump_local_variables(
    capsys: CaptureFixture[str], log_format: LogFormat
) -> None:
    """Rich pretty-printing would print frame locals, so it stays pinned off."""
    configure_logging(LogLevel.DEBUG, log_format)

    def authenticate() -> None:
        _password = "ntlm-secret-value"
        raise RuntimeError("login failed")

    try:
        authenticate()
    except RuntimeError:
        structlog.get_logger("ews.demo").exception("authentication failed")

    captured = capsys.readouterr()
    assert "ntlm-secret-value" not in captured.err
    assert "RuntimeError: login failed" in captured.err
