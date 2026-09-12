import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _restore_diagnostics() -> Iterator[None]:
    """Undo the process-wide logging state installed by ``configure_logging``.

    The standard library root logger is global, so a test that installs the
    stderr handler and lowers the diagnostic level would otherwise keep
    forwarding records into the capture stream of that test.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
