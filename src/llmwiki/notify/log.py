"""The default notifier: a WARNING in the service log."""

from __future__ import annotations

import logging

logger = logging.getLogger("llmwiki.alerts")


class LogNotifier:
    def send(self, text: str) -> bool:
        logger.warning("%s", text)
        return True
