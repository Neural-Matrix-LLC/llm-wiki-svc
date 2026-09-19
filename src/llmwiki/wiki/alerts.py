"""Cost alerts and the monthly hard cap (Phase 2, design v1.4 §4.10.3, plan §21.2 C4/C5).

Evaluated after spend is recorded - by ``tools`` after an ingest, an answer
or a judge call - from running totals that are refreshed from the ledger at
most once a minute (one month-prefix read). Three thresholds, ``0`` = off:

* ``COST_ALERT_DAILY_USD`` - a WARNING + notifier message once per day;
* ``COST_ALERT_MONTHLY_USD`` - once per month;
* ``COST_HARD_CAP_MONTHLY_USD`` - once per month, and :meth:`capped` turns
  true: the ingest worker parks post-capture processing (``paused``) until
  the month rolls over or the cap is raised. Capture, search and answer keep
  working; query spend keeps counting.

What already fired lives in ``wiki/_meta/cost/alerts.json`` so a restart
does not re-send.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime

from llmwiki.config import Settings
from llmwiki.models.plan import AlertState
from llmwiki.notify.base import Notifier
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import ALERTS_KEY
from llmwiki.wiki.ledger import CostLedger

logger = logging.getLogger(__name__)

REFRESH_S = 60.0


def _month_of(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def _day_of(moment: datetime) -> str:
    return moment.date().isoformat()


def _next_month_start(moment: datetime) -> str:
    year, month = moment.year, moment.month
    month += 1
    if month > 12:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}-01"


class CostAlerts:
    """Running spend totals, threshold checks, deduped notifications, the cap."""

    def __init__(
        self,
        store: ObjectStore,
        ledger: CostLedger,
        notifier: Notifier,
        settings: Settings,
        *,
        queued: object = None,
    ) -> None:
        self.store = store
        self.ledger = ledger
        self.notifier = notifier
        self.settings = settings
        # A callable returning how many sources are waiting (for the cap
        # message); supplied by the factory from the worker, optional.
        self._queued = queued
        self._lock = threading.Lock()
        self._fetched_at = 0.0
        self._month = ""
        self._day = ""
        self.month_usd = 0.0
        self.day_usd = 0.0

    # -- totals -------------------------------------------------------------------

    def refresh(self, now: datetime | None = None) -> None:
        """Re-read this month's ledger (one bounded prefix read)."""
        now = now or datetime.now(UTC)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        records = self.ledger.read(since=start, until=now)
        month_usd = sum(record.cost_usd for record in records)
        today = _day_of(now)
        day_usd = sum(record.cost_usd for record in records
                      if _day_of(record.at.astimezone(UTC) if record.at.tzinfo else
                                 record.at.replace(tzinfo=UTC)) == today)
        with self._lock:
            self._fetched_at = time.monotonic()
            self._month, self._day = _month_of(now), today
            self.month_usd, self.day_usd = month_usd, day_usd

    def _ensure_fresh(self, now: datetime) -> bool:
        """Refresh from the ledger when the cache is stale or the day/month rolled.

        Returns True when a refresh happened - the caller then must not add the
        spend it just ledgered on top, since the read already includes it.
        """
        stale = (time.monotonic() - self._fetched_at) > REFRESH_S
        rolled = self._month != _month_of(now) or self._day != _day_of(now)
        if stale or rolled:
            self.refresh(now)
            return True
        return False

    def totals(self, now: datetime | None = None) -> tuple[float, float]:
        """``(month_usd, day_usd)`` as of now, refreshing if stale."""
        now = now or datetime.now(UTC)
        self._ensure_fresh(now)
        return self.month_usd, self.day_usd

    # -- the cap ----------------------------------------------------------------------

    def capped(self, now: datetime | None = None) -> bool:
        cap = self.settings.cost_hard_cap_monthly_usd
        if cap <= 0:
            return False
        month_usd, _ = self.totals(now)
        return month_usd >= cap

    # -- evaluation ---------------------------------------------------------------------

    def evaluate(self, added_usd: float = 0.0, now: datetime | None = None) -> list[str]:
        """Account ``added_usd`` (just ledgered), check thresholds, notify once per period.

        Returns the messages that fired. Never raises: an unreadable ledger or
        a failed notifier is logged and the caller's work is unaffected.
        """
        now = now or datetime.now(UTC)
        try:
            refreshed = self._ensure_fresh(now)
            with self._lock:
                if not refreshed:
                    self.month_usd += added_usd
                    self.day_usd += added_usd
                month_usd, day_usd = self.month_usd, self.day_usd
            return self._check(month_usd, day_usd, now)
        except Exception as exc:  # pragma: no cover - defensive; alerts must never fail work
            logger.warning("cost alerts: evaluation skipped (%s: %s)", type(exc).__name__, exc)
            return []

    def _check(self, month_usd: float, day_usd: float, now: datetime) -> list[str]:
        cfg = self.settings
        daily, monthly, cap = (cfg.cost_alert_daily_usd, cfg.cost_alert_monthly_usd,
                               cfg.cost_hard_cap_monthly_usd)
        if daily <= 0 and monthly <= 0 and cap <= 0:
            return []
        state = self.state()
        month, day = _month_of(now), _day_of(now)
        fired: list[str] = []
        changed = False

        if daily > 0 and day_usd >= daily and state.daily_warned != day:
            fired.append(f"llmwiki: today's LLM spend ${day_usd:.2f} passed "
                         f"COST_ALERT_DAILY_USD=${daily:.2f} ({day})")
            state.daily_warned = day
            changed = True
        if monthly > 0 and month_usd >= monthly and state.monthly_warned != month:
            fired.append(f"llmwiki: this month's LLM spend ${month_usd:.2f} passed "
                         f"COST_ALERT_MONTHLY_USD=${monthly:.2f} ({month})")
            state.monthly_warned = month
            changed = True
        if cap > 0 and month_usd >= cap and state.capped != month:
            waiting = self._waiting()
            fired.append(f"llmwiki: monthly hard cap COST_HARD_CAP_MONTHLY_USD=${cap:.2f} reached "
                         f"(${month_usd:.2f}). Processing of new sources is paused until "
                         f"{_next_month_start(now)} or the cap is raised; {waiting} source(s) "
                         "waiting. Capture, search and answers keep working.")
            state.capped = month
            changed = True

        for text in fired:
            logger.warning("%s", text)
            try:
                self.notifier.send(text)
            except Exception as exc:  # pragma: no cover - notifier contract says it won't
                logger.warning("cost alerts: notifier failed (%s)", exc)
        if changed:
            self.save_state(state)
        return fired

    def _waiting(self) -> int:
        try:
            return int(self._queued()) if callable(self._queued) else 0
        except Exception:  # pragma: no cover - a status probe must not break the alert
            return 0

    # -- dedup state --------------------------------------------------------------------

    def state(self) -> AlertState:
        try:
            return AlertState.model_validate(json.loads(self.store.get(ALERTS_KEY)))
        except ObjectNotFound:
            return AlertState()
        except Exception:
            logger.warning("cost alerts: %s unreadable; starting fresh", ALERTS_KEY)
            return AlertState()

    def save_state(self, state: AlertState) -> None:
        self.store.put(ALERTS_KEY, state.model_dump_json(indent=2).encode("utf-8"),
                       "application/json")

    def status(self, now: datetime | None = None) -> dict:
        """What the dashboard and ``/healthz`` show."""
        now = now or datetime.now(UTC)
        month_usd, day_usd = self.totals(now)
        cfg = self.settings
        return {
            "month": _month_of(now),
            "month_usd": round(month_usd, 6),
            "day_usd": round(day_usd, 6),
            "daily_alert_usd": cfg.cost_alert_daily_usd,
            "monthly_alert_usd": cfg.cost_alert_monthly_usd,
            "hard_cap_usd": cfg.cost_hard_cap_monthly_usd,
            "capped": self.capped(now),
            "alerts": self.state().model_dump(),
        }
