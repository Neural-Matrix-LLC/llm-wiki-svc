"""START-*: fail fast at startup (design B1 + v1.1 items 1/4/6/9). Starts throw-away llm-wiki processes on port 8039 and stops them
by PID. Needs LLM_WIKI_START_CMD, e.g. 'uv run uvicorn llmwiki.api.app:app --host 127.0.0.1 --port {port}'."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time

import httpx
import pytest

PORT = int(os.environ.get("QA_SSO_STARTUP_PORT", "8039"))
# v1.1: no SESSION_SECRET (item 9); SSO_CLIENT_ID/SECRET required in sso/both (item 4); static token >= 24 chars
# and not "changeme" in sso/both (item 6). SESSION_DB_PATH under the per-test tmp dir (item 2).
STRONG_TOKEN = "qa-startup-static-token-0123456789"      # 34 chars
SSO_ONLY = ("SSO_ISSUER", "SSO_BASE_URL", "SSO_CLIENT_ID", "SSO_CLIENT_SECRET")
BASE_ENV = {"AUTH_MODE": "sso", "SSO_BASE_URL": "http://127.0.0.1:8030", "SSO_ISSUER": "http://127.0.0.1:8030",
            "SSO_CLIENT_ID": "qa-wiki-login", "SSO_CLIENT_SECRET": "qa-wiki-login-secret-0123456789abcdef0123",
            "SESSION_COOKIE_SECURE": "false",
            "STORAGE_BACKEND": "local", "VECTOR_BACKEND": "memory", "EMBEDDING_BACKEND": "fake", "LLM_BACKEND": "fake",
            "LEXICAL_BACKEND": "memory", "RERANKER_BACKEND": "none", "INGEST_API_TOKEN": STRONG_TOKEN}


def _start(cfg, tmp_path, env_over, drop=()):
    if not cfg.start_cmd:
        pytest.skip("LLM_WIKI_START_CMD not set")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SSO_", "AUTH_MODE", "SESSION_"))}
    env.pop("INGEST_API_TOKEN", None)
    env.update(BASE_ENV, LOCAL_STORAGE_PATH=str(tmp_path / "data"), SESSION_DB_PATH=str(tmp_path / "sessions.sqlite3"))
    env.update(env_over)
    for k in drop:
        env.pop(k, None)
    # cwd = an empty dir so the repo's real .env is not picked up (pydantic-settings env_file=".env")
    cmd = cfg.start_cmd.format(port=PORT)
    return subprocess.Popen(shlex.split(cmd), cwd=str(tmp_path), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)


def _outcome(p, timeout=30):
    """-> ('exited', rc, output) or ('serving', None, '')."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.poll() is not None:
            return "exited", p.returncode, p.stdout.read()
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=1).status_code == 200:
                return "serving", None, ""
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return "timeout", None, ""


def _stop(p) -> str:
    """Stop the process group we created (never by name or port) and return its remaining output."""
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)       # the process group we created; never by name or port
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(5)
    try:
        return p.stdout.read() or ""
    except (ValueError, OSError):
        return ""


@pytest.mark.parametrize("mode", ["sso", "both"])
@pytest.mark.parametrize("missing", list(SSO_ONLY))
def test_START_01_missing_required_setting_refuses(cfg, tmp_path, mode, missing):
    p = _start(cfg, tmp_path, {"AUTH_MODE": mode}, drop=(missing,))
    try:
        state, rc, out = _outcome(p)
        assert state == "exited" and rc != 0, f"started without {missing} in AUTH_MODE={mode}"
        assert missing in out or missing.lower() in out.lower(), out[-1500:]
    finally:
        _stop(p)


@pytest.mark.parametrize("value", ["SSO ", "none", "oidc", "true"])
def test_START_02_invalid_auth_mode_refuses(cfg, tmp_path, value):
    p = _start(cfg, tmp_path, {"AUTH_MODE": value})
    try:
        state, rc, _ = _outcome(p)
        assert state == "exited" and rc != 0
    finally:
        _stop(p)


def test_START_03_static_needs_no_sso_settings(cfg, tmp_path):
    p = _start(cfg, tmp_path, {"AUTH_MODE": "static"}, drop=SSO_ONLY)
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        _stop(p)


def test_START_04_default_mode_is_static(cfg, tmp_path):
    p = _start(cfg, tmp_path, {}, drop=("AUTH_MODE",) + SSO_ONLY)
    try:
        assert _outcome(p)[0] == "serving"
        r = httpx.get(f"http://127.0.0.1:{PORT}/dashboard", timeout=5)
        assert r.status_code == 401, "default AUTH_MODE must be static (401, not a /login redirect)"
    finally:
        _stop(p)


WEAK = {"changeme": "changeme", "23_chars": "x" * 23, "unset_default": None}


@pytest.mark.parametrize("mode", ["sso", "both"])
@pytest.mark.parametrize("weak", list(WEAK))
def test_START_05_weak_static_token_refused_in_sso_both(cfg, tmp_path, mode, weak):
    """v1.1 item 6 (Q-16): refuse to start if INGEST_API_TOKEN is changeme or shorter than 24 chars.
    unset_default = INGEST_API_TOKEN not set at all (the code default is 'changeme')."""
    over = {"AUTH_MODE": mode}
    drop = ()
    if WEAK[weak] is None:
        drop = ("INGEST_API_TOKEN",)
    else:
        over["INGEST_API_TOKEN"] = WEAK[weak]
    p = _start(cfg, tmp_path, over, drop=drop)
    try:
        state, rc, out = _outcome(p)
        assert state == "exited" and rc != 0, f"AUTH_MODE={mode} started with a weak static token ({weak})"
        assert "INGEST_API_TOKEN" in out, out[-1500:]
        if weak == "23_chars":
            assert WEAK[weak] not in out, "the error must not echo the token value"
    finally:
        _stop(p)


@pytest.mark.parametrize("mode", ["sso", "both"])
def test_START_05b_24_char_token_accepted(cfg, tmp_path, mode):
    p = _start(cfg, tmp_path, {"AUTH_MODE": mode, "INGEST_API_TOKEN": "y" * 24})
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        _stop(p)


@pytest.mark.parametrize("weak", ["changeme", "23_chars"])
def test_START_05c_static_mode_weak_token_only_warns(cfg, tmp_path, weak):
    p = _start(cfg, tmp_path, {"AUTH_MODE": "static", "INGEST_API_TOKEN": WEAK[weak]}, drop=SSO_ONLY)
    try:
        assert _outcome(p)[0] == "serving", "static mode must still start (v1.1 item 6: only warn)"
    finally:
        out = _stop(p)
    assert "WARNING" in out.upper() and "INGEST_API_TOKEN" in out, out[-1500:]


def test_START_07_static_mode_mcp_warning(cfg, tmp_path):
    """v1.1 item 1: static mode logs 'MCP is unauthenticated in AUTH_MODE=static' at startup."""
    p = _start(cfg, tmp_path, {"AUTH_MODE": "static"}, drop=SSO_ONLY)
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        out = _stop(p)
    assert "MCP is unauthenticated in AUTH_MODE=static" in out, out[-1500:]


@pytest.mark.parametrize("mode", ["sso", "both"])
def test_START_08_no_mcp_warning_in_sso_both(cfg, tmp_path, mode):
    p = _start(cfg, tmp_path, {"AUTH_MODE": mode})
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        out = _stop(p)
    assert "MCP is unauthenticated" not in out


@pytest.mark.parametrize("mode", ["sso", "both"])
def test_START_09_session_secret_not_needed(cfg, tmp_path, mode):
    """v1.1 item 9: SESSION_SECRET dropped. Starting without it works (BASE_ENV has none); with a stray one set
    it must not break startup either."""
    p = _start(cfg, tmp_path, {"AUTH_MODE": mode, "SESSION_SECRET": "stray-leftover-0123456789abcdef"})
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        _stop(p)


@pytest.mark.parametrize("mode", ["sso", "both"])
def test_START_10_session_db_created_at_configured_path(cfg, tmp_path, mode):
    """v1.1 item 2: SESSION_DB_PATH is honoured (file created at startup or at the first login)."""
    p = _start(cfg, tmp_path, {"AUTH_MODE": mode})
    try:
        assert _outcome(p)[0] == "serving"
        db = tmp_path / "sessions.sqlite3"
        assert db.exists(), "session DB is created (and pruned) at startup (v1.1 item 2 'prune at start')"
    finally:
        _stop(p)


def test_START_11_session_db_default_path(cfg, tmp_path):
    """Q-32 (not answered by v1.2; QA judgment): the implementation's default is {LOCAL_STORAGE_PATH}/
    sessions.sqlite3 (config.py effective_session_db_path). Tested for STORAGE_BACKEND=local; what a deployment
    with a non-local storage backend should use stays on the decision list."""
    p = _start(cfg, tmp_path, {"AUTH_MODE": "sso"}, drop=("SESSION_DB_PATH",))
    try:
        assert _outcome(p)[0] == "serving"
        assert (tmp_path / "data" / "sessions.sqlite3").exists(), list((tmp_path / "data").glob("*"))
    finally:
        _stop(p)


def test_START_06_sso_unreachable_at_startup_does_not_block(cfg, tmp_path):
    """JWKS is fetched lazily (cache 1 h); the app must start while the SSO is down."""
    p = _start(cfg, tmp_path, {"SSO_BASE_URL": "http://127.0.0.1:8038", "SSO_ISSUER": "http://127.0.0.1:8038"})
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        _stop(p)


# ================================================================ v1.2 C-11/Q-25: CLIENT_IP_HEADER + TRUSTED_PROXY_IPS
@pytest.mark.parametrize("mode", ["sso", "both"])
@pytest.mark.parametrize("case", ["header_without_trusted", "bad_cidr"])
def test_START_12_client_ip_header_config_validated(cfg, tmp_path, mode, case):
    """Developer's choice: CLIENT_IP_HEADER without TRUSTED_PROXY_IPS refuses to start (the header could never be
    honoured); an unparsable TRUSTED_PROXY_IPS refuses too."""
    over = {"AUTH_MODE": mode, "CLIENT_IP_HEADER": "x-forwarded-for"}
    if case == "bad_cidr":
        over["TRUSTED_PROXY_IPS"] = "127.0.0.1/33,not-an-ip"
    p = _start(cfg, tmp_path, over, drop=("TRUSTED_PROXY_IPS",) if case == "header_without_trusted" else ())
    try:
        state, rc, out = _outcome(p)
        assert state == "exited" and rc != 0, f"{case}: started anyway"
        assert "TRUSTED_PROXY_IPS" in out, out[-1500:]
    finally:
        _stop(p)


def test_START_12b_static_mode_ignores_client_ip_header(cfg, tmp_path):
    """static mode has no SSO calls, so CLIENT_IP_HEADER alone must not stop it (no regression for static)."""
    p = _start(cfg, tmp_path, {"AUTH_MODE": "static", "CLIENT_IP_HEADER": "x-forwarded-for"}, drop=SSO_ONLY)
    try:
        assert _outcome(p)[0] == "serving"
    finally:
        _stop(p)


TRUSTED_PEER, UNTRUSTED_PEER = "127.0.0.70", "127.0.0.71"


@pytest.mark.needs_stub
def test_START_14_forwarded_header_only_from_trusted_peer(cfg, tmp_path, stub, browser_factory):
    """v1.2 C-11/Q-25: CLIENT_IP_HEADER is honoured only when the TCP peer is in TRUSTED_PROXY_IPS. A throw-away
    instance (sso mode, SSO_BASE_URL -> stub) with CLIENT_IP_HEADER=x-forwarded-for, TRUSTED_PROXY_IPS=127.0.0.70/32;
    the stub records the client_ip llm-wiki sends on /login."""
    stub.config(login="invalid_credentials")
    p = _start(cfg, tmp_path, {"AUTH_MODE": "sso", "SSO_BASE_URL": cfg.stub_url,
                               "CLIENT_IP_HEADER": "x-forwarded-for", "TRUSTED_PROXY_IPS": f"{TRUSTED_PEER}/32"})
    try:
        assert _outcome(p)[0] == "serving"
        url = f"http://127.0.0.1:{PORT}"
        cases = [  # (peer, X-Forwarded-For or None, expected client_ip seen by the SSO)
            (TRUSTED_PEER, "198.51.100.7", "198.51.100.7"),
            (TRUSTED_PEER, f"203.0.113.9, 198.51.100.8, {TRUSTED_PEER}", "198.51.100.8"),   # right-to-left
            (TRUSTED_PEER, None, TRUSTED_PEER),
            (TRUSTED_PEER, "not-an-ip", TRUSTED_PEER),
            (UNTRUSTED_PEER, "198.51.100.9", UNTRUSTED_PEER),                               # spoof ignored
            (UNTRUSTED_PEER, None, UNTRUSTED_PEER),
        ]
        seen = []
        for peer, xff, want in cases:
            b = browser_factory(url, source_ip=peer)
            r = b.submit_login("qa_wiki_direct", "wrong", headers={"X-Forwarded-For": xff} if xff else None)
            assert "Wrong username or password" in r.text, (r.status_code, r.text[:300])
            got = (stub.calls()["last"].get("/api/v1/auth/login") or {}).get("client_ip")
            seen.append((peer, xff, want, got))
        assert all(w == g for _, _, w, g in seen), seen
    finally:
        _stop(p)


# ================================================================ v1.2 D-16: session DB file mode
@pytest.mark.parametrize("mode_bits", [0o644, 0o640, 0o604])
def test_START_13_refuses_group_or_world_readable_session_db(cfg, tmp_path, mode_bits):
    db = tmp_path / "sessions.sqlite3"
    db.touch()
    os.chmod(db, mode_bits)
    p = _start(cfg, tmp_path, {"AUTH_MODE": "sso"})
    try:
        state, rc, out = _outcome(p)
        assert state == "exited" and rc != 0, f"started with session DB mode {oct(mode_bits)}"
        assert "sessions.sqlite3" in out and ("0600" in out or "chmod 600" in out), out[-1500:]
    finally:
        _stop(p)


def test_START_13b_creates_db_0600_and_dir_0700(cfg, tmp_path):
    """D-16: created with 0600, its directory 0700 if the app creates it (umask 022 in this process tree)."""
    import stat
    target = tmp_path / "newdir" / "sub" / "sessions.sqlite3"
    p = _start(cfg, tmp_path, {"AUTH_MODE": "sso", "SESSION_DB_PATH": str(target)})
    try:
        assert _outcome(p)[0] == "serving"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600, oct(target.stat().st_mode)
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700, oct(target.parent.stat().st_mode)
        for side in ("-wal", "-shm"):
            f = target.with_name(target.name + side)
            if f.exists():
                assert stat.S_IMODE(f.stat().st_mode) & 0o077 == 0, (side, oct(f.stat().st_mode))
    finally:
        _stop(p)
