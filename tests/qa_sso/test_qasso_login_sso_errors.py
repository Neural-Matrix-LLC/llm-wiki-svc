"""LERR-*: mapping SSO failures to user-facing messages (design B4), driven by the stub SSO."""
from __future__ import annotations

import time

import pytest

import qasso_data as D
from wiki_client import SESSION_COOKIE

pytestmark = [pytest.mark.needs_stub]


def _attempt(stubbed, browser_factory, **kw):
    b = browser_factory(stubbed)
    t0 = time.monotonic()
    r = b.submit_login(kw.get("user", "qa_wiki_direct"), D.PASSWORD)
    return b, r, time.monotonic() - t0


def _no_session(b, r):
    assert r.status_code != 303 and b.session_value is None, (r.status_code, r.headers.get("location"))
    assert "Traceback" not in r.text and "eyJ" not in r.text


@pytest.mark.parametrize("mode,message", [
    ("invalid_credentials", "Wrong username or password"),
    ("account_inactive", "Please activate your account"),
])
def test_LERR_01_mapped_messages(stub, stubbed, browser_factory, mode, message):
    stub.config(login=mode)
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert message in r.text
    assert "Invalid username or password." not in r.text, "raw SSO detail should be replaced by the mapped text"


def test_LERR_02_rate_limited_shows_retry_after(stub, stubbed, browser_factory):
    stub.config(login="rate_limited", retry_after=42)
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert "Too many attempts" in r.text and "42" in r.text


@pytest.mark.parametrize("mode", ["close", "server_error", "garbage"])
def test_LERR_03_unavailable_is_503(stub, stubbed, browser_factory, mode):
    """B4 + v1.2 C-14: SSO unreachable -> 'Login service unavailable', 503 everywhere, login included.
    5xx/garbage treated alike (Q-09)."""
    stub.config(login=mode)
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert r.status_code == 503, r.status_code
    assert "Login service unavailable" in r.text


@pytest.mark.slow
def test_LERR_04_timeout_10s(stub, stubbed, browser_factory):
    stub.config(login="hang", hang_seconds=25)
    b, r, elapsed = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert r.status_code == 503 and "Login service unavailable" in r.text   # v1.2 C-14
    assert 9 <= elapsed <= 14, f"B4 10 s timeout, took {elapsed:.1f} s"


def test_LERR_05_unexpected_sso_error_is_generic(stub, stubbed, browser_factory):
    stub.config(login="bad_request")
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert "stub: bad request" not in r.text, "raw SSO detail must not leak to the page (Q-09)"


def test_LERR_06_forged_login_response_rejected(stub, stubbed, browser_factory):
    """B4: llm-wiki verifies the access token the SSO returned. Tokens signed by another key -> no session."""
    stub.config(login="forged")
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)


def test_LERR_07_messages_do_not_reveal_account_existence(stub, stubbed, browser_factory):
    stub.config(login="invalid_credentials")
    _, r1, _ = _attempt(stubbed, browser_factory, user="qa_wiki_direct")
    _, r2, _ = _attempt(stubbed, browser_factory, user="nobody_qa_xyz")
    assert r1.status_code == r2.status_code and ("Wrong username or password" in r1.text) == ("Wrong username or password" in r2.text)


def test_LERR_08_minted_success_creates_session(stub, stubbed, browser_factory):
    """Control case for the stub itself: a correctly signed response does create a session."""
    stub.config(login="mint", refresh="mint", logout="mint", access_ttl=900)
    b, r, _ = _attempt(stubbed, browser_factory)
    assert r.status_code == 303 and SESSION_COOKIE in b.jar


def test_LERR_09_invalid_client_is_misconfigured_503(stub, stubbed, browser_factory, cfg):
    """v1.2 C-19: the SSO refuses our ServiceClient (401 invalid_client) -> no silent fallback: llm-wiki logs an
    ERROR and shows 'Login service misconfigured' with 503. Not the 'wrong password' text (that would blame the
    user for an operator problem)."""
    stub.config(login="invalid_client")
    b, r, _ = _attempt(stubbed, browser_factory)
    _no_session(b, r)
    assert r.status_code == 503, r.status_code
    assert "Login service misconfigured" in r.text
    assert "Wrong username or password" not in r.text
    calls = stub.calls()
    assert calls["counts"].get("/api/v1/auth/login") == 1, "must not retry without ServiceClient auth"
    assert calls["auth"]["/api/v1/auth/login"]["scheme"] == "basic"
    logs = "".join(open(f, errors="replace").read() for f in cfg.log_files if "wiki-stubbed" in f)
    if not cfg.log_files:
        pytest.skip("LLM_WIKI_LOG_FILES not set: cannot check the ERROR log line")
    err = [ln for ln in logs.splitlines() if "ERROR" in ln and ("invalid_client" in ln or "misconfigured" in ln.lower())]
    assert err, "C-19: an ERROR log line naming invalid_client/misconfiguration is required"
