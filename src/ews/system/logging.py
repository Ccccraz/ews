"""structlog diagnostics that never write to stdout."""

import logging
import os
import sys
from enum import StrEnum

import structlog
from structlog.typing import Processor


class LogLevel(StrEnum):
    """Diagnostic level accepted from the command line."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


class LogFormat(StrEnum):
    """Diagnostic rendering accepted from the command line."""

    JSON = "JSON"
    CONSOLE = "CONSOLE"


_SHARED_PROCESSORS: list[Processor] = [
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.dev.set_exc_info,
]


def configure_logging(
    level: LogLevel = LogLevel.WARNING,
    log_format: LogFormat = LogFormat.JSON,
) -> None:
    """Render diagnostics to one stderr handler shared with standard library logging.

    Structured JSON is the default and ``LogFormat.CONSOLE`` is the explicit
    human-readable mode. Stdout stays reserved for the JSON envelope, so the
    handler must target stderr, and the level and format apply to third-party
    standard library records as well as to our own.

    Exception rendering never includes local variables: the JSON path formats
    the traceback itself, and the console path pins ``plain_traceback`` because
    the Rich formatter that structlog selects automatically for the installed
    Rich dependency dumps the locals of every frame, which can hold credentials.
    """
    processors = [*_SHARED_PROCESSORS]
    if log_format is LogFormat.JSON:
        processors.append(structlog.processors.format_exc_info)
    processors.append(structlog.stdlib.ProcessorFormatter.wrap_for_formatter)
    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED_PROCESSORS,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _renderer(log_format),
            ],
        )
    )
    logging.basicConfig(handlers=[handler], level=level.value, force=True)


def _renderer(log_format: LogFormat) -> Processor:
    if log_format is LogFormat.JSON:
        return structlog.processors.JSONRenderer()
    # Escape codes only help on a terminal, and NO_COLOR is the standard opt-out.
    colors = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
    return structlog.dev.ConsoleRenderer(
        colors=colors,
        exception_formatter=structlog.dev.plain_traceback,
    )
