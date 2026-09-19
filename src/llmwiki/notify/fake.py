"""Recording notifier for tests and offline runs."""

from __future__ import annotations


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True
