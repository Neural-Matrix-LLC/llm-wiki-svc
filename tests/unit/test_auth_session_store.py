"""Server-side session store, refresh lock and the /mcp guard (docs/sso-integration-design.md v1.1
items 1, 2 and 7)."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading
import time

import anyio
import httpx
import pytest
from tests.sso_fake import FakeSSO, Key, access_claims
from tests.unit import test_auth_routes as _routes
from tests.unit.test_auth_routes import BROWSER_IP, STATIC, bearer, csrf_meta, login, make_settings

from llmwiki.auth.service import AuthService
from llmwiki.auth.session import PRUNE_INTERVAL_S, SessionStore

# shared fixtures (pytest registers module attributes that are fixtures)
key = _routes.key
sso = _routes.sso
build = _routes.build


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def tokens(n: int = 0) -> dict:
    return {"username": "alice", "sub": "42", "access": f"a{n}", "refresh": f"r{n}",
            "access_exp": 1_800_000_000 + 900}


# ---------------------------------------------------------------- store
def test_only_the_hash_of_the_id_is_stored(tmp_path) -> None:
    store = SessionStore(tmp_path / "s.db", 3600)
    sid, rec = store.create(**tokens())
    assert rec.id_hash == hashlib.sha256(sid.encode()).hexdigest()
    raw = b"".join(f.read_bytes() for f in tmp_path.glob("s.db*"))
    assert sid.encode() not in raw
    assert store.get(sid) == rec
    assert len(rec.csrf) >= 43


@pytest.mark.parametrize("bad", [None, "", "nope", "x" * 129])
def test_unknown_or_malformed_ids(tmp_path, bad) -> None:
    store = SessionStore(tmp_path / "s.db", 3600)
    store.create(**tokens())
    assert store.get(bad) is None


def test_absolute_lifetime_is_not_extended_by_use_or_refresh(tmp_path) -> None:
    clock = Clock()
    store = SessionStore(tmp_path / "s.db", 7 * 24 * 3600, clock=clock)
    sid, rec = store.create(**tokens())
    for _ in range(6):  # daily use + a refresh each day
        clock.now += 24 * 3600
        current = store.get(sid)
        assert current is not None
        store.refresh_locked(current.id_hash, current.refresh, 10**9,
                             lambda r: ("a2", f"r{clock.now}", int(clock.now) + 900))
    assert store.get(sid).created_at == rec.created_at
    assert store.remaining_s(store.get(sid)) == 24 * 3600
    clock.now += 24 * 3600  # exactly 7 days after login
    assert store.get(sid) is None
    conn = sqlite3.connect(tmp_path / "s.db")
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone() == (0,)


def test_expired_rows_are_pruned_at_start_and_hourly(tmp_path) -> None:
    clock = Clock()
    store = SessionStore(tmp_path / "s.db", 3 * 3600, clock=clock)
    for n in range(3):
        store.create(**tokens(n))
    clock.now += 2 * 3600
    keep, _ = store.create(**tokens(9))
    clock.now += 1.5 * 3600  # the first three are past 3 h now
    count = lambda: sqlite3.connect(tmp_path / "s.db").execute(  # noqa: E731
        "SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count() == 4
    assert store.get(keep) is not None  # > 1 h since the last prune -> prunes on this access
    assert count() == 1
    # a new process (store) prunes at start
    for n in range(2):
        store.create(**tokens(n))
    clock.now += 3 * 3600 + 1
    fresh = SessionStore(tmp_path / "s.db", 3 * 3600, clock=clock)
    fresh.init()
    assert count() == 0
    assert PRUNE_INTERVAL_S == 3600


def test_refresh_failure_leaves_the_row_untouched(tmp_path) -> None:
    store = SessionStore(tmp_path / "s.db", 3600)
    sid, rec = store.create(**tokens())

    def boom(_):
        raise RuntimeError("SSO said no")

    with pytest.raises(RuntimeError):
        store.refresh_locked(rec.id_hash, rec.refresh, 10**9, boom)
    assert store.get(sid) == rec


def test_refresh_lock_across_two_workers_sharing_the_file(tmp_path) -> None:
    """Two processes (two stores on one file) refresh one session concurrently: one SSO call."""
    a = SessionStore(tmp_path / "s.db", 3600)
    b = SessionStore(tmp_path / "s.db", 3600)
    sid, rec = a.create(**{**tokens(), "access_exp": int(time.time())})
    b.init()
    calls: list[str] = []
    barrier = threading.Barrier(2)
    results: list = []

    def do_refresh(current):
        calls.append(current.refresh)
        time.sleep(0.3)
        return "a-new", "r-new", int(time.time()) + 900

    def worker(store):
        barrier.wait()
        results.append(store.refresh_locked(rec.id_hash, rec.refresh, 60, do_refresh))

    threads = [threading.Thread(target=worker, args=(s,)) for s in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["r0"]  # exactly one refresh, with the original refresh token
    assert sorted(flag for _, flag in results) == [False, True]
    assert {r.access for r, _ in results} == {"a-new"}  # the loser reuses the new access token


def test_refresh_lock_reuses_even_when_the_new_token_is_due_again(tmp_path) -> None:
    """Access lifetime <= the 60 s margin (e.g. SSO_ACCESS_TTL_MIN=1): the second waiter still
    reuses the first one's tokens instead of refreshing again (its refresh token was rotated)."""
    store = SessionStore(tmp_path / "s.db", 3600)
    _, rec = store.create(**{**tokens(), "access_exp": int(time.time())})
    calls: list[str] = []

    def do_refresh(current):
        calls.append(current.refresh)
        return "a-new", "r-new", int(time.time()) + 30  # due again at once

    first, refreshed = store.refresh_locked(rec.id_hash, rec.refresh, 60, do_refresh)
    second, again = store.refresh_locked(rec.id_hash, rec.refresh, 60, do_refresh)
    assert refreshed and not again and calls == ["r0"] and second == first


# ---------------------------------------------------------------- service + middleware
def test_concurrent_requests_refresh_once(build, sso) -> None:
    """Concurrent requests on one session (asyncio lock + BEGIN IMMEDIATE): a single refresh."""
    sso.access_ttl = 30  # due for refresh on the next request
    client = build(auth_mode="sso")
    login(client)
    sid = client.cookies.get("llmwiki_session")
    sso.access_ttl = 900
    sso.refresh_delay = 0.3
    sso.calls.clear()
    app = client.app

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app, client=(BROWSER_IP, 1234))
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver",
                                     cookies={"llmwiki_session": sid}) as ac:
            statuses: list[int] = []

            async def one() -> None:
                statuses.append((await ac.get("/usage")).status_code)

            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(one)
            return statuses

    statuses = anyio.run(run)
    assert statuses == [200] * 5
    assert sso.paths().count("/api/v1/auth/refresh") == 1
    assert len(sso.live_refresh) == 1


def test_concurrent_refresh_from_threads_without_the_asyncio_lock(tmp_path) -> None:
    """The DB transaction alone is the guard (e.g. two uvicorn workers): one SSO refresh."""
    fake = FakeSSO(Key.generate())
    fake.access_ttl = 30
    cfg = make_settings(tmp_path, auth_mode="sso")
    services = [AuthService(cfg, fake.transport) for _ in range(2)]
    sid, rec, _ = services[0].login("alice", "alice-pw")
    fake.access_ttl = 900
    fake.refresh_delay = 0.3
    barrier = threading.Barrier(2)
    seen: list[str] = []

    def worker(svc):
        barrier.wait()
        current = svc.get_session(sid)
        new, principal = svc.session_principal(current, "203.0.113.5")
        seen.append(new.access)

    threads = [threading.Thread(target=worker, args=(s,)) for s in services]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.paths().count("/api/v1/auth/refresh") == 1
    assert len(set(seen)) == 1


def test_refresh_with_sso_down_but_valid_token_keeps_going(tmp_path, caplog) -> None:
    fake = FakeSSO(Key.generate())
    fake.access_ttl = 30
    cfg = make_settings(tmp_path, auth_mode="sso")
    svc = AuthService(cfg, fake.transport)
    sid, rec, _ = svc.login("alice", "alice-pw")
    fake.down = True
    new, principal = svc.session_principal(rec)
    assert new == rec and principal.username == "alice" and principal.via == "session"
    assert "SSO unreachable" in caplog.text


# ---------------------------------------------------------------- /mcp guard (v1.1 item 1)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def mcp(client, headers=None):
    return client.post("/mcp/", headers={**MCP_HEADERS, **(headers or {})}, json=INIT)


@pytest.mark.parametrize("mode,credential,status", [
    ("static", "none", 200),
    ("sso", "none", 401), ("sso", "static", 401), ("sso", "garbage", 401), ("sso", "sso", 200),
    ("sso", "sso_noperm", 403), ("sso", "expired", 401), ("sso", "session", 401),
    ("both", "none", 401), ("both", "static", 200), ("both", "sso", 200),
    ("both", "sso_noperm", 403),
])
def test_mcp_auth_matrix(build, key, mode, credential, status) -> None:
    with build(auth_mode=mode) as client:
        headers = {
            "none": {}, "static": bearer(STATIC), "garbage": bearer("x.y.z"),
            "sso": bearer(key.sign(access_claims())),
            "sso_noperm": bearer(key.sign(access_claims(perms=[]))),
            "expired": bearer(key.sign(access_claims(exp=int(time.time()) - 600))),
            "session": {},
        }[credential]
        if credential == "session":
            login(client)  # the browser session cookie is not accepted on /mcp
        r = mcp(client, headers)
        assert r.status_code == status, r.text
        if status == 401:
            assert r.json() == {"detail": "invalid or missing credentials"}
            assert r.headers["www-authenticate"] == 'Bearer error="invalid_token"'
        if status == 403:
            assert r.json() == {"detail": "forbidden", "error": "permission_denied"}


def test_basic_auth_header_shape_sent_to_the_sso(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client)
    scheme, _, value = sso.auth_headers[0].partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(value).decode().startswith("llm-wiki:")
    assert csrf_meta(client)
