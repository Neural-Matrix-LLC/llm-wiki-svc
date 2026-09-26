"""scripts/telegram_webhook.py and its compose one-shot: phase1-testing-guide.md §2 steps 3-5.

Nothing here reaches Telegram or a tunnel: the script takes an ``httpx.Client``
built on ``httpx.MockTransport``, which records every request. Pinned: (a) the
skip/refuse decisions made before any request, (b) the step-3 probe - 401 is
the only pass, and no ``setWebhook`` is sent after a failed one, (c) the exact
``setWebhook`` payload, (d) the ``getWebhookInfo`` judgement, (e) the bot token
never reaching output, and (f) the compose wiring that lets a cloudflared
container from another project reach ``api`` and makes ``up -d`` register.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import yaml

from llmwiki.config import Settings

REPO = Path(__file__).resolve().parents[2]
TOKEN = "123456:SECRET-bot-token"
BASE = "https://llmwiki.example.org"
HOOK = f"{BASE}/channels/telegram/webhook"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tw() -> ModuleType:
    return _load("telegram_webhook")


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        llm_provider="fake",
        telegram_bot_token=TOKEN,
        telegram_webhook_secret="hook-secret",
        public_base_url=BASE,
    )
    base.update(overrides)
    return Settings(**base)


class Recorder:
    """A MockTransport handler: the probe gets ``probe`` statuses in turn, the Bot API ``info``."""

    def __init__(self, probe=(401,), info=None, set_ok=True):
        self.requests: list[httpx.Request] = []
        self.probe = list(probe)
        self.info = info if info is not None else {"url": HOOK, "pending_update_count": 0}
        self.set_ok = set_ok

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/channels/telegram/webhook":
            status = self.probe.pop(0) if len(self.probe) > 1 else self.probe[0]
            return httpx.Response(status)
        if path.endswith("/setWebhook"):
            if not self.set_ok:
                return httpx.Response(400, json={"ok": False, "description": "bad webhook"})
            return httpx.Response(200, json={"ok": True, "result": True})
        if path.endswith("/getWebhookInfo"):
            return httpx.Response(200, json={"ok": True, "result": self.info})
        if path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        raise AssertionError(f"unexpected request {request.url}")

    def methods(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests]


def _run(tw, rec: Recorder, cfg: Settings, action: str = "sync") -> int:
    client = httpx.Client(transport=httpx.MockTransport(rec))
    if action == "sync":
        return tw.sync(cfg, client, sleep=lambda _s: None, clock=lambda: 1000.0)
    return tw.main([action], cfg=cfg, client=client)


# --- (a) decided before any request ------------------------------------------

@pytest.mark.parametrize("override", [{"telegram_bot_token": ""}, {"public_base_url": ""}])
def test_sync_is_a_noop_while_unconfigured(tw, override, capsys) -> None:
    rec = Recorder()
    assert _run(tw, rec, _settings(**override)) == 0
    assert rec.requests == []
    assert "[SKIP]" in capsys.readouterr().out


@pytest.mark.parametrize("override", [{"public_base_url": "http://llmwiki.example.org"},
                                      {"telegram_webhook_secret": ""}])
def test_sync_refuses_plain_http_and_a_blank_secret(tw, override) -> None:
    rec = Recorder()
    assert _run(tw, rec, _settings(**override)) == 1
    assert rec.requests == []


# --- (b) step 3: the tunnel probe ------------------------------------------------

def test_probe_goes_through_the_public_hostname_without_the_secret(tw) -> None:
    rec = Recorder()
    assert _run(tw, rec, _settings()) == 0
    probe = rec.requests[0]
    assert str(probe.url) == HOOK
    assert "x-telegram-bot-api-secret-token" not in probe.headers


@pytest.mark.parametrize(("status", "hint"), [
    (502, "llmwiki-api:8000"),
    (530, "llmwiki-api:8000"),
    (302, "Bypass"),
    (403, "Bypass"),
    (404, "TELEGRAM_BOT_TOKEN"),
])
def test_a_failed_probe_names_the_cause_and_registers_nothing(tw, status, hint, capsys) -> None:
    rec = Recorder(probe=(status,))
    assert _run(tw, rec, _settings()) == 1
    assert "setWebhook" not in rec.methods()
    assert hint in capsys.readouterr().out


def test_a_transient_probe_failure_is_retried_until_the_api_answers(tw) -> None:
    rec = Recorder(probe=(502, 502, 401))
    assert _run(tw, rec, _settings()) == 0
    assert rec.methods()[:3] == ["webhook"] * 3
    assert "setWebhook" in rec.methods()


# --- (c) step 4: the registration --------------------------------------------------

@pytest.mark.parametrize("base", [BASE, BASE + "/"])
def test_set_webhook_payload(tw, base) -> None:
    rec = Recorder()
    assert _run(tw, rec, _settings(public_base_url=base)) == 0
    (req,) = [r for r in rec.requests if r.url.path.endswith("/setWebhook")]
    assert req.url.path == f"/bot{TOKEN}/setWebhook"
    body = json.loads(req.content)
    assert body["url"] == HOOK
    assert body["secret_token"] == "hook-secret"
    assert {"message", "channel_post"} <= set(body["allowed_updates"])


def test_a_rejected_set_webhook_fails_sync(tw) -> None:
    assert _run(tw, Recorder(set_ok=False), _settings()) == 1


# --- (d) step 5: getWebhookInfo -----------------------------------------------------

def test_sync_fails_when_telegram_holds_another_url(tw) -> None:
    rec = Recorder(info={"url": "https://elsewhere.example/hook"})
    assert _run(tw, rec, _settings()) == 1


def test_sync_ignores_an_error_older_than_this_registration(tw) -> None:
    rec = Recorder(info={"url": HOOK, "last_error_date": 999, "last_error_message": "old"})
    assert _run(tw, rec, _settings()) == 0
    rec = Recorder(info={"url": HOOK, "last_error_date": 1000, "last_error_message": "new"})
    assert _run(tw, rec, _settings()) == 1


def test_info_fails_on_any_last_error(tw) -> None:
    ok = Recorder()
    assert _run(tw, ok, _settings(), "info") == 0
    assert ok.methods() == ["getWebhookInfo"]
    bad = Recorder(info={"url": HOOK, "last_error_date": 1, "last_error_message": "Wrong response"})
    assert _run(tw, bad, _settings(), "info") == 1


def test_delete_calls_delete_webhook(tw) -> None:
    rec = Recorder()
    assert _run(tw, rec, _settings(), "delete") == 0
    assert rec.methods() == ["deleteWebhook"]


# --- (e) the token never reaches output ----------------------------------------------

def test_the_bot_token_is_never_printed(tw, capsys) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/channels/telegram/webhook":
            return httpx.Response(401)
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    assert tw.sync(_settings(), client, sleep=lambda _s: None) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert "***" in captured.out


# --- (f) compose wiring ---------------------------------------------------------------

@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


def test_up_runs_the_webhook_one_shot_after_api(compose) -> None:
    svc = compose["services"]["telegram-webhook"]
    assert "profiles" not in svc
    assert svc["entrypoint"] == ["python", "scripts/telegram_webhook.py"]
    assert svc["command"] == ["sync"]
    assert svc["depends_on"]["api"]["condition"] == "service_healthy"
    assert svc["env_file"][0]["path"] == ".env"
    assert "telegram-webhook" not in compose["services"]["api"].get("depends_on", {})


def test_a_cloudflared_from_another_project_can_reach_api(compose) -> None:
    assert compose["networks"]["default"]["name"] == "llmwiki-net"
    assert "llmwiki-api" in compose["services"]["api"]["networks"]["default"]["aliases"]


def test_the_tunnel_compose_joins_llmwiki_net_and_reads_its_own_env_file() -> None:
    """Deployed in its own directory, whose `.env` is the only file Hostinger reads."""
    tunnel = yaml.safe_load((REPO / "docker-compose-cloudflared.yml").read_text())
    svc = tunnel["services"]["cloudflared"]
    assert "llmwiki-net" in svc["networks"]
    assert tunnel["networks"]["llmwiki-net"]["external"] is True
    assert svc["env_file"] == [{"path": ".env", "required": True}]
    # env_file alone puts TUNNEL_TOKEN in the container; no ${...} interpolation.
    assert "environment" not in svc
    assert (REPO / ".env.cloudflared.example").is_file()
