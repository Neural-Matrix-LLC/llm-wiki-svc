"""configure_logging: sets the root logger's level, once, idempotently.

Lives in config.py, not its own module - see the docstring on
configure_logging for why (test_layering.py::test_transport_layer_only_calls_tools).
"""

from __future__ import annotations

import logging

import pytest

from llmwiki import config
from llmwiki.config import configure_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Each test gets a clean root logger, so tests cannot see each other's state."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_configured = config._logging_configured
    yield
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
    config._logging_configured = original_configured


def test_configure_logging_sets_the_root_level() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_rejects_an_unknown_level() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        configure_logging("not-a-level")


def test_configure_logging_is_idempotent() -> None:
    """A second call (a second Settings, serve --reload) must not stack handlers."""
    configure_logging("INFO")
    before = len(logging.getLogger().handlers)
    configure_logging("DEBUG")
    after = len(logging.getLogger().handlers)

    assert after == before
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_is_case_insensitive() -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
