"""The partitioned cost ledger (Phase 2, plan §21.2 C1, §21.6.6).

One key per day per writing process; a windowed read lists only the months it
overlaps; the pre-Phase-2 single file is read through until migrated.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from tests.doubles import SpyObjectStore

from llmwiki.models.plan import CostRecord
from llmwiki.storage.layout import (
    COST_KEY,
    COST_PREFIX,
    check_cost_writer,
    cost_key,
    cost_key_day,
    cost_month_prefix,
)
from llmwiki.wiki.compiler import read_cost_ledger
from llmwiki.wiki.ledger import CostLedger, summarize


def _record(**overrides) -> CostRecord:
    base = {"op": "summarize_source", "model": "m", "input_tokens": 10, "output_tokens": 1,
            "cost_usd": 0.001}
    base.update(overrides)
    return CostRecord(**base)


# --- keys -----------------------------------------------------------------------


def test_cost_key_is_partitioned_by_day_and_writer() -> None:
    assert cost_key(date(2026, 9, 18), "api") == "wiki/_meta/cost/2026-09/18-api.jsonl"
    assert cost_key(date(2026, 1, 2), "backfill") == "wiki/_meta/cost/2026-01/02-backfill.jsonl"
    assert cost_month_prefix("2026-09") == "wiki/_meta/cost/2026-09/"
    assert cost_key(date(2026, 9, 18), "api").startswith(cost_month_prefix("2026-09"))


def test_cost_key_day_round_trips_and_rejects_strangers() -> None:
    assert cost_key_day("wiki/_meta/cost/2026-09/18-api.jsonl") == date(2026, 9, 18)
    assert cost_key_day("wiki/_meta/cost/alerts.json") is None
    assert cost_key_day("wiki/_meta/cost.jsonl") is None
    assert cost_key_day("wiki/_meta/cost/2026-09/notes.jsonl") is None


@pytest.mark.parametrize("bad", ["", "API", "a b", "x" * 17, "../x", "wiki/"])
def test_writer_names_are_validated_before_they_become_keys(bad: str) -> None:
    with pytest.raises(ValueError):
        check_cost_writer(bad)
    with pytest.raises(ValueError):
        cost_key(date(2026, 9, 18), bad)


def test_month_prefix_rejects_non_months() -> None:
    with pytest.raises(ValueError):
        cost_month_prefix("2026-9")


# --- append -----------------------------------------------------------------------


def test_append_writes_todays_key_for_this_writer_only(store) -> None:
    ledger = CostLedger(store, writer="cli")
    ledger.append([_record(), _record(op="plan_compile")], kind="compile", source_id="s1")

    key = cost_key(datetime.now(UTC).date(), "cli")
    lines = store.get(key).decode().splitlines()
    assert len(lines) == 2
    assert not store.exists(COST_KEY), "the legacy single file is never written again"
    records = ledger.read()
    assert {r.op for r in records} == {"summarize_source", "plan_compile"}
    assert all(r.source_id == "s1" and r.kind == "compile" for r in records)


def test_append_tags_kind_and_domain_over_the_records_own_values(store) -> None:
    ledger = CostLedger(store)
    ledger.append([_record(kind="compile", domain="general")], kind="query", domain="ml")
    (record,) = ledger.read()
    assert (record.kind, record.domain) == ("query", "ml")


def test_two_writers_never_touch_the_same_object(store) -> None:
    CostLedger(store, writer="api").append([_record()])
    CostLedger(store, writer="cli").append([_record()])
    keys = store.list(COST_PREFIX)
    assert len(keys) == 2 and len({k.rsplit("-", 1)[1] for k in keys}) == 2
    assert len(CostLedger(store).read()) == 2


def test_concurrent_appends_from_one_process_lose_nothing(store) -> None:
    ledger = CostLedger(store, writer="api")
    threads = [
        threading.Thread(target=ledger.append, args=([_record(op=f"op{i}")],))
        for i in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ledger.read()) == 12


def test_empty_append_writes_nothing(store) -> None:
    assert CostLedger(store).append([]) == 0
    assert store.list(COST_PREFIX) == []


# --- read --------------------------------------------------------------------------


def _seed_month(store, month: str, day: int, count: int, writer: str = "api") -> None:
    when = datetime.fromisoformat(f"{month}-{day:02d}T12:00:00+00:00")
    key = cost_key(when.date(), writer)
    lines = "\n".join(_record(at=when).model_dump_json() for _ in range(count)) + "\n"
    store.put(key, lines.encode(), "application/x-ndjson")


def test_windowed_read_lists_only_the_months_in_the_window(store) -> None:
    """The scale guarantee (plan §21.10): three months seeded, one month asked for."""
    _seed_month(store, "2026-07", 3, 2)
    _seed_month(store, "2026-08", 10, 3)
    _seed_month(store, "2026-09", 1, 4)
    spy = SpyObjectStore(store)

    records = CostLedger(spy).read(
        since=datetime(2026, 8, 1, tzinfo=UTC), until=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert len(records) == 3
    assert spy.list_prefixes == [cost_month_prefix("2026-08")]
    assert spy.distinct_gets(COST_PREFIX) == {cost_key(date(2026, 8, 10), "api")}


def test_window_spanning_months_lists_each_month_once(store) -> None:
    _seed_month(store, "2026-07", 30, 1)
    _seed_month(store, "2026-08", 1, 1)
    spy = SpyObjectStore(store)
    records = CostLedger(spy).read(
        since=datetime(2026, 7, 29, tzinfo=UTC), until=datetime(2026, 8, 2, tzinfo=UTC),
    )
    assert len(records) == 2
    assert spy.list_prefixes == [cost_month_prefix("2026-07"), cost_month_prefix("2026-08")]


def test_unwindowed_read_returns_everything_oldest_first(store) -> None:
    _seed_month(store, "2026-09", 2, 1)
    _seed_month(store, "2026-07", 5, 1)
    records = CostLedger(store).read()
    assert [r.at.month for r in records] == [7, 9]


def test_legacy_single_file_is_read_through(store) -> None:
    old = _record(at=datetime.now(UTC) - timedelta(days=1), op="legacy")
    store.put(COST_KEY, (old.model_dump_json() + "\n").encode())
    CostLedger(store).append([_record(op="new")])

    ops = {r.op for r in CostLedger(store).read()}
    assert ops == {"legacy", "new"}
    assert {r.op for r in read_cost_ledger(store)} == ops, "the compat reader sees both"


def test_read_can_filter_by_domain(store) -> None:
    CostLedger(store).append([_record()], domain="ml")
    CostLedger(store).append([_record()], domain="general")
    assert len(CostLedger(store).read(domain="ml")) == 1


def test_malformed_lines_are_skipped_not_fatal(store) -> None:
    key = cost_key(datetime.now(UTC).date(), "api")
    store.put(key, b'{"op":"a","model":"m"}\nnot json\n\n')
    assert len(CostLedger(store).read()) == 1


# --- migrate -----------------------------------------------------------------------


def test_migrate_moves_legacy_lines_into_day_keys_and_deletes_the_file(store) -> None:
    day1 = datetime(2026, 5, 3, 9, tzinfo=UTC)
    day2 = datetime(2026, 6, 1, 9, tzinfo=UTC)
    lines = [_record(at=day1), _record(at=day1), _record(at=day2)]
    store.put(COST_KEY, ("\n".join(r.model_dump_json() for r in lines) + "\n").encode())

    moved = CostLedger(store).migrate_legacy()

    assert moved == 3
    assert not store.exists(COST_KEY)
    assert store.exists(cost_key(day1.date(), "legacy"))
    assert store.exists(cost_key(day2.date(), "legacy"))
    assert len(CostLedger(store).read()) == 3
    assert CostLedger(store).migrate_legacy() == 0, "idempotent once the file is gone"


# --- summarize -------------------------------------------------------------------


def test_summarize_aggregates_along_every_axis() -> None:
    at = datetime(2026, 9, 18, 10, tzinfo=UTC)
    records = [
        _record(op="a", model="m1", cost_usd=1.0, domain="ml", kind="compile",
                source_id="s1", at=at),
        _record(op="b", model="m2", cost_usd=2.0, domain="general", kind="query",
                at=at + timedelta(days=1), cache_read_tokens=5),
        _record(op="a", model="m1", cost_usd=4.0, domain="ml", kind="compile",
                source_id="s2", at=at),
    ]
    summary = summarize(records)
    assert summary.call_count == 3 and summary.total_usd == 7.0
    assert summary.by_model == {"m1": 5.0, "m2": 2.0}
    assert summary.by_op == {"a": 5.0, "b": 2.0}
    assert summary.by_domain == {"ml": 5.0, "general": 2.0}
    assert summary.by_kind == {"compile": 5.0, "query": 2.0}
    assert summary.by_day == {"2026-09-18": 5.0, "2026-09-19": 2.0}
    assert summary.top_sources == [("s2", 4.0), ("s1", 1.0)]
    assert summary.cache_read_tokens == 5
    assert (summary.since, summary.until) == (at, at + timedelta(days=1))


def test_summarize_of_nothing_is_empty() -> None:
    summary = summarize([])
    assert summary.call_count == 0 and summary.total_usd == 0.0 and summary.since is None
