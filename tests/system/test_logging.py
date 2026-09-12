import logging
import sys

from loguru import logger
from pydantic import SecretStr
from pytest import CaptureFixture

from ews.system.logging import InterceptHandler, LogLevel, configure_logging


def test_diagnostics_go_to_stderr_only(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)

    logger.info("cache rebuilt")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cache rebuilt" in captured.err


def test_level_filters_lower_severity_records(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.WARNING)

    logger.info("quiet")
    logger.warning("loud")

    captured = capsys.readouterr()
    assert "quiet" not in captured.err
    assert "loud" in captured.err


def test_configuring_twice_keeps_a_single_sink(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)
    configure_logging(LogLevel.INFO)

    logger.info("single line")

    captured = capsys.readouterr()
    assert captured.err.count("single line") == 1


def test_third_party_logging_is_intercepted(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.INFO)

    logging.getLogger("exchangelib.protocol").warning("cannot autodiscover")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot autodiscover" in captured.err
    assert "test_third_party_logging_is_intercepted" in captured.err


def test_third_party_debug_stays_hidden_at_the_default_level(
    capsys: CaptureFixture[str],
) -> None:
    configure_logging()

    logging.getLogger("exchangelib").debug("ntlm challenge header sent")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_unknown_levels_fall_back_to_the_numeric_value(capsys: CaptureFixture[str]) -> None:
    logger.remove()
    logger.add(sys.stderr, level=0)
    record = logging.LogRecord("exchangelib", 7, __file__, 1, "custom level record", (), None)

    InterceptHandler().emit(record)

    captured = capsys.readouterr()
    assert "Level 7" in captured.err
    assert "custom level record" in captured.err


def test_exception_text_is_logged(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.DEBUG)
    record = logging.LogRecord(
        "exchangelib",
        logging.ERROR,
        __file__,
        1,
        "session failed",
        (),
        (RuntimeError, RuntimeError("connection reset"), None),
    )

    InterceptHandler().emit(record)

    captured = capsys.readouterr()
    assert "session failed" in captured.err
    assert "RuntimeError: connection reset" in captured.err


def test_secret_values_are_masked(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.DEBUG)

    logger.debug("authenticating with {}", SecretStr("ntlm-secret-value"))

    captured = capsys.readouterr()
    assert "ntlm-secret-value" not in captured.err
    assert "**********" in captured.err


def test_tracebacks_never_dump_local_variables(capsys: CaptureFixture[str]) -> None:
    configure_logging(LogLevel.DEBUG)

    def authenticate() -> None:
        _password = "ntlm-secret-value"
        raise RuntimeError("login failed")

    try:
        authenticate()
    except RuntimeError:
        logger.exception("authentication failed")

    captured = capsys.readouterr()
    assert "ntlm-secret-value" not in captured.err
    assert "RuntimeError: login failed" in captured.err
