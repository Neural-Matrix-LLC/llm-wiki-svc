"""Telegram notifier: the capture bot's token, a chat id, one ``sendMessage``.

Reuses ``TELEGRAM_BOT_TOKEN`` (the capture channel's bot) and needs
``ALERT_TELEGRAM_CHAT_ID`` - the chat the operator wants alerts in. Delivery
is best effort: a failure is logged and reported as ``False``; it never
propagates into the ingest or query path that triggered the alert.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT_S = 10.0


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.chat_id = chat_id
        self._client = httpx.Client(base_url=f"{API_BASE}/bot{token}", timeout=TIMEOUT_S)

    def send(self, text: str) -> bool:
        try:
            response = self._client.post("/sendMessage", json={"chat_id": self.chat_id,
                                                               "text": text})
            response.raise_for_status()
            return bool(response.json().get("ok", True))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("telegram alert not delivered: %s", exc)
            return False
