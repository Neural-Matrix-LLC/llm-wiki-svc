"""The measured cost ledger, partitioned by day and by writing process.

Phase 2 (design v1.4 §4.10.3, plan §21.2 C1). Object stores have no append,
so every write is a read-modify-write of one key; the pre-Phase-2 ledger kept
*all* lines in ``wiki/_meta/cost.jsonl``, which grew without bound and raced
whenever two processes (the API and a cron ``lint``, say) appended at once.
Here a line goes to ``wiki/_meta/cost/{YYYY-MM}/{DD}-{writer}.jsonl``:

* one key per day per *writer* (``api``, ``cli``, ``backfill`` ...), so two
  processes never touch the same object and one process only needs a local
  lock;
* a month's spend is one bounded prefix listing (≤ 31 × writers keys), so a
  summary never reads more than the months in its window.

The legacy single file is still *read* (read-through) so nothing breaks
before ``llmwiki usage --migrate`` moves its lines; it is never written again.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from datetime import UTC, date, datetime

from llmwiki.models.plan import CostKind, CostRecord, CostSummary
from llmwiki.models.source import GENERAL_DOMAIN, utcnow
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    COST_KEY,
    COST_PREFIX,
    check_cost_writer,
    cost_key,
    cost_key_day,
    cost_month_prefix,
)

logger = logging.getLogger(__name__)

DEFAULT_WRITER = "api"

# One lock per process: append() is a read-modify-write of one object, and two
# threads of the same process (the ingest worker's lanes) share the writer name
# and therefore the key. Different processes have different writer names.
_APPEND_LOCK = threading.Lock()


def _parse_lines(raw: str) -> list[CostRecord]:
    """Parse ndjson lines. Malformed lines are skipped, never fatal."""
    records: list[CostRecord] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            records.append(CostRecord(**json.loads(line)))
        except Exception:
            continue
    return records


def _months_between(since: date, until: date) -> list[str]:
    months: list[str] = []
    year, month = since.year, since.month
    while (year, month) <= (until.year, until.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


class CostLedger:
    """Append and read measured :class:`CostRecord` lines for one writer."""

    def __init__(self, store: ObjectStore, writer: str = DEFAULT_WRITER) -> None:
        self.store = store
        self.writer = check_cost_writer(writer)

    # -- write ----------------------------------------------------------------

    def append(
        self,
        records: Iterable[CostRecord],
        *,
        kind: CostKind | None = None,
        domain: str | None = None,
        source_id: str | None = None,
    ) -> int:
        """Append ``records`` to today's key for this writer. Returns the count written.

        ``kind``/``domain``/``source_id`` overwrite the records' own values when
        given - the caller knows what the call paid for better than the adapter
        that measured it did.
        """
        updates: dict = {}
        if kind is not None:
            updates["kind"] = kind
        if domain is not None:
            updates["domain"] = domain
        if source_id is not None:
            updates["source_id"] = source_id
        lines = [record.model_copy(update=updates).model_dump_json() for record in records]
        if not lines:
            return 0

        key = cost_key(utcnow().date(), self.writer)
        with _APPEND_LOCK:
            try:
                existing = self.store.get(key).decode("utf-8")
            except ObjectNotFound:
                existing = ""
            payload = existing + ("" if existing.endswith("\n") or not existing else "\n")
            payload += "\n".join(lines) + "\n"
            self.store.put(key, payload.encode("utf-8"), "application/x-ndjson")
        logger.debug("ledger: appended %d line(s) to %s", len(lines), key)
        return len(lines)

    # -- read -----------------------------------------------------------------

    def read(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        *,
        domain: str | None = None,
        include_legacy: bool = True,
    ) -> list[CostRecord]:
        """Every record in the window, oldest first.

        A window bounds the read to the month prefixes it overlaps (the scale
        guarantee); no window at all reads every month - fine for a small
        deployment, and what the deprecated ``llmwiki cost`` alias does.
        """
        if since is not None:
            since = _as_utc(since)
        if until is not None:
            until = _as_utc(until)

        if since is None and until is None:
            keys = [key for key in self.store.list(COST_PREFIX) if cost_key_day(key) is not None]
        else:
            first = (since or datetime(2000, 1, 1, tzinfo=UTC)).date()
            last = (until or utcnow()).date()
            keys = []
            for month in _months_between(first, last):
                for key in self.store.list(cost_month_prefix(month)):
                    day = cost_key_day(key)
                    if day is not None and first <= day <= last:
                        keys.append(key)

        records: list[CostRecord] = []
        for key in sorted(keys):
            try:
                records.extend(_parse_lines(self.store.get(key).decode("utf-8")))
            except ObjectNotFound:  # pragma: no cover - listed a moment ago
                continue

        if include_legacy:
            records.extend(self._read_legacy())

        records = [
            record for record in records
            if (since is None or _as_utc(record.at) >= since)
            and (until is None or _as_utc(record.at) <= until)
            and (domain is None or record.domain == domain)
        ]
        records.sort(key=lambda record: record.at)
        return records

    def _read_legacy(self) -> list[CostRecord]:
        try:
            raw = self.store.get(COST_KEY).decode("utf-8")
        except ObjectNotFound:
            return []
        return _parse_lines(raw)

    # -- one-shot migration -----------------------------------------------------

    def migrate_legacy(self, *, writer: str = "legacy") -> int:
        """Move ``wiki/_meta/cost.jsonl`` into partitioned keys and delete it.

        Lines are grouped by their own ``at`` day under the ``legacy`` writer
        name, so re-running is a no-op once the file is gone. Returns the number
        of lines moved.
        """
        legacy = self._read_legacy()
        if not legacy:
            return 0
        by_day: dict[date, list[CostRecord]] = {}
        for record in legacy:
            by_day.setdefault(_as_utc(record.at).date(), []).append(record)
        for day, records in sorted(by_day.items()):
            key = cost_key(day, writer)
            try:
                existing = self.store.get(key).decode("utf-8")
            except ObjectNotFound:
                existing = ""
            payload = existing + ("" if existing.endswith("\n") or not existing else "\n")
            payload += "\n".join(record.model_dump_json() for record in records) + "\n"
            self.store.put(key, payload.encode("utf-8"), "application/x-ndjson")
        self.store.delete(COST_KEY)
        logger.info("ledger: migrated %d legacy line(s) into %d day key(s)",
                    len(legacy), len(by_day))
        return len(legacy)


def summarize(records: list[CostRecord], *, top_sources: int = 10) -> CostSummary:
    """Aggregate records into a :class:`CostSummary` along every axis the dashboard shows."""
    summary = CostSummary(call_count=len(records))
    per_source: dict[str, float] = {}
    for record in records:
        summary.total_usd += record.cost_usd
        summary.input_tokens += record.input_tokens
        summary.output_tokens += record.output_tokens
        summary.cache_read_tokens += record.cache_read_tokens
        summary.by_model[record.model] = summary.by_model.get(record.model, 0.0) + record.cost_usd
        summary.by_op[record.op] = summary.by_op.get(record.op, 0.0) + record.cost_usd
        summary.by_domain[record.domain or GENERAL_DOMAIN] = (
            summary.by_domain.get(record.domain or GENERAL_DOMAIN, 0.0) + record.cost_usd
        )
        summary.by_kind[record.kind] = summary.by_kind.get(record.kind, 0.0) + record.cost_usd
        day = _as_utc(record.at).date().isoformat()
        summary.by_day[day] = summary.by_day.get(day, 0.0) + record.cost_usd
        if record.source_id:
            per_source[record.source_id] = per_source.get(record.source_id, 0.0) + record.cost_usd
    summary.top_sources = sorted(per_source.items(), key=lambda kv: -kv[1])[:top_sources]
    if records:
        summary.since = min(_as_utc(r.at) for r in records)
        summary.until = max(_as_utc(r.at) for r in records)
    return summary
