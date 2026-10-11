"""llm-wiki-svc against the REAL SSO server (docs/sso-integration-design.md B7, integration).

Starts SSO-server (branch feat/app-permissions) under gunicorn on SQLite and llm-wiki-svc under
uvicorn (offline backends, AUTH_MODE=sso) on dedicated free ports, seeds users with and without
``application.app1`` (direct and via a group) and llm-wiki's ServiceClient, then drives the browser
flow, the Bearer flow and /mcp over real HTTP. uvicorn runs with --no-proxy-headers and the wiki
with CLIENT_IP_HEADER=x-forwarded-for, TRUSTED_PROXY_IPS=127.0.0.1/32 (design v1.2 C-11), so each
test "browser" gets its own X-Forwarded-For IP and the SSO's per-IP throttle (v1.1 item 4) can be
observed per end user. Local only: no cloud keys, nothing costs money.

    SSO_SERVER_DIR=../sso-server pytest -m integration tests/integration/test_sso_live.py

``SSO_SERVER_DIR`` defaults to ``../sso-server``; ``SSO_SERVER_PYTHON`` defaults to the first of
its ``.venv-py3.13.16-dj52`` / ``.venv`` interpreters that exists (needs requirements-dev.txt:
gunicorn). Skipped when neither is found.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
PERM = "application.app1"
PASSWORD = "Wiki-Pass-" + "9" * 8
CLIENT_ID = "llm-wiki"
CLIENT_SECRET = "wiki-svc-" + secrets.token_urlsafe(24)
PER_IP_LIMIT = 8  # SSO_THROTTLE_LOGIN for this run (per-user stays 5/min)
_ips = iter(f"198.51.100.{n}" for n in range(1, 255))

SEED = r"""
from django.contrib.auth.models import Group, Permission, User
p = Permission.objects.get(content_type__app_label="application", codename="app1")
g, _ = Group.objects.get_or_create(name="wiki-users")
g.permissions.add(p)
def mk(name, **kw):
    u = User.objects.create_user(name, f"{name}@example.test", PASSWORD, **kw)
    return u
mk("wiki_direct").user_permissions.add(p)
mk("wiki_group").groups.add(g)
mk("wiki_none")
mk("wiki_inactive", is_active=False).user_permissions.add(p)
mk("wiki_super", is_superuser=True, is_staff=True)
mk("wiki_revoke").user_permissions.add(p)
mk("wiki_throttle").user_permissions.add(p)
mk("wiki_race").user_permissions.add(p)
from sso.models import ServiceClient
c = ServiceClient(client_id=CLIENT_ID, name="llm-wiki-svc")
c.set_secret(CLIENT_SECRET)
c.save()
print("seeded")
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{url}: process exited with {proc.returncode}")
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up in {timeout} s")


def _sso_python(sso_dir: Path) -> Path | None:
    explicit = os.environ.get("SSO_SERVER_PYTHON")
    if explicit:
        return Path(explicit)
    for name in (".venv-py3.13.16-dj52", ".venv"):
        candidate = sso_dir / name / "bin" / "python"
        if candidate.exists() and (candidate.parent / "gunicorn").exists():
            return candidate
    return None


@pytest.fixture(scope="module")
def sso(tmp_path_factory):
    sso_dir = Path(os.environ.get("SSO_SERVER_DIR", REPO.parent / "sso-server")).resolve()
    python = _sso_python(sso_dir)
    if not (sso_dir / "manage.py").exists() or python is None:
        pytest.skip(f"no SSO-server checkout with a venv at {sso_dir}")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    tmp = tmp_path_factory.mktemp("sso")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp / "private.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    (tmp / "public.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    (tmp / "private.pem").chmod(0o600)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        "PATH": os.environ["PATH"], "HOME": str(tmp), "SSO_ENV_FILE": "",
        "DJANGO_SETTINGS_MODULE": "ssoproject.settings",
        "SSO_SECRET_KEY": secrets.token_urlsafe(50), "SSO_DEBUG": "False",
        "SSO_ALLOWED_HOSTS": "127.0.0.1,localhost", "SSO_ISSUER": base,
        "SSO_DB_ENGINE": "sqlite", "SSO_SQLITE_PATH": str(tmp / "sso.sqlite3"),
        "SSO_JWT_PRIVATE_KEY_PATH": str(tmp / "private.pem"),
        "SSO_JWT_PUBLIC_KEY_PATHS": str(tmp / "public.pem"),
        "SSO_CORS_ALLOWED_ORIGINS": "http://127.0.0.1:1", "SSO_SSL_REDIRECT": "False",
        "SSO_COOKIE_SECURE": "False", "SSO_CACHE_URL": f"filecache://{tmp}/cache",
        # 1 min access tokens: every session request is inside the 60 s refresh margin, so the
        # live run exercises refresh + rotation. A low per-IP limit: each test browser has its
        # own IP (delegated client_ip); per-user stays 5/min.
        "SSO_ACCESS_TTL_MIN": "1", "SSO_THROTTLE_LOGIN": f"{PER_IP_LIMIT}/min",
    }
    run = {"cwd": sso_dir, "env": env, "check": True, "capture_output": True, "text": True}
    subprocess.run([str(python), "manage.py", "migrate", "--noinput", "-v", "0"], **run)
    subprocess.run([str(python), "manage.py", "shell", "-c",
                    f"PASSWORD={PASSWORD!r}\nCLIENT_ID={CLIENT_ID!r}\n"
                    f"CLIENT_SECRET={CLIENT_SECRET!r}\n" + SEED], **run)
    log = open(tmp / "gunicorn.log", "w")  # noqa: SIM115
    proc = subprocess.Popen([str(python.parent / "gunicorn"), "ssoproject.wsgi:application",
                             "-b", f"127.0.0.1:{port}", "--workers", "2"],
                            cwd=sso_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        _wait(f"{base}/healthz", proc)
        yield {"base": base, "python": python, "dir": sso_dir, "env": env,
               "log": tmp / "gunicorn.log"}
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()


def manage(sso: dict, code: str) -> None:
    subprocess.run([str(sso["python"]), "manage.py", "shell", "-c", code], cwd=sso["dir"],
                   env=sso["env"], check=True, capture_output=True)


def _start_wiki(sso: dict, tmp: Path, **over: str):  # type: ignore[no-untyped-def]
    port = _free_port()
    env = {
        "PATH": os.environ["PATH"], "HOME": str(tmp),
        "STORAGE_BACKEND": "local", "VECTOR_BACKEND": "memory", "EMBEDDING_BACKEND": "fake",
        "LLM_PROVIDER": "fake", "LOCAL_STORAGE_PATH": str(tmp / "data"), "EMBEDDING_DIM": "64",
        "WORKER_MODE": "inline", "LEXICAL_BACKEND": "none", "RERANKER_BACKEND": "none",
        "LLMWIKI_PROVIDERS_CONFIG": str(tmp / "none.py"),
        "LLMWIKI_OPS_CONFIG": str(tmp / "none.py"),
        "AGENT_SKILLS_DIR": str(tmp / "noskills"),
        "INGEST_API_TOKEN": "static-" + secrets.token_urlsafe(24),
        "AUTH_MODE": "sso", "SSO_BASE_URL": sso["base"], "SSO_ISSUER": sso["base"],
        "SSO_CLIENT_ID": CLIENT_ID, "SSO_CLIENT_SECRET": CLIENT_SECRET,
        "SESSION_COOKIE_SECURE": "false",
        "CLIENT_IP_HEADER": "x-forwarded-for", "TRUSTED_PROXY_IPS": "127.0.0.1/32",
        **over,
    }
    log = open(tmp / "uvicorn.log", "w")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "llmwiki.api.app:app",
                             "--host", "127.0.0.1", "--port", str(port),
                             "--no-proxy-headers"],
                            cwd=tmp, env=env, stdout=log, stderr=subprocess.STDOUT)
    return proc, log, port, env


@pytest.fixture(scope="module")
def wiki(sso, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wiki")
    proc, log, port, env = _start_wiki(sso, tmp)
    try:
        _wait(f"http://127.0.0.1:{port}/healthz", proc)
        yield {"base": f"http://127.0.0.1:{port}", "log": tmp / "uvicorn.log",
               "static": env["INGEST_API_TOKEN"], "db": tmp / "data" / "sessions.sqlite3"}
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()


def browser(wiki: dict, ip: str | None = None) -> httpx.Client:
    """A browser with its own IP (X-Forwarded-For; uvicorn trusts it from 127.0.0.1)."""
    return httpx.Client(base_url=wiki["base"], follow_redirects=False, timeout=15,
                        headers={"X-Forwarded-For": ip or next(_ips)})


def session_row(wiki: dict, cookie: str) -> dict | None:
    import hashlib
    import sqlite3

    conn = sqlite3.connect(wiki["db"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?",
                           (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def csrf_meta(page: httpx.Response) -> str:
    return re.search(r'<meta name="csrf-token" content="([^"]+)">', page.text).group(1)


def form_login(client: httpx.Client, username: str, password: str = PASSWORD) -> httpx.Response:
    page = client.get("/login?next=/dashboard")
    csrf = re.search(r"name='csrf_token' value='([^']+)'", page.text).group(1)
    return client.post("/login", data={"username": username, "password": password,
                                       "csrf_token": csrf, "next": "/dashboard"})


def sso_tokens(sso: dict, username: str) -> dict:
    r = httpx.post(f"{sso['base']}/api/v1/auth/login",
                   json={"username": username, "password": PASSWORD, "client": "native"})
    assert r.status_code == 200, r.text
    return r.json()


def test_healthz_reports_sso_mode(wiki, sso) -> None:
    auth = httpx.get(f"{wiki['base']}/healthz").json()["config"]["auth"]
    assert auth["mode"] == "sso" and auth["sso_issuer"] == sso["base"] and auth["mcp_protected"]


@pytest.mark.parametrize("user", ["wiki_direct", "wiki_group", "wiki_super"])
def test_browser_login_with_permission(wiki, user) -> None:
    with browser(wiki) as c:
        r = form_login(c, user)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        page = c.get("/dashboard")
        assert page.status_code == 200 and f"Signed in as <strong>{user}</strong>" in page.text
        assert c.post("/lint").status_code == 403  # cookie write without X-CSRF-Token
        assert c.post("/lint", headers={"X-CSRF-Token": csrf_meta(page)}).status_code == 200


def test_login_by_email(wiki) -> None:
    with browser(wiki) as c:
        assert form_login(c, "wiki_direct@example.test").status_code == 303


@pytest.mark.parametrize("user,password,status,text", [
    ("wiki_none", PASSWORD, 403, "have access to llm-wiki (application.app1)"),
    ("wiki_inactive", PASSWORD, 403, "Please activate your account"),
    ("wiki_direct", "wrong-password", 401, "Wrong username or password"),
    ("nobody", PASSWORD, 401, "Wrong username or password"),
])
def test_browser_login_refused(wiki, user, password, status, text) -> None:
    with browser(wiki) as c:
        r = form_login(c, user, password)
        assert r.status_code == status and text in r.text
        assert "llmwiki_session" not in c.cookies
        assert c.get("/dashboard").status_code == 303


def test_per_ip_throttle_applies_to_the_browser_not_to_the_wiki_server(wiki, sso) -> None:
    """v1.1 item 4: Basic auth + client_ip -> the SSO's per-IP limit counts each browser."""
    ip_a, ip_b = next(_ips), next(_ips)
    with browser(wiki, ip_a) as c:
        statuses = [form_login(c, f"ghost{n}", "wrong").status_code
                    for n in range(PER_IP_LIMIT + 1)]
    assert statuses[:PER_IP_LIMIT] == [401] * PER_IP_LIMIT and statuses[-1] == 429, statuses
    with browser(wiki, ip_b) as c:  # another browser behind the same wiki server: not blocked
        assert form_login(c, "wiki_direct").status_code == 303
    log = Path(sso["log"]).read_text()
    assert f'"ip": "{ip_a}"' in log and f'"ip": "{ip_b}"' in log  # the SSO logged the real IPs


def test_client_ip_without_basic_auth_is_ignored_by_the_real_sso(sso) -> None:
    r = httpx.post(f"{sso['base']}/api/v1/auth/login",
                   json={"username": "ghost-direct", "password": "x", "client": "native",
                         "client_ip": "192.0.2.77"})
    assert r.status_code == 401
    log = Path(sso["log"]).read_text()
    assert '"ip": "192.0.2.77"' not in log
    assert re.search(r'"ip": "127\.0\.0\.1".*"username": "ghost-direct"', log)


def test_rate_limited_by_the_real_sso(wiki) -> None:
    with browser(wiki) as c:  # per-user limit (5/min), whatever the IP
        statuses = [form_login(c, "wiki_throttle", "wrong").status_code for _ in range(6)]
    assert 429 in statuses, statuses
    with browser(wiki) as c:
        r = form_login(c, "wiki_throttle", "wrong")
        assert r.status_code == 429 and "Too many attempts, try again in" in r.text
        assert r.headers["retry-after"].isdigit()


def test_bearer_tokens_from_the_real_sso(wiki, sso) -> None:
    ok = sso_tokens(sso, "wiki_group")["access_token"]
    none = sso_tokens(sso, "wiki_none")["access_token"]
    assert httpx.post(f"{wiki['base']}/lint", headers={"Authorization": f"Bearer {ok}"}
                      ).status_code == 200
    r = httpx.post(f"{wiki['base']}/lint", headers={"Authorization": f"Bearer {none}"})
    assert r.status_code == 403
    assert r.json() == {"detail": "forbidden", "error": "permission_denied"}
    r = httpx.post(f"{wiki['base']}/lint", headers={"Authorization": f"Bearer {wiki['static']}"})
    assert r.status_code == 401  # AUTH_MODE=sso: the static token is not accepted


def test_session_refresh_rotates_at_the_real_sso(wiki, sso) -> None:
    with browser(wiki) as c:
        assert form_login(c, "wiki_direct").status_code == 303
        cookie = c.cookies["llmwiki_session"]
        before = session_row(wiki, cookie)
        assert before is not None and before["refresh"] and cookie not in str(before)
        r = c.get("/dashboard")  # 1-minute access token: refreshed now
        assert r.status_code == 200
        assert c.cookies["llmwiki_session"] == cookie  # the opaque id does not change
        after = session_row(wiki, cookie)
        assert after["refresh"] != before["refresh"] and after["created_at"] == before["created_at"]
        assert c.get("/dashboard").status_code == 200
    # the rotated-away refresh token is blacklisted at the SSO
    r = httpx.post(f"{sso['base']}/api/v1/auth/refresh", json={"refresh_token": before["refresh"]})
    assert r.status_code == 401


def test_concurrent_requests_do_not_double_refresh(wiki) -> None:
    """Five parallel requests on one session with a due access token: if two of them used the same
    refresh token, the SSO's reuse detection would refuse one and end the session."""
    from concurrent.futures import ThreadPoolExecutor

    with browser(wiki) as c:
        assert form_login(c, "wiki_race").status_code == 303
        cookie = c.cookies["llmwiki_session"]
        ip = c.headers["X-Forwarded-For"]

    def hit(_: int) -> int:
        with httpx.Client(base_url=wiki["base"], timeout=30,
                          headers={"X-Forwarded-For": ip},
                          cookies={"llmwiki_session": cookie}) as one:
            return one.get("/usage").status_code

    with ThreadPoolExecutor(5) as pool:
        statuses = list(pool.map(hit, range(5)))
    assert statuses == [200] * 5, statuses
    assert hit(0) == 200 and session_row(wiki, cookie) is not None


def test_mcp_requires_an_sso_bearer(wiki, sso) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    accept = {"Accept": "application/json, text/event-stream"}
    url = f"{wiki['base']}/mcp/"
    assert httpx.post(url, json=body, headers=accept).status_code == 401
    assert httpx.post(url, json=body, headers={
        **accept, "Authorization": f"Bearer {wiki['static']}"}).status_code == 401
    none = sso_tokens(sso, "wiki_none")["access_token"]
    assert httpx.post(url, json=body, headers={
        **accept, "Authorization": f"Bearer {none}"}).status_code == 403
    ok = sso_tokens(sso, "wiki_group")["access_token"]
    assert httpx.post(url, json=body, headers={
        **accept, "Authorization": f"Bearer {ok}"}).status_code == 200


def test_revoked_permission_takes_effect_on_refresh(wiki, sso) -> None:
    with browser(wiki) as c:
        assert form_login(c, "wiki_revoke").status_code == 303
        manage(sso, "from django.contrib.auth.models import User, Permission\n"
                    "u=User.objects.get(username='wiki_revoke')\n"
                    "u.user_permissions.remove(Permission.objects.get("
                    "content_type__app_label='application', codename='app1'))")
        r = c.get("/dashboard")
        assert r.status_code == 403 and "have access to llm-wiki" in r.text
        assert c.get("/dashboard").status_code == 303  # session cleared


def test_logout_blacklists_the_refresh_token_at_the_sso(wiki, sso) -> None:
    with browser(wiki) as c:
        assert form_login(c, "wiki_super").status_code == 303
        page = c.get("/dashboard")
        csrf = re.search(r"name='csrf_token' value='([^']+)'", page.text).group(1)
        cookie = c.cookies["llmwiki_session"]
        refresh_token = session_row(wiki, cookie)["refresh"]
        r = c.post("/logout", data={"csrf_token": csrf})
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert session_row(wiki, cookie) is None  # row deleted server-side
        assert c.get("/dashboard").status_code == 303
    r = httpx.post(f"{sso['base']}/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 401  # blacklisted by the SSO


def test_wrong_service_secret_is_a_503_misconfigured(sso, tmp_path) -> None:
    """v1.2 C-19: the real SSO answers 401 invalid_client; the wiki logs an error and shows 503."""
    proc, log, port, _ = _start_wiki(sso, tmp_path, SSO_CLIENT_SECRET="wrong-" + "x" * 30)
    try:
        _wait(f"http://127.0.0.1:{port}/healthz", proc)
        with browser({"base": f"http://127.0.0.1:{port}"}) as c:
            r = form_login(c, "wiki_direct")
        assert r.status_code == 503 and "Login service misconfigured" in r.text
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()
    text = (tmp_path / "uvicorn.log").read_text()
    assert "ERROR" in text and "invalid_client" in text


def test_real_sso_bad_client_ip_with_valid_credentials_is_400(sso) -> None:
    r = httpx.post(f"{sso['base']}/api/v1/auth/login", auth=(CLIENT_ID, CLIENT_SECRET),
                   json={"username": "wiki_direct", "password": PASSWORD, "client": "native",
                         "client_ip": "not-an-ip"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    r = httpx.post(f"{sso['base']}/api/v1/auth/login", auth=(CLIENT_ID, "wrong"),
                   json={"username": "wiki_direct", "password": PASSWORD, "client": "native"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_session_db_is_0600(wiki) -> None:
    assert Path(wiki["db"]).stat().st_mode & 0o777 == 0o600


def test_csrf_failure_body(wiki) -> None:
    with browser(wiki) as c:
        assert form_login(c, "wiki_group").status_code == 303
        r = c.post("/lint")
        assert r.status_code == 403 and r.json() == {"error": "csrf_failed"}


def test_no_tokens_in_the_service_log(wiki) -> None:
    text = Path(wiki["log"]).read_text()
    assert "login ok: wiki_direct" in text  # the service does log logins
    assert not re.search(r"eyJ[\w-]{20,}\.[\w-]{20,}\.[\w-]{20,}", text)
    assert PASSWORD not in text
