"""Cost alerts, the notifier backends and the monthly hard cap (Phase 2, plan §21.2 C4/C5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.models.plan import CostRecord
from llmwiki.notify.base import Notifier
from llmwiki.notify.fake import FakeNotifier
from llmwiki.notify.log import LogNotifier
from llmwiki.notify.telegram import TelegramNotifier
from llmwiki.storage.layout import ALERTS_KEY
from llmwiki.wiki.alerts import CostAlerts
from llmwiki.wiki.ledger import CostLedger

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


# --- notifiers ---------------------------------------------------------------------------


def test_notifier_backends_satisfy_the_protocol() -> None:
    assert isinstance(FakeNotifier(), Notifier) and isinstance(LogNotifier(), Notifier)


def test_log_notifier_warns(caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="llmwiki.alerts"):
        assert LogNotifier().send("spend alert") is True
    assert "spend alert" in caplog.text


def test_telegram_notifier_posts_send_message_and_never_raises() -> None:
    seen: dict = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier("tok", "4242")
    notifier._client = httpx.Client(base_url="https://api.telegram.org/bottok",
                                    transport=httpx.MockTransport(ok))
    assert notifier.send("hello") is True
    assert seen["path"] == "/bottok/sendMessage" and seen["body"] == {"chat_id": "4242",
                                                                        "text": "hello"}

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    notifier._client = httpx.Client(base_url="https://api.telegram.org/bottok",
                                    transport=httpx.MockTransport(down))
    assert notifier.send("hello") is False


# --- CostAlerts ---------------------------------------------------------------------------


def _alerts(store, **thresholds) -> tuple[CostAlerts, FakeNotifier]:
    cfg = Settings(_env_file=None, storage_backend="local", local_storage_path=store.root,
                   vector_backend="memory", embedding_backend="fake", llm_backend="fake",
                   **thresholds)
    notifier = FakeNotifier()
    return CostAlerts(store, CostLedger(store), notifier, cfg, queued=lambda: 3), notifier


def _spend(store, usd: float, at: datetime) -> None:
    """Write a ledger line under the key of its own day, as if appended back then."""
    from llmwiki.storage.base import ObjectNotFound
    from llmwiki.storage.layout import cost_key

    key = cost_key(at.date(), "api")
    try:
        existing = store.get(key).decode()
    except ObjectNotFound:
        existing = ""
    line = CostRecord(op="x", model="m", cost_usd=usd, at=at).model_dump_json() + "\n"
    store.put(key, (existing + line).encode(), "application/x-ndjson")


def test_nothing_configured_never_fires_and_never_reads_state(store) -> None:
    alerts, notifier = _alerts(store)
    _spend(store, 100.0, NOW)
    assert alerts.evaluate(100.0, now=NOW) == []
    assert notifier.sent == [] and not store.exists(ALERTS_KEY)
    assert alerts.capped(NOW) is False


def test_daily_alert_fires_once_per_day(store) -> None:
    alerts, notifier = _alerts(store, cost_alert_daily_usd=1.0)
    _spend(store, 0.6, NOW)
    assert alerts.evaluate(0.6, now=NOW) == []
    _spend(store, 0.6, NOW)
    fired = alerts.evaluate(0.6, now=NOW)
    assert len(fired) == 1 and "COST_ALERT_DAILY_USD" in fired[0] and "$1.20" in fired[0]
    assert alerts.evaluate(0.5, now=NOW + timedelta(hours=1)) == [], "deduped for the day"
    assert notifier.sent == fired
    assert json.loads(store.get(ALERTS_KEY))["daily_warned"] == "2026-09-19"
    # A new day re-arms it.
    tomorrow = NOW + timedelta(days=1)
    _spend(store, 2.0, tomorrow)
    assert len(alerts.evaluate(2.0, now=tomorrow)) == 1


def test_monthly_alert_and_hard_cap_fire_once_per_month_and_cap_parks(store) -> None:
    alerts, notifier = _alerts(store, cost_alert_monthly_usd=5.0, cost_hard_cap_monthly_usd=8.0)
    _spend(store, 5.5, NOW)
    fired = alerts.evaluate(5.5, now=NOW)
    assert [("MONTHLY" in f, "hard cap" in f) for f in fired] == [(True, False)]
    assert alerts.capped(NOW) is False

    _spend(store, 3.0, NOW)
    fired = alerts.evaluate(3.0, now=NOW)
    assert len(fired) == 1 and "hard cap" in fired[0] and "3 source(s) waiting" in fired[0]
    assert "2026-10-01" in fired[0], "says when processing resumes"
    assert alerts.capped(NOW) is True
    assert alerts.evaluate(1.0, now=NOW) == [], "both deduped for the month"
    state = json.loads(store.get(ALERTS_KEY))
    assert state["monthly_warned"] == "2026-09" and state["capped"] == "2026-09"
    assert len(notifier.sent) == 2

    # Month rollover: totals reset, the cap lifts, the alerts re-arm.
    october = datetime(2026, 10, 2, tzinfo=UTC)
    assert alerts.capped(october) is False
    assert alerts.status(october)["month_usd"] == 0.0


def test_totals_are_read_from_the_ledger_and_survive_a_restart(store) -> None:
    _spend(store, 2.0, NOW - timedelta(days=3))
    _spend(store, 1.5, NOW)
    alerts, _ = _alerts(store, cost_hard_cap_monthly_usd=3.0)
    assert alerts.totals(NOW) == (3.5, 1.5)
    assert alerts.capped(NOW) is True, "a fresh process sees the month's spend"
    assert alerts.status(NOW)["capped"] is True


def test_last_months_spend_does_not_count(store) -> None:
    _spend(store, 50.0, NOW - timedelta(days=40))
    alerts, _ = _alerts(store, cost_hard_cap_monthly_usd=10.0)
    assert alerts.totals(NOW) == (0.0, 0.0) and not alerts.capped(NOW)


def test_dedup_state_survives_a_new_instance(store) -> None:
    alerts, _ = _alerts(store, cost_alert_daily_usd=1.0)
    _spend(store, 2.0, NOW)
    assert len(alerts.evaluate(2.0, now=NOW)) == 1
    again, notifier = _alerts(store, cost_alert_daily_usd=1.0)
    assert again.evaluate(0.0, now=NOW) == [] and notifier.sent == []


def test_unreadable_state_starts_fresh(store) -> None:
    store.put(ALERTS_KEY, b"not json")
    alerts, _ = _alerts(store, cost_alert_daily_usd=1.0)
    _spend(store, 2.0, NOW)
    assert len(alerts.evaluate(2.0, now=NOW)) == 1


# --- through tools: the cap pauses processing, not capture or answers ----------------------


@pytest.fixture
def capped_cfg(tmp_path):
    from llmwiki import factory

    cfg = Settings(
        _env_file=None, storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake", local_storage_path=tmp_path / "data",
        embedding_dim=64, worker_mode="inline", notify_backend="fake",
        cost_hard_cap_monthly_usd=0.01, ingest_api_token=SecretStr("test-token"),
    )
    factory.reset()
    yield cfg
    factory.reset()


def test_hard_cap_pauses_processing_but_not_capture_search_or_answer(capped_cfg) -> None:
    """Load-bearing (plan §21.10): under the cap, raw/ still fills, queries still answer."""
    from llmwiki import factory

    store = factory.object_store(capped_cfg)
    first = tools.ingest_now(text="# Chunking\n\nWindows over text.\n", cfg=capped_cfg)
    assert first.state == "done", "nothing spent yet, so the first source compiles"
    # Push the ledger over the cap the way real spend would.
    factory.ledger(capped_cfg).append([CostRecord(op="x", model="m", cost_usd=5.0)])
    factory.cost_alerts(capped_cfg).refresh()
    assert tools.processing_capped(cfg=capped_cfg) is True

    ref = tools.ingest_source(text="# Paged attention\n\nKV blocks on the GPU.\n", cfg=capped_cfg)
    assert not ref.duplicate and store.exists(f"raw/{ref.source_id}/meta.json")
    tools.enqueue_source(ref.source_id, cfg=capped_cfg)
    status = tools.get_source_status(ref.source_id, cfg=capped_cfg)
    assert status.state == "paused" and "COST_HARD_CAP_MONTHLY_USD" in (status.error or "")
    assert tools.worker_status(cfg=capped_cfg).parked == [ref.source_id]

    assert tools.search_wiki("chunking", cfg=capped_cfg), "search keeps working"
    answer = tools.answer("how is text chunked?", cfg=capped_cfg)
    assert answer.text, "answers keep working (and their spend keeps counting)"
    assert tools.budget_status(cfg=capped_cfg)["capped"] is True
    assert factory.notifier(capped_cfg).sent and "hard cap" in factory.notifier(capped_cfg).sent[0]

    # Raise the cap: resume drains the parked source.
    raised = capped_cfg.model_copy(update={"cost_hard_cap_monthly_usd": 1000.0})
    factory.reset()
    assert tools.processing_capped(cfg=raised) is False
    tools.enqueue_source(ref.source_id, cfg=raised)
    assert tools.get_source_status(ref.source_id, cfg=raised).state == "done"


def test_usage_summary_defaults_to_month_to_date_and_takes_a_month(capped_cfg) -> None:
    from llmwiki import factory

    ledger = factory.ledger(capped_cfg)
    ledger.append([CostRecord(op="a", model="m", cost_usd=1.0, domain="ml", kind="query")])
    store = factory.object_store(capped_cfg)
    _spend(store, 7.0, NOW - timedelta(days=60))  # two months ago

    this_month = tools.usage_summary(cfg=capped_cfg)
    assert this_month.call_count == 1 and this_month.by_domain == {"ml": 1.0}
    assert this_month.by_kind == {"query": 1.0} and this_month.since is not None

    old_month = (NOW - timedelta(days=60)).strftime("%Y-%m")
    assert tools.usage_summary(month=old_month, cfg=capped_cfg).total_usd == 7.0
    assert tools.usage_summary(month=old_month, domain="ml", cfg=capped_cfg).call_count == 0
    with pytest.raises(ValueError):
        tools.usage_summary(month="2026-9", cfg=capped_cfg)


def test_migrate_moves_the_legacy_file_through_tools(capped_cfg) -> None:
    from llmwiki import factory
    from llmwiki.storage.layout import COST_KEY

    store = factory.object_store(capped_cfg)
    store.put(COST_KEY, (CostRecord(op="old", model="m", cost_usd=0.5).model_dump_json()
                         + "\n").encode())
    assert tools.migrate_cost_ledger(cfg=capped_cfg) == 1
    assert not store.exists(COST_KEY)
    assert tools.usage_summary(cfg=capped_cfg).by_op == {"old": 0.5}
    assert tools.migrate_cost_ledger(cfg=capped_cfg) == 0


def test_usage_and_dashboard_routes(capped_cfg, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from llmwiki import config, factory
    from llmwiki.api.app import create_app

    monkeypatch.setattr(config, "settings", capped_cfg)
    monkeypatch.setattr("llmwiki.api.routes.settings", capped_cfg)
    monkeypatch.setattr("llmwiki.tools.default_settings", capped_cfg)
    monkeypatch.setattr("llmwiki.factory.default_settings", capped_cfg)
    factory.ledger(capped_cfg).append([CostRecord(op="answer_query", model="m", cost_usd=0.25,
                                                   domain="ml", kind="query", source_id="s1")])
    tools.upsert_domain("ml", "Machine learning", cfg=capped_cfg)
    auth = {"Authorization": "Bearer test-token"}

    with TestClient(create_app()) as client:
        assert client.get("/usage").status_code == 401
        body = client.get("/usage", headers=auth).json()
        assert body["by_op"] == {"answer_query": 0.25} and body["by_domain"] == {"ml": 0.25}
        assert client.get("/usage", params={"month": "nope"}, headers=auth).status_code == 422

        assert client.get("/dashboard").status_code == 401
        assert client.get("/dashboard", params={"token": "wrong"}).status_code == 401
        page = client.get("/dashboard", params={"token": "test-token"})
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        html = page.text
        assert "<script" not in html, "no JavaScript, no external assets"
        assert "Machine learning" in html and "answer_query" in html and "s1" in html
        assert "hard cap" in html.lower() and "<svg" in html
        via_header = client.get("/dashboard", headers=auth)
        assert via_header.status_code == 200
        assert client.get("/healthz").json()["budget"]["hard_cap_usd"] == 0.01
