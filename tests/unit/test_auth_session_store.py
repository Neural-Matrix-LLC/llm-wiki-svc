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


# ---------------------------------------------------------------- v1.2 clarifications
def test_db_file_0600_and_new_directory_0700(tmp_path) -> None:
    """v1.2 D-16."""
    import os

    old = os.umask(0o022)
    try:
        store = SessionStore(tmp_path / "new-dir" / "s.db", 3600)
        store.init()
    finally:
        os.umask(old)
    assert (tmp_path / "new-dir" / "s.db").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "new-dir").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o666, 0o620])
def test_refuses_a_group_or_world_accessible_db(tmp_path, mode) -> None:
    path = tmp_path / "s.db"
    SessionStore(path, 3600).init()
    path.chmod(mode)
    with pytest.raises(RuntimeError, match="must not be accessible by group or others"):
        SessionStore(path, 3600).init()


def test_service_refuses_to_start_with_a_readable_db(build, tmp_path) -> None:
    db = tmp_path / "data" / "sessions.sqlite3"
    db.parent.mkdir(parents=True)
    SessionStore(db, 3600).init()
    db.chmod(0o644)
    with pytest.raises(RuntimeError, match="chmod 600"):
        build(auth_mode="sso")


def test_idle_timeout(tmp_path) -> None:
    """v1.2 Q-27: SESSION_IDLE_HOURS ends an unused session; use keeps it alive."""
    clock = Clock()
    store = SessionStore(tmp_path / "s.db", 7 * 24 * 3600, clock=clock, idle_s=12 * 3600)
    active, _ = store.create(**tokens(1))
    idle, _ = store.create(**tokens(2))
    for _ in range(4):  # 4 x 4 h with use of "active" only
        clock.now += 4 * 3600
        assert store.get(active) is not None
    assert store.get(idle) is None  # 16 h unused
    clock.now += 12 * 3600
    assert store.get(active) is None


def test_idle_rows_are_pruned(tmp_path) -> None:
    clock = Clock()
    store = SessionStore(tmp_path / "s.db", 7 * 24 * 3600, clock=clock, idle_s=3600)
    store.create(**tokens())
    clock.now += 3601
    assert store.prune() == 1


def test_session_idle_hours_setting(build, sso) -> None:
    client = build(auth_mode="sso", session_idle_hours=0.5)
    login(client)
    from llmwiki import tools

    assert tools.auth_service().store.idle_s == 1800
    header = [h for h in client.get("/dashboard").headers.get_list("set-cookie")]
    assert header == []  # Max-Age stays the remaining absolute lifetime (Q-26), not re-sent


# ---------------------------------------------------------------- client IP (v1.2 C-11)
@pytest.mark.parametrize("peer,header_value,cfg,expected", [
    ("198.51.100.7", "203.0.113.1", {}, "198.51.100.7"),  # no CLIENT_IP_HEADER: the peer
    ("198.51.100.7", "203.0.113.1", {"client_ip_header": "cf-connecting-ip",
                                     "trusted_proxy_ips": "127.0.0.1/32"}, "198.51.100.7"),
    ("127.0.0.1", "203.0.113.1", {"client_ip_header": "cf-connecting-ip",
                                  "trusted_proxy_ips": "127.0.0.1/32"}, "203.0.113.1"),
    ("127.0.0.1", "garbage", {"client_ip_header": "cf-connecting-ip",
                              "trusted_proxy_ips": "127.0.0.1"}, "127.0.0.1"),
    ("127.0.0.1", None, {"client_ip_header": "cf-connecting-ip",
                         "trusted_proxy_ips": "127.0.0.1"}, "127.0.0.1"),
    ("10.0.0.2", "6.6.6.6, 203.0.113.9, 10.0.0.1", {
        "client_ip_header": "x-forwarded-for", "trusted_proxy_ips": "10.0.0.0/8"}, "203.0.113.9"),
    ("::1", "2001:db8::5", {"client_ip_header": "x-real-ip", "trusted_proxy_ips": "::1/128"},
     "2001:db8::5"),
])
def test_client_ip_header_only_from_trusted_proxies(tmp_path, peer, header_value, cfg,
                                                    expected) -> None:
    from llmwiki.api.session import client_ip_of

    settings = make_settings(tmp_path, **cfg)
    headers = []
    if header_value is not None:
        headers.append((settings.client_ip_header.lower().encode() or b"x-none",
                        header_value.encode()))
    assert client_ip_of({"client": (peer, 1), "headers": headers}, settings) == expected


def test_client_ip_reaches_the_sso_via_a_trusted_proxy(build, sso) -> None:
    client = build(auth_mode="sso", client_ip_header="cf-connecting-ip",
                   trusted_proxy_ips=f"{BROWSER_IP}/32")
    page = client.get("/login")
    csrf = page.text.split("name='csrf_token' value='")[1].split("'")[0]
    client.post("/login", data={"username": "alice", "password": "alice-pw",
                                "csrf_token": csrf}, headers={"CF-Connecting-IP": "192.0.2.44"})
    assert sso.calls[0][1]["client_ip"] == "192.0.2.44"


def test_client_ip_header_from_an_untrusted_peer_is_ignored(build, sso) -> None:
    client = build(auth_mode="sso", client_ip_header="cf-connecting-ip",
                   trusted_proxy_ips="10.0.0.0/8")
    page = client.get("/login")
    csrf = page.text.split("name='csrf_token' value='")[1].split("'")[0]
    client.post("/login", data={"username": "alice", "password": "alice-pw",
                                "csrf_token": csrf}, headers={"CF-Connecting-IP": "192.0.2.44"})
    assert sso.calls[0][1]["client_ip"] == BROWSER_IP


@pytest.mark.parametrize("over,match", [
    ({"client_ip_header": "cf-connecting-ip"}, "TRUSTED_PROXY_IPS is empty"),
    ({"trusted_proxy_ips": "not-a-cidr"}, "TRUSTED_PROXY_IPS"),
    ({"session_idle_hours": 0}, "SESSION_IDLE_HOURS"),
])
def test_bad_client_ip_settings_fail_fast(build, over, match) -> None:
    with pytest.raises(RuntimeError, match=match):
        build(auth_mode="sso", **over)


# ---------------------------------------------------------------- /mcp in static mode (v1.2 C-12)
def test_static_mode_protect_reads_also_protects_mcp(build, caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="llmwiki.api.app")
    with build(auth_mode="static", sso_protect_reads=True) as client:
        r = mcp(client)
        assert r.status_code == 401
        assert r.json() == {"detail": "invalid or missing bearer token"}  # Phase 0 401 body
        assert "www-authenticate" not in r.headers
        assert mcp(client, bearer("wrong")).status_code == 401
        assert mcp(client, bearer(STATIC)).status_code == 200
    assert "MCP is unauthenticated" not in caplog.text


def test_static_mode_default_mcp_stays_open_and_unwrapped(build) -> None:
    from llmwiki.api.session import MCPAuthMiddleware

    with build(auth_mode="static") as client:
        assert mcp(client).status_code == 200
        mounts = [r for r in client.app.routes if getattr(r, "path", "") == "/mcp"]
        assert mounts and not isinstance(mounts[0].app, MCPAuthMiddleware)
