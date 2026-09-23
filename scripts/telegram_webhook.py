#!/usr/bin/env python3
"""Register the Telegram webhook from ``.env``: phase1-testing-guide.md §2 steps 3-5 in one go.

On the production box there is no repo checkout, no ``curl`` in the image and
no ``$TELEGRAM_BOT_TOKEN`` in the host shell: the token and secret live only in
the ``.env`` the ``api`` container reads. This script runs in the same image
with the same ``.env`` (the ``telegram-webhook`` one-shot in
``docker-compose.yml``), so ``docker compose up -d`` is what registers the bot:

  sync (default) - three named checks, exit 1 at the first failure:
    3. Tunnel    - POST <PUBLIC_BASE_URL>/channels/telegram/webhook through the
                   public hostname *without* the secret header. 401 is the pass:
                   Cloudflare Tunnel -> llmwiki-net -> api is up, the route is
                   mounted and the secret is enforced (channels/telegram.py).
                   ``setWebhook`` never probes the URL itself, so without this a
                   broken tunnel would only surface on the first real message.
    4. Register  - ``setWebhook`` with the URL, TELEGRAM_WEBHOOK_SECRET and the
                   update types the route reads. Always sent, even when the URL
                   is unchanged: the secret may have been rotated.
    5. Verify    - ``getWebhookInfo``: the URL must match, and no delivery error
                   may be newer than this registration.
  info           - step 5 alone; exit 1 on a URL mismatch or any last error.
  delete         - ``deleteWebhook`` (guide §2 step 10); ``--drop-pending`` too.

    docker compose up -d                                 # runs `sync`
    docker compose logs telegram-webhook                 # its report
    docker compose run --rm telegram-webhook info
    docker compose run --rm telegram-webhook delete

``sync`` is a no-op (exit 0) while TELEGRAM_BOT_TOKEN or PUBLIC_BASE_URL is
blank, so a dev ``docker compose up`` - where PUBLIC_BASE_URL stays blank -
never repoints a shared bot at the wrong host. The bot token is redacted from
everything printed, including httpx errors (they carry the request URL).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

from llmwiki.config import Settings, load_settings

API_BASE = "https://api.telegram.org"
WEBHOOK_PATH = "/channels/telegram/webhook"
# What channels/telegram.py's _handle_update reads; everything else is dropped there anyway.
ALLOWED_UPDATES = ["message", "channel_post"]
# Cloudflare's answers while the tunnel or the origin is not (yet) reachable.
# `up -d` starts cloudflared and api independently, so these are retried briefly.
_TRANSIENT = {502, 503, 504, 520, 521, 522, 523, 524, 530}
PROBE_ATTEMPTS = 6
PROBE_DELAY_S = 5.0


class StepFailed(RuntimeError):
    """One named step did not hold."""


def webhook_url(base: str) -> str:
    return base.rstrip("/") + WEBHOOK_PATH


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def probe_hint(status: int) -> str:
    """What a non-401 answer from the public webhook URL means on this deployment."""
    if status in _TRANSIENT:
        return ("the tunnel cannot reach the api container - is cloudflared on llmwiki-net, "
                "and does the public hostname point at http://llmwiki-api:8000?")
    if status in (301, 302, 303, 307, 308, 403):
        return ("Cloudflare Access (or a redirect) is in front of the webhook path - add an "
                f"Access application for {WEBHOOK_PATH} with a Bypass/Everyone policy")
    if status == 404:
        return "the route is not mounted - TELEGRAM_BOT_TOKEN is not set in the api container's env"
    return "unexpected answer - is the hostname routed to llmwiki at all?"


def check_tunnel(client: httpx.Client, url: str, sleep: Callable[[float], None]) -> None:
    status: int | None = None
    error = ""
    for attempt in range(PROBE_ATTEMPTS):
        if attempt:
            sleep(PROBE_DELAY_S)
        try:
            status = client.post(url, json={}, follow_redirects=False).status_code
            error = ""
        except httpx.HTTPError as exc:
            status, error = None, str(exc)
            continue
        if status not in _TRANSIENT:
            break
    if status == 401:
        return
    if status is None:
        raise StepFailed(f"{url} unreachable: {error}")
    raise StepFailed(f"{url} answered {status}, expected 401: {probe_hint(status)}")


def _call(client: httpx.Client, token: str, method: str, **payload: Any) -> dict:
    resp = client.post(f"{API_BASE}/bot{token}/{method}", json=payload or None)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not body.get("ok"):
        detail = body.get("description", "")
        raise StepFailed(f"{method}: HTTP {resp.status_code} {detail}".rstrip())
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def check_info(info: dict, expected_url: str | None, *, since: float | None = None) -> str:
    """Judge ``getWebhookInfo``; errors older than ``since`` (epoch s) predate this registration.

    ``expected_url`` None (PUBLIC_BASE_URL blank) only requires that some URL is set.
    """
    url = info.get("url", "")
    if not url or (expected_url is not None and url != expected_url):
        want = expected_url or "a webhook"
        raise StepFailed(f"registered url is {url or '(none)'}, expected {want}")
    err_date = info.get("last_error_date")
    err_msg = info.get("last_error_message")
    if err_msg and (since is None or (err_date or 0) >= since):
        raise StepFailed(f"Telegram's last delivery failed: {err_msg}")
    pending = info.get("pending_update_count", 0)
    return f"{url} (pending updates: {pending})"


def sync(cfg: Settings, client: httpx.Client, *, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.time) -> int:
    token = cfg.telegram_bot_token.get_secret_value()
    secret = cfg.telegram_webhook_secret.get_secret_value()
    base = cfg.public_base_url.strip()
    if not token:
        print("  [SKIP] TELEGRAM_BOT_TOKEN is blank: the Telegram channel is off")
        return 0
    if not base:
        print("  [SKIP] PUBLIC_BASE_URL is blank: nothing to register (the dev default)")
        return 0
    if not base.startswith("https://"):
        print(f"  [FAIL] PUBLIC_BASE_URL={base}: Telegram only calls https:// webhooks")
        return 1
    if not secret:
        print("  [FAIL] TELEGRAM_WEBHOOK_SECRET is blank: the route would 401 every update")
        return 1
    url = webhook_url(base)
    try:
        print("3. Tunnel")
        check_tunnel(client, url, sleep)
        print(f"  [OK]   {url} -> 401 without the secret")

        print("4. Register")
        started = int(clock())
        _call(client, token, "setWebhook", url=url, secret_token=secret,
              allowed_updates=ALLOWED_UPDATES)
        print(f"  [OK]   setWebhook {url}")

        print("5. Verify")
        print(f"  [OK]   {check_info(_call(client, token, 'getWebhookInfo'), url, since=started)}")
    except (StepFailed, httpx.HTTPError) as exc:
        print(f"  [FAIL] {_redact(str(exc), token)}")
        return 1
    return 0


def info(cfg: Settings, client: httpx.Client) -> int:
    token = cfg.telegram_bot_token.get_secret_value()
    if not token:
        print("  [FAIL] TELEGRAM_BOT_TOKEN is blank")
        return 1
    try:
        base = cfg.public_base_url.strip()
        result = _call(client, token, "getWebhookInfo")
        print(f"  [OK]   {check_info(result, webhook_url(base) if base else None)}")
    except (StepFailed, httpx.HTTPError) as exc:
        print(f"  [FAIL] {_redact(str(exc), token)}")
        return 1
    return 0


def delete(cfg: Settings, client: httpx.Client, *, drop_pending: bool) -> int:
    token = cfg.telegram_bot_token.get_secret_value()
    if not token:
        print("  [FAIL] TELEGRAM_BOT_TOKEN is blank")
        return 1
    try:
        _call(client, token, "deleteWebhook", drop_pending_updates=drop_pending)
    except (StepFailed, httpx.HTTPError) as exc:
        print(f"  [FAIL] {_redact(str(exc), token)}")
        return 1
    print("  [OK]   webhook deleted")
    return 0


def main(argv: list[str] | None = None, *, cfg: Settings | None = None,
         client: httpx.Client | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", nargs="?", default="sync", choices=("sync", "info", "delete"))
    ap.add_argument("--drop-pending", action="store_true",
                    help="delete: also discard updates Telegram is still holding")
    args = ap.parse_args(argv)

    cfg = cfg or load_settings()
    with client or httpx.Client(timeout=15.0) as http:
        if args.action == "info":
            return info(cfg, http)
        if args.action == "delete":
            return delete(cfg, http, drop_pending=args.drop_pending)
        return sync(cfg, http)


if __name__ == "__main__":
    sys.exit(main())
