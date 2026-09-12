import logging
from collections.abc import Iterator

import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def _restore_diagnostics() -> Iterator[None]:
    """Undo the process-wide logging state installed by ``configure_logging``.

    Loguru sinks and standard library handlers are global, so a test that lowers
    the diagnostic level would otherwise keep forwarding records into the capture
    stream of that test.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    logger.remove()
    root.handlers = handlers
    root.setLevel(level)
