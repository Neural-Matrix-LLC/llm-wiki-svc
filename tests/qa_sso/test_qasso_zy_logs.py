"""LOG-*: no tokens, passwords or session cookies in llm-wiki logs (design B3 'never log tokens'). Runs late
(zy) so the logs contain the whole run. Instances should run with LOG_LEVEL=DEBUG (run/start_all.sh does)."""
from __future__ import annotations

import re

import pytest

import qasso_data as D

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}")


@pytest.fixture(scope="module")
def logs(cfg):
    if not cfg.log_files:
        pytest.skip("LLM_WIKI_LOG_FILES not set")
    text = ""
    for path in cfg.log_files:
        with open(path, errors="replace") as fh:
            text += fh.read()
    return text


def test_LOG_01_no_jwts(logs):
    found = JWT_RE.findall(logs)
    assert not found, f"{len(found)} JWT(s) in logs, first: {found[0][:40]}..."


def test_LOG_02_no_passwords(logs):
    assert D.PASSWORD not in logs and D.WRONG_PASSWORD not in logs


def test_LOG_03_no_static_token(logs, cfg):
    if not cfg.static_token or len(cfg.static_token) < 8:
        pytest.skip("static token unknown/short")
    # PRE-02 fixed in d8b1e89 (token= redacted in uvicorn's access log): strict again - the harness sends
    # /dashboard?token=<static> many times (matrix static_query_ok, RED-*), none of it may reach any log.
    # the RED-02 probe line (percent-encoded parameter name) is reported by RED-02 itself, not double-counted here
    leaks = [ln for ln in logs.splitlines() if cfg.static_token in ln and "?%74oken=" not in ln]
    assert not leaks, leaks[:3]
    if cfg.dotted_token:
        assert cfg.dotted_token not in logs, "the JWT-shaped static token (D-09 instance) was logged"


def test_LOG_04_no_session_cookie_values(logs):
    assert not re.search(r"llmwiki_session=[A-Za-z0-9+/=_-]{40,}", logs)


def test_LOG_05_no_sso_client_secret(logs):
    """v1.1 item 4: llm-wiki's SSO_CLIENT_SECRET (Basic auth to the SSO) never appears in its logs."""
    import qasso_data as D
    assert D.LOGIN_CLIENT_SECRET not in logs
    import base64
    basic = base64.b64encode(f"{D.LOGIN_CLIENT_ID}:{D.LOGIN_CLIENT_SECRET}".encode()).decode()
    assert basic not in logs


def test_LOG_06_no_csrf_values_or_session_ids(logs):
    """Session ids (43+ char url-safe strings after llmwiki_session=) and X-CSRF-Token values not logged."""
    import re
    assert not re.search(r"X-CSRF-Token['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", logs, re.I)
