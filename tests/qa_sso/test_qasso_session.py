"""SESS-*: server-side sessions (design B4 + v1.1 items 2 and 7).

v1.1 item 2: SQLite at SESSION_DB_PATH (id hash SHA-256, username, sub, access, refresh, access_exp, csrf,
created_at, last_seen); logout and refresh failure delete the row; absolute lifetime SESSION_MAX_AGE_HOURS
(168), no sliding; prune on start and hourly; per-session lock + BEGIN IMMEDIATE so only one refresh runs.
v1.1 item 7: SSO down during refresh keeps the session (continue while the access token is valid, else 503);
a refresh without the permission deletes the session (403 page).

Fast cases use the stub SSO in "mint" mode against the `stubbed` instance (2 uvicorn workers; access_ttl=65 s
so the 60 s refresh window opens after ~5 s; a refresh token works once). Cases marked `grey` read or age the
QA instance's own session DB (session_store.py; writes only under run/state/). Slow cases use the real SSO.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import time

import httpx
import pytest

import qasso_data as D
from wiki_client import SESSION_COOKIE, set_cookie

REFRESH = "/api/v1/auth/refresh"
HOUR = 3600


def _minted_login(stub, stubbed, browser_factory, ttl=65, user="qa_wiki_refresh", **cfg):
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=ttl, username=user, **cfg)
    b = browser_factory(stubbed)
    b.login(user, D.PASSWORD)
    return b


def _get(url, cookie, path="/usage", json_=True):
    h = {"Cookie": f"{SESSION_COOKIE}={cookie}"}
    if json_:
        h["Accept"] = "application/json"
    with httpx.Client(base_url=url, timeout=60, follow_redirects=False) as c:
        return c.get(path, headers=h)


# ================================================================ storage (grey box)
@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_01_row_per_session_with_documented_columns(wiki, logged_in, session_db, cfg):
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess")
    store = session_db(instance_name_of(cfg, wiki))
    _, cols = store.table()
    low = " ".join(c.lower() for c in cols)
    for needle in ("user", "sub", "access", "refresh", "access_exp", "csrf", "created", "last_seen"):
        assert needle in low, f"v1.1 item 2 column {needle!r} missing: {cols}"
    row = store.row_for_cookie(b.session_value)
    assert row is not None, "row keyed by SHA-256 of the cookie id expected"


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_02_raw_session_id_not_stored(wiki, logged_in, session_db, cfg):
    """Only the SHA-256 of the id is stored: a leaked DB/backup does not yield usable cookies."""
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess")
    store = session_db(instance_name_of(cfg, wiki))
    raw = store.raw_bytes()
    assert b.session_value.encode() not in raw, "raw session id found in the session DB"
    assert hashlib.sha256(b.session_value.encode()).hexdigest().encode() in raw or \
        hashlib.sha256(b.session_value.encode()).digest() in raw


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_03_tokens_in_db_not_in_cookie(wiki, logged_in, session_db, cfg):
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess")
    row = session_db(instance_name_of(cfg, wiki)).row_for_cookie(b.session_value)
    store = session_db(instance_name_of(cfg, wiki))
    access, refresh = store.value(row, "access"), store.value(row, "refresh")
    assert str(access).count(".") == 2 and str(refresh).count(".") == 2
    assert access not in b.session_value and refresh not in b.session_value


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_04_session_store_not_world_readable(wiki, logged_in, session_db, cfg):
    """v1.2 D-16: session DB file mode 0600 (dir 0700 if created by the app). The harness keeps the default
    umask (022) so a wrong mode would show. -wal/-shm hold the same tokens and are checked too."""
    from conftest import instance_name_of
    logged_in("qa_wiki_sess")
    modes = session_db(instance_name_of(cfg, wiki)).file_modes()
    print("session store modes:", {k: oct(v) for k, v in modes.items()})
    files = {k: oct(v) for k, v in modes.items() if not k.endswith("/") and v & 0o077}
    assert not files, f"session store files accessible to group/others (D-16 wants 0600): {files}"
    db = [k for k in modes if not k.endswith("/") and "-" not in k.rsplit(".", 1)[-1]]
    assert db and modes[db[0]] == 0o600, {k: oct(v) for k, v in modes.items()}


# ================================================================ refresh (stub, fast)
@pytest.mark.needs_stub
def test_SESS_05_refresh_inside_window_server_side(stub, stubbed, browser_factory, session_db):
    b = _minted_login(stub, stubbed, browser_factory)
    store = session_db("stubbed")
    before = store.row_for_cookie(b.session_value)
    time.sleep(6)
    r = b.get("/dashboard")
    assert r.status_code == 200
    assert stub.count(REFRESH) == 1
    c = set_cookie(r, SESSION_COOKIE)
    assert c is None or c.value == b.session_value or not c.deleted, "the session id does not need to change"
    after = store.row_for_cookie(b.session_value)
    if before and after:
        assert store.value(after, "access") != store.value(before, "access"), "rotated tokens stored server-side"
        assert store.value(after, "refresh") != store.value(before, "refresh")


@pytest.mark.needs_stub
def test_SESS_06_no_refresh_outside_window(stub, stubbed, browser_factory):
    b = _minted_login(stub, stubbed, browser_factory, ttl=600)
    for _ in range(3):
        assert b.get("/dashboard").status_code == 200
    assert stub.count(REFRESH) == 0


@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["token_401", "token_403"])
def test_SESS_07_refresh_rejected_deletes_session(stub, stubbed, browser_factory, session_db, mode):
    """B4 + v1.1 item 2: refresh failure (SSO says the token/account is bad) deletes the row; HTML 303 /login,
    JSON 401; the old cookie is dead afterwards."""
    b = _minted_login(stub, stubbed, browser_factory)
    cookie = b.session_value
    stub.config(refresh=mode)
    time.sleep(6)
    r = b.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), r.headers.get("location")
    c = set_cookie(r, SESSION_COOKIE)
    assert c is not None and c.deleted, "session cookie must be cleared"
    assert session_db("stubbed").row_for_cookie(cookie) is None, "row must be deleted"
    stub.config(refresh="mint")
    assert _get(stubbed.url, cookie).status_code == 401


@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["token_401", "token_403"])
def test_SESS_07b_refresh_rejected_json_401(stub, stubbed, browser_factory, mode):
    b = _minted_login(stub, stubbed, browser_factory)
    stub.config(refresh=mode)
    time.sleep(6)
    r = b.get("/usage", headers={"Accept": "application/json"})
    assert r.status_code == 401


@pytest.mark.needs_stub
@pytest.mark.parametrize("n", [8, 20])
def test_SESS_08_parallel_refresh_single_call_no_logouts(stub, stubbed, browser_factory, n):
    """D-04 / v1.1 item 2: N parallel requests (spread over 2 uvicorn workers) inside the refresh window ->
    exactly one SSO refresh, every request 200, nobody logged out. The stub's refresh tokens work once."""
    b = _minted_login(stub, stubbed, browser_factory)
    cookie = b.session_value
    time.sleep(6)
    with cf.ThreadPoolExecutor(n) as ex:
        codes = list(ex.map(lambda _: _get(stubbed.url, cookie).status_code, range(n)))
    assert codes == [200] * n, f"statuses {codes}; SSO refresh calls {stub.count(REFRESH)}"
    assert stub.count(REFRESH) == 1, f"one refresh expected, got {stub.count(REFRESH)}"
    assert _get(stubbed.url, cookie).status_code == 200, "session still alive after the burst"


@pytest.mark.needs_stub
def test_SESS_08b_parallel_refresh_with_expired_access(stub, stubbed, browser_factory):
    """Same race when the access token has already expired (all requests MUST wait for the one refresh)."""
    b = _minted_login(stub, stubbed, browser_factory, ttl=2)
    cookie = b.session_value
    stub.config(access_ttl=900)   # the REFRESHED token is long-lived, so one refresh satisfies every waiter
    time.sleep(4)
    with cf.ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(lambda _: _get(stubbed.url, cookie).status_code, range(10)))
    assert codes == [200] * 10, codes
    assert stub.count(REFRESH) == 1


@pytest.mark.needs_stub
def test_SESS_09_refresh_without_perm_deletes_session_403(stub, stubbed, browser_factory, session_db):
    """v1.1 item 7 (Q-06): the refreshed token no longer carries the permission -> session deleted, 403."""
    b = _minted_login(stub, stubbed, browser_factory)
    cookie = b.session_value
    stub.config(perms=[])
    time.sleep(6)
    r = b.get("/usage", headers={"Accept": "application/json"})
    assert r.status_code == 403 and r.json() == {"detail": "forbidden", "error": "permission_denied"}
    assert session_db("stubbed").row_for_cookie(cookie) is None, "session must be deleted (Q-06)"
    stub.config(perms=[D.APP_PERM])
    assert _get(stubbed.url, cookie).status_code == 401
    assert _get(stubbed.url, cookie, "/dashboard", json_=False).status_code == 303


@pytest.mark.needs_stub
def test_SESS_09b_refresh_without_perm_html_403_page(stub, stubbed, browser_factory):
    b = _minted_login(stub, stubbed, browser_factory)
    stub.config(perms=[])
    time.sleep(6)
    r = b.get("/dashboard")
    assert r.status_code == 403 and "text/html" in r.headers.get("content-type", "")


@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["server_error", "close", "garbage", "hang"])
def test_SESS_10_sso_down_access_still_valid_continues(stub, stubbed, browser_factory, session_db, mode):
    """v1.1 item 7 (Q-05): SSO unreachable/5xx during refresh, access token still valid -> continue (200),
    keep the session; the next refresh after recovery succeeds."""
    b = _minted_login(stub, stubbed, browser_factory)
    cookie = b.session_value
    stub.config(refresh=mode, hang_seconds=15)
    time.sleep(6)
    r = b.get("/usage", headers={"Accept": "application/json"})
    assert r.status_code == 200, r.status_code
    assert session_db("stubbed").row_for_cookie(cookie) is not None, "session must be kept (Q-05)"
    stub.config(refresh="mint")
    assert _get(stubbed.url, cookie).status_code == 200


@pytest.mark.slow
@pytest.mark.needs_stub
@pytest.mark.parametrize("mode", ["server_error", "close"])
def test_SESS_11_sso_down_access_expired_503_keeps_session(stub, stubbed, browser_factory, session_db, mode):
    """v1.1 item 7 (Q-05): access token no longer valid (past exp + 30 s leeway) and the SSO is down -> 503
    "Login service unavailable" (HTML page / JSON), session NOT cleared; works again once the SSO is back."""
    b = _minted_login(stub, stubbed, browser_factory, ttl=2)
    cookie = b.session_value
    stub.config(refresh=mode)
    time.sleep(34)
    rj = _get(stubbed.url, cookie)
    assert rj.status_code == 503 and "json" in rj.headers.get("content-type", ""), rj.status_code
    rh = _get(stubbed.url, cookie, "/dashboard", json_=False)
    assert rh.status_code == 503 and "login service unavailable" in rh.text.lower()
    assert all(not (c := set_cookie(x, SESSION_COOKIE)) or not c.deleted for x in (rj, rh)), "cookie must stay"
    assert session_db("stubbed").row_for_cookie(cookie) is not None
    stub.config(refresh="mint")
    assert _get(stubbed.url, cookie).status_code == 200


@pytest.mark.needs_stub
def test_SESS_12_expired_access_refreshes_transparently(stub, stubbed, browser_factory):
    b = _minted_login(stub, stubbed, browser_factory, ttl=3)
    time.sleep(5)
    assert b.get("/dashboard").status_code == 200
    assert stub.count(REFRESH) >= 1


@pytest.mark.needs_stub
def test_SESS_13_replayed_cookie_after_logout_elsewhere(stub, stubbed, browser_factory):
    """Two browsers, one session id (copied cookie): one logs out -> the other is out too (row deleted)."""
    b = _minted_login(stub, stubbed, browser_factory, ttl=600)
    cookie = b.session_value
    b.logout()
    assert _get(stubbed.url, cookie).status_code == 401


# ================================================================ lifetime (grey box: age the QA row)
@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_14_absolute_lifetime_enforced(wiki, logged_in, session_db, cfg):
    """v1.1 item 2: SESSION_MAX_AGE_HOURS absolute; a session older than that is dead even though its refresh
    token would still work."""
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess")
    store = session_db(instance_name_of(cfg, wiki))
    assert store.age("qa_wiki_sess", "created", cfg.session_max_age_h * HOUR + 120) >= 1
    assert _get(wiki.url, b.session_value).status_code == 401
    assert _get(wiki.url, b.session_value, "/dashboard", json_=False).status_code == 303


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_15_just_inside_absolute_lifetime_ok(wiki, logged_in, session_db, cfg):
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess2")
    session_db(instance_name_of(cfg, wiki)).age("qa_wiki_sess2", "created", cfg.session_max_age_h * HOUR - 600)
    assert _get(wiki.url, b.session_value).status_code == 200


@pytest.mark.grey
@pytest.mark.needs_stub
def test_SESS_16_no_sliding_refresh_keeps_created_at(stub, stubbed, browser_factory, session_db):
    """'No sliding beyond it': a refresh rotates tokens but created_at (the absolute anchor) stays."""
    b = _minted_login(stub, stubbed, browser_factory)
    store = session_db("stubbed")
    created = store.value(store.row_for_cookie(b.session_value), "created")
    time.sleep(6)
    assert b.get("/dashboard").status_code == 200 and stub.count(REFRESH) == 1
    assert store.value(store.row_for_cookie(b.session_value), "created") == created


@pytest.mark.grey
@pytest.mark.needs_wiki
@pytest.mark.parametrize("idle,alive", [("under", True), ("over", False)])
def test_SESS_17_idle_timeout(wiki, logged_in, session_db, cfg, idle, alive):
    """v1.2 Q-27: SESSION_IDLE_HOURS (default 12): unused that long -> session ends (row deleted, 401 JSON)."""
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess2")
    store = session_db(instance_name_of(cfg, wiki))
    secs = cfg.session_idle_h * HOUR - 600 if idle == "under" else cfg.session_idle_h * HOUR + 120
    store.age("qa_wiki_sess2", "last_seen", secs)
    r = _get(wiki.url, b.session_value)
    if alive:
        assert r.status_code == 200, r.status_code
    else:
        assert r.status_code == 401, r.status_code
        assert store.row_for_cookie(b.session_value) is None, "idle-expired row must be deleted"


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_18_last_seen_updated(wiki, logged_in, session_db, cfg):
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess2")
    store = session_db(instance_name_of(cfg, wiki))
    store.age("qa_wiki_sess2", "last_seen", 3600)
    old = store.value(store.row_for_cookie(b.session_value), "last_seen")
    assert _get(wiki.url, b.session_value).status_code == 200
    assert store.value(store.row_for_cookie(b.session_value), "last_seen") != old


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_22_last_seen_written_at_most_once_a_minute(wiki, logged_in, session_db, cfg):
    """Developer's choice (beyond the design): last_seen is written only when >= 60 s old. Verify both sides of
    the threshold, and that created_at (absolute expiry) never moves."""
    from conftest import instance_name_of
    b = logged_in("qa_wiki_sess2")
    store = session_db(instance_name_of(cfg, wiki))
    created0 = store.value(store.row_for_cookie(b.session_value), "created")
    store.age("qa_wiki_sess2", "last_seen", 30)
    aged = store.value(store.row_for_cookie(b.session_value), "last_seen")
    for _ in range(3):
        assert _get(wiki.url, b.session_value).status_code == 200
    assert store.value(store.row_for_cookie(b.session_value), "last_seen") == aged, "written although < 60 s old"
    store.age("qa_wiki_sess2", "last_seen", 31)          # now 61 s old
    assert _get(wiki.url, b.session_value).status_code == 200
    now_seen = store.value(store.row_for_cookie(b.session_value), "last_seen")
    assert abs(now_seen - time.time()) <= 5, (now_seen, time.time())
    assert store.value(store.row_for_cookie(b.session_value), "created") == created0, "created_at must not move"


@pytest.mark.grey
@pytest.mark.needs_wiki
def test_SESS_23_throttled_last_seen_does_not_delay_logout_or_absolute_expiry(wiki, browser_factory, seeded,
                                                                               session_db, cfg):
    """A session used continuously (last_seen fresh) still dies at the absolute lifetime, and logout right after
    a request whose last_seen write was skipped still kills a copied cookie at once."""
    from conftest import instance_name_of
    store = session_db(instance_name_of(cfg, wiki))
    b = browser_factory(wiki)
    b.login("qa_wiki_sess2", D.PASSWORD)
    stolen = b.session_value
    assert _get(wiki.url, stolen).status_code == 200      # last_seen < 60 s old: no write
    b.logout()
    assert _get(wiki.url, stolen).status_code == 401, "logout must not depend on last_seen"
    b2 = browser_factory(wiki)
    b2.login("qa_wiki_sess2", D.PASSWORD)
    store.age("qa_wiki_sess2", "created", cfg.session_max_age_h * HOUR + 5)   # last_seen stays fresh
    assert _get(wiki.url, b2.session_value).status_code == 401, "absolute lifetime must win over fresh last_seen"


# ================================================================ restart and prune (restarts the `sso` instance by PID)
@pytest.mark.grey
@pytest.mark.restart
@pytest.mark.needs_wiki
def test_SESS_19_sessions_survive_restart(cfg, browser_factory, seeded, restart):
    """v1.1 item 2: sessions live in the SQLite file (prune 'on start' implies they persist) -> a restart of
    the process keeps users signed in."""
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    b = browser_factory(inst)
    b.login("qa_wiki_sess", D.PASSWORD)
    assert _get(inst.url, b.session_value).status_code == 200
    restart("sso")
    assert _get(inst.url, b.session_value).status_code == 200


@pytest.mark.grey
@pytest.mark.restart
@pytest.mark.needs_wiki
def test_SESS_20_expired_rows_pruned_on_start(cfg, browser_factory, seeded, restart, session_db):
    inst = cfg.instances.get("sso") or pytest.skip("LLM_WIKI_SSO_URL not set")
    b = browser_factory(inst)
    b.login("qa_wiki_sess2", D.PASSWORD)
    store = session_db("sso")
    store.age("qa_wiki_sess2", "created", cfg.session_max_age_h * HOUR + 3600)
    assert store.row_for_cookie(b.session_value) is not None
    restart("sso")
    assert store.row_for_cookie(b.session_value) is None, "expired row must be pruned on start"


def test_SESS_21_hourly_prune():
    pytest.skip("manual: hourly prune needs an hour of wall time (or a debug hook); verified by SESS-20 on start "
                "plus code review of the scheduler")


# ================================================================ real SSO, slow
@pytest.mark.slow
@pytest.mark.grey
@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_SESS_30_real_refresh_rotation(wiki, logged_in, cfg, sso_http, session_db):
    from conftest import instance_name_of
    if cfg.access_ttl > 180:
        pytest.skip("needs SSO_ACCESS_TTL_SECONDS <= 180 (run with QA_SSO_ACCESS_TTL_MIN=2)")
    b = logged_in("qa_wiki_refresh")
    store = session_db(instance_name_of(cfg, wiki))
    old_refresh = store.value(store.row_for_cookie(b.session_value), "refresh")
    time.sleep(max(cfg.access_ttl - 55, 1))
    assert b.get("/dashboard").status_code == 200
    new_refresh = store.value(store.row_for_cookie(b.session_value), "refresh")
    assert new_refresh != old_refresh
    assert sso_http.post(REFRESH, json={"refresh_token": old_refresh}).status_code == 401, "old refresh blacklisted"


INTROSPECT_CACHE_S = 30      # sso-client: active answers cached <= 30 s (SSO design v1.2 §4.4)


def _until_not_200(fn, limit=INTROSPECT_CACHE_S + 8, step=1.0):
    """Poll fn() until it stops returning 200; return (response, seconds). Fails after ``limit`` s."""
    t0 = time.monotonic()
    while True:
        r = fn()
        if r.status_code != 200:
            return r, time.monotonic() - t0
        assert time.monotonic() - t0 < limit, f"still 200 after {limit}s: introspect cache bound not honoured"
        time.sleep(step)


@pytest.mark.grey
@pytest.mark.needs_wiki
@pytest.mark.needs_sso
def test_SESS_31_logout_all_at_sso_ends_session_within_cache_bound(wiki, logged_in, cfg, sso_http, session_db):
    """v1.2b (accepted change, was D-10 'until the next refresh'): the SSO's introspect answer is authoritative, so
    an SSO logout-all ends the llm-wiki session within the 30 s introspect cache; the row is then deleted."""
    from conftest import instance_name_of
    b = logged_in("qa_wiki_logoutall")
    store = session_db(instance_name_of(cfg, wiki))
    assert b.get("/dashboard").status_code == 200        # warms the 30 s cache
    access = store.value(store.row_for_cookie(b.session_value), "access")
    r = sso_http.post("/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 204, r.text
    cookie = b.session_value
    r, secs = _until_not_200(lambda: b.get("/dashboard"))
    assert r.status_code == 303 and r.headers["location"].startswith("/login"), r.status_code
    assert secs <= INTROSPECT_CACHE_S + 3, secs
    assert store.row_for_cookie(cookie) is None


@pytest.mark.needs_wiki
@pytest.mark.needs_manage
def test_SESS_32_revoked_grant_takes_effect_within_cache_bound(wiki, logged_in, cfg, sso_admin, reset_user):
    """v1.2b (accepted change): a revoked grant takes effect within the 30 s introspect cache (not at the next
    refresh); then 403 and the session is deleted (Q-06)."""
    reset_user("qa_wiki_mut_revoke")
    b = logged_in("qa_wiki_mut_revoke")
    assert b.get("/usage", headers={"Accept": "application/json"}).status_code == 200
    sso_admin.call("revoke", "qa_wiki_mut_revoke", D.APP_PERM)
    cookie = b.session_value
    r, secs = _until_not_200(lambda: b.get("/usage", headers={"Accept": "application/json"}))
    assert r.status_code == 403, r.status_code
    assert secs <= INTROSPECT_CACHE_S + 3, secs
    assert _get(wiki.url, cookie).status_code == 401, "session deleted after the 403 (Q-06)"
