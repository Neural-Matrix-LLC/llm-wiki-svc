"""llm-wiki-svc against the REAL SSO server (docs/sso-integration-design.md B7, integration).

Starts SSO-server (branch feat/app-permissions) under gunicorn on SQLite and llm-wiki-svc under
uvicorn (offline backends, AUTH_MODE=sso) on dedicated free ports, seeds users with and without
``application.app1`` (direct and via a group), then drives the browser flow and the Bearer flow over
real HTTP. Local only: no cloud keys, nothing costs money.

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
        # live run exercises refresh + rotation. Generous per-IP limit; per-user stays 5/min.
        "SSO_ACCESS_TTL_MIN": "1", "SSO_THROTTLE_LOGIN": "1000/min",
    }
    run = {"cwd": sso_dir, "env": env, "check": True, "capture_output": True, "text": True}
    subprocess.run([str(python), "manage.py", "migrate", "--noinput", "-v", "0"], **run)
    subprocess.run([str(python), "manage.py", "shell", "-c",
                    f"PASSWORD={PASSWORD!r}\n" + SEED], **run)
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


@pytest.fixture(scope="module")
def wiki(sso, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wiki")
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
        "SESSION_SECRET": secrets.token_urlsafe(48), "SESSION_COOKIE_SECURE": "false",
    }
    log = open(tmp / "uvicorn.log", "w")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "llmwiki.api.app:app",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=tmp, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        _wait(f"http://127.0.0.1:{port}/healthz", proc)
        yield {"base": f"http://127.0.0.1:{port}", "log": tmp / "uvicorn.log",
               "static": env["INGEST_API_TOKEN"], "secret": env["SESSION_SECRET"]}
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()


def browser(wiki: dict) -> httpx.Client:
    return httpx.Client(base_url=wiki["base"], follow_redirects=False, timeout=15)


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
    assert auth["mode"] == "sso" and auth["sso_issuer"] == sso["base"]


@pytest.mark.parametrize("user", ["wiki_direct", "wiki_group", "wiki_super"])
def test_browser_login_with_permission(wiki, user) -> None:
    with browser(wiki) as c:
        r = form_login(c, user)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard"
        page = c.get("/dashboard")
        assert page.status_code == 200 and f"Signed in as <strong>{user}</strong>" in page.text
        assert c.post("/lint").status_code == 200  # the session works on JSON routes too


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


def test_rate_limited_by_the_real_sso(wiki) -> None:
    with browser(wiki) as c:
        statuses = [form_login(c, "wiki_throttle", "wrong").status_code for _ in range(7)]
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
        first = c.cookies["llmwiki_session"]
        r = c.get("/dashboard")  # 1-minute access token: refreshed now
        assert r.status_code == 200
        assert c.cookies["llmwiki_session"] != first
        assert c.get("/dashboard").status_code == 200


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
        from itsdangerous import URLSafeTimedSerializer

        data = URLSafeTimedSerializer(wiki["secret"], salt="llmwiki-session-v1").loads(
            c.cookies["llmwiki_session"])
        refresh_token = data["refresh_token"]
        r = c.post("/logout", data={"csrf_token": csrf})
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert c.get("/dashboard").status_code == 303
    r = httpx.post(f"{sso['base']}/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 401  # blacklisted by the SSO


def test_no_tokens_in_the_service_log(wiki) -> None:
    text = Path(wiki["log"]).read_text()
    assert "login ok: wiki_direct" in text  # the service does log logins
    assert not re.search(r"eyJ[\w-]{20,}\.[\w-]{20,}\.[\w-]{20,}", text)
    assert PASSWORD not in text
