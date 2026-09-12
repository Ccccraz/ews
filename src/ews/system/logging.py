"""Loguru diagnostics that never write to stdout."""

import inspect
import logging
import sys
from enum import StrEnum

from loguru import logger


class LogLevel(StrEnum):
    """Diagnostic level accepted from the command line."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


class InterceptHandler(logging.Handler):
    """Forward standard library logging records, such as exchangelib's, to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        """Re-emit one standard library record through the configured loguru sink."""
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(level: LogLevel = LogLevel.WARNING) -> None:
    """Install the single stderr sink and route third-party logging into loguru.

    Stdout stays reserved for the JSON envelope, so no sink may target it. The
    default level keeps third-party DEBUG records, which can carry transport
    details such as NTLM headers, out of the output. Tracebacks keep ``diagnose``
    and ``backtrace`` off so they never dump local variables, which can hold
    credentials.
    """
    logger.remove()
    logger.add(sys.stderr, level=level.value, backtrace=False, diagnose=False)
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
