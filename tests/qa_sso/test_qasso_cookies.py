"""COOKIE-*: the llmwiki_session cookie after v1.1 item 2 (server-side sessions).

The cookie holds only a random 256-bit session id; tokens live in SQLite (SESSION_DB_PATH). So: no tokens or
user data in the cookie, a small constant size whatever the user's groups/perms, flags per B4, a new id per
login (no fixation), and unknown/garbage ids are simply "no session".
"""
from __future__ import annotations

import base64
import math
import re

import httpx
import pytest

import qasso_data as D
from wiki_client import SESSION_COOKIE, decode_session, set_cookie

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]
SMALL_COOKIE = 200        # a 256-bit id is 43 chars base64url / 64 hex; name + attrs stay far below this


def _login_response(wiki, browser_factory, user="qa_wiki_direct"):
    b = browser_factory(wiki)
    r = b.login(user, D.PASSWORD)
    c = set_cookie(r, SESSION_COOKIE)
    assert c is not None, "login must set llmwiki_session"
    return b, r, c


def _status_with_cookie(wiki, value, path="/usage"):
    with httpx.Client(base_url=wiki.url, timeout=30, follow_redirects=False) as c:
        return c.get(path, headers={"Cookie": f"{SESSION_COOKIE}={value}", "Accept": "application/json"})


def _id_bits(value: str) -> float:
    v = value.strip('"')
    if re.fullmatch(r"[0-9a-fA-F]+", v):
        return len(v) * 4
    if re.fullmatch(r"[A-Za-z0-9_\-]+=*", v):
        return len(v.rstrip("=")) * 6
    return len(v) * math.log2(64)


def test_COOKIE_01_flags(wiki, browser_factory, seeded, cfg):
    _, _, c = _login_response(wiki, browser_factory)
    assert "httponly" in c.attrs, c.raw
    assert ("secure" in c.attrs) is cfg.cookie_secure, c.raw
    assert c.attrs.get("samesite", "").lower() == "lax", c.raw
    assert c.attrs.get("path") == "/", c.raw
    assert "domain" not in c.attrs, f"host-only cookie expected (design: no shared cookie domain): {c.raw}"


def test_COOKIE_02_max_age_is_remaining_absolute_lifetime(wiki, browser_factory, seeded, cfg):
    """v1.2 Q-26: cookie Max-Age = remaining absolute lifetime (SESSION_MAX_AGE_HOURS, default 168 h). At login
    that is the full lifetime (+-120 s)."""
    _, _, c = _login_response(wiki, browser_factory)
    assert "max-age" in c.attrs, f"v1.2 Q-26: Max-Age required: {c.raw}"
    full = cfg.session_max_age_h * 3600
    assert full - 120 <= int(c.attrs["max-age"]) <= full, (c.raw, full)


@pytest.mark.grey
def test_COOKIE_02b_reissued_max_age_shrinks_with_session_age(wiki, browser_factory, seeded, cfg, session_db):
    """Q-26: if the app re-sends the session cookie on a later request of a day-old session, its Max-Age must
    be the REMAINING lifetime (<= 168 h - 24 h), never a fresh full lifetime."""
    from conftest import instance_name_of
    b = browser_factory(wiki)
    b.login("qa_wiki_sess2", D.PASSWORD)
    session_db(instance_name_of(cfg, wiki)).age("qa_wiki_sess2", "created", 24 * 3600)
    r = b.get("/dashboard")
    assert r.status_code == 200
    sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("llmwiki_session=")]
    for h in sc:   # the app may or may not re-send the cookie on ordinary requests; if it does, it must shrink
        m = re.search(r"(?i)max-age=(\d+)", h)
        assert m and int(m.group(1)) <= cfg.session_max_age_h * 3600 - 24 * 3600 + 60, h


def test_COOKIE_03_opaque_random_id_no_tokens(wiki, browser_factory, seeded):
    _, _, c = _login_response(wiki, browser_factory)
    v = c.value.strip('"')
    assert v.count(".") < 2 and "eyJ" not in v, f"cookie must not carry a JWT/signed payload: {v[:60]}..."
    data = decode_session(v)
    assert not data, f"cookie must not decode to session data: {data!r}"
    try:
        raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
        assert b"qa_wiki_direct" not in raw and b"refresh" not in raw
    except Exception:  # noqa: BLE001 - not base64 at all is fine
        pass
    assert "qa_wiki_direct" not in v
    assert _id_bits(v) >= 256, f"v1.1: random 256-bit id; got ~{_id_bits(v):.0f} bits ({len(v)} chars)"


def test_COOKIE_04_unknown_and_tampered_ids_are_no_session(wiki, browser_factory, seeded):
    _, _, c = _login_response(wiki, browser_factory)
    v = c.value.strip('"')
    flipped = v[:-1] + ("A" if v[-1] != "A" else "B")
    for bad in (flipped, "A" * len(v), "", "x" * 5000, "../../etc/passwd", "' OR 1=1 --"):
        r = _status_with_cookie(wiki, bad)
        assert r.status_code == 401, (bad[:20], r.status_code)
    with httpx.Client(base_url=wiki.url, timeout=30, follow_redirects=False) as cl:
        r = cl.get("/dashboard", headers={"Cookie": f"{SESSION_COOKIE}={flipped}"})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_COOKIE_05_ids_unique_per_login(wiki, browser_factory, seeded):
    values = {_login_response(wiki, browser_factory)[2].value for _ in range(3)}
    assert len(values) == 3


def test_COOKIE_06_size_small_and_constant(wiki, browser_factory, seeded):
    """D-05 resolved by v1.1 item 2: the cookie no longer grows with groups/perms (qa_wiki_many: 40 long groups)."""
    sizes = {}
    for user in ("qa_wiki_direct", "qa_wiki_many", "qa_wiki_super"):
        _, _, c = _login_response(wiki, browser_factory, user)
        sizes[user] = (len(c.name) + 1 + len(c.value), len(c.raw))
    print("session cookie sizes (name=value, full Set-Cookie):", sizes)
    assert len({nv for nv, _ in sizes.values()}) == 1, f"cookie size must not depend on the user: {sizes}"
    for user, (nv, raw) in sizes.items():
        assert nv < SMALL_COOKIE and raw < 400, (user, nv, raw)


def test_COOKIE_07_cookie_from_other_instance_rejected(cfg, browser_factory, seeded):
    """Each instance has its own SESSION_DB_PATH: an id from one is unknown to another."""
    insts = [i for i in cfg.instances.values() if i.mode != "static"]
    if len(insts) < 2:
        pytest.skip("needs two sso/both instances")
    a, other = insts[0], insts[1]
    b = browser_factory(a)
    b.login("qa_wiki_direct", D.PASSWORD)
    assert _status_with_cookie(other, b.session_value).status_code == 401


def test_COOKIE_08_session_fixation_new_id_on_login(wiki, browser_factory, seeded):
    """A pre-planted llmwiki_session value (attacker-chosen, or a previous session) is replaced at login."""
    b = browser_factory(wiki)
    planted = "qa-planted-" + "A" * 40
    b.jar[SESSION_COOKIE] = planted
    b.login("qa_wiki_direct", D.PASSWORD)
    assert b.session_value != planted
    assert _status_with_cookie(wiki, planted).status_code == 401
    prev = b.session_value
    r = b.submit_login_while_signed_in("qa_wiki_direct", D.PASSWORD)   # re-login over an existing session
    assert r.status_code == 303, r.status_code
    assert b.session_value != prev, "a new login must issue a new session id"


def test_COOKIE_09_failed_relogin_observed(wiki, browser_factory, seeded):
    """Q-10 (still not in the design; QA judgment): a failed re-login must never issue a new session; whether
    the existing one is kept is recorded (printed) for the decision list. Invariant: if the browser's cookie is
    cleared, the server-side row must be gone too (no orphaned live session the user believes is closed)."""
    from test_qasso_session import _get
    b = browser_factory(wiki)
    b.login("qa_wiki_direct", D.PASSWORD)
    prev = b.session_value
    r = b.submit_login_while_signed_in("qa_wiki_direct", D.WRONG_PASSWORD)
    assert r.status_code != 303, r.status_code
    assert b.session_value in (prev, None), "a failed login must not issue a new session id"
    alive = _get(wiki.url, prev).status_code == 200
    print(f"Q-10 observed: cookie {'kept' if b.session_value else 'cleared'}, old session "
          f"{'alive' if alive else 'dead'}")
    if b.session_value is None:
        assert not alive, "cookie cleared but the server-side session still works"
