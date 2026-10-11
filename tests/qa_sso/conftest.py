"""Black-box QA suite: llm-wiki-svc SSO login + the SSO `perms` claim (design docs/sso-integration-design.md).

Everything talks HTTP to running servers (started by tests/qa_sso/run/start_all.sh on dedicated ports) or
to the SSO's manage.py. Nothing imports llmwiki. Without SSO_BASE_URL / LLM_WIKI_BASE_URL the whole
directory is skipped, so a plain `pytest` of the repo is unaffected.

Environment (see README.md):
  SSO_BASE_URL, SSO_ISSUER (default = SSO_BASE_URL), SSO_AUDIENCE (default myfin-services)
  SSO_MANAGE_CMD, SSO_REPO_DIR       manage.py command + cwd used for seeding/grants (R27 guard applies)
  SSO_TEST_PRIVATE_KEY_PATH          the QA SSO's signing key (forging tokens with valid signatures)
  SSO_ACCESS_LOG                     SSO gunicorn access log (counts JWKS fetches / logout calls)
  SSO_ACCESS_TTL_SECONDS             access-token lifetime of the QA SSO (default 900)
  LLM_WIKI_BASE_URL + LLM_WIKI_AUTH_MODE (+ LLM_WIKI_PROTECT_READS)   the primary llm-wiki instance
  LLM_WIKI_STATIC_URL / _SSO_URL / _BOTH_URL / _SSO_READS_URL / _BOTH_READS_URL   extra instances (matrix)
  LLM_WIKI_STUBBED_URL + STUB_SSO_URL  an sso-mode instance whose SSO_BASE_URL is the stub (tests/qa_sso/stub_sso.py)
  LLM_WIKI_STATIC_TOKEN              INGEST_API_TOKEN of the instances
  LLM_WIKI_STATIC_READS_URL          AUTH_MODE=static + SSO_PROTECT_READS=true (v1.1 item 5)
  LLM_WIKI_DOTTED_URL / _DOTTED_TOKEN  AUTH_MODE=both whose INGEST_API_TOKEN looks like a JWT (v1.1 item 6)
  LLM_WIKI_SESSION_DBS               name=path,... SESSION_DB_PATH of each instance (server-side sessions)
  LLM_WIKI_SESSION_MAX_AGE_HOURS     SESSION_MAX_AGE_HOURS of the instances (default 168)
  QA_SSO_RESTART_CMD                 command template restarting one instance by PID, e.g. 'run/restart.sh {name}'
  SSO_ALLAPPS_URL                    a 2nd SSO on the same DB with the opt-in SSO_SUPERUSER_ALL_APPS=true
  SSO_SERVER_LOG                     SSO stderr log (JSON login events with "ip")
  LLM_WIKI_COOKIE_SECURE             expected Secure flag of the instances (default true)
  LLM_WIKI_LOG_FILES                 comma list of llm-wiki log files (no-secrets-in-logs)
  LLM_WIKI_START_CMD                 shell command that starts llm-wiki (fail-fast startup cases)
  LLM_WIKI_REPO_DIR                  llm-wiki-svc checkout (static/git cases; default: this repo)
  QA_SSO_THROTTLE_PHASE=1            the SSO runs at spec throttle rates (test_qasso_zz_throttle.py)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _bool(name, default):
    v = _env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Instance:
    name: str
    url: str
    mode: str                 # static | sso | both
    protect_reads: bool = False


@dataclass
class Cfg:
    sso_url: str | None = field(default_factory=lambda: (_env("SSO_BASE_URL") or "").rstrip("/") or None)
    sso_issuer: str | None = field(default_factory=lambda: _env("SSO_ISSUER", (_env("SSO_BASE_URL") or "").rstrip("/") or None))
    sso_audience: str = field(default_factory=lambda: _env("SSO_AUDIENCE", "myfin-services"))
    manage_cmd: str | None = field(default_factory=lambda: _env("SSO_MANAGE_CMD"))
    sso_repo: str | None = field(default_factory=lambda: _env("SSO_REPO_DIR"))
    private_key_path: str | None = field(default_factory=lambda: _env("SSO_TEST_PRIVATE_KEY_PATH"))
    sso_access_log: str | None = field(default_factory=lambda: _env("SSO_ACCESS_LOG"))
    access_ttl: int = field(default_factory=lambda: int(_env("SSO_ACCESS_TTL_SECONDS", "900")))
    static_token: str | None = field(default_factory=lambda: _env("LLM_WIKI_STATIC_TOKEN"))
    sso_allapps_url: str | None = field(default_factory=lambda: (_env("SSO_ALLAPPS_URL") or "").rstrip("/") or None)
    sso_server_log: str | None = field(default_factory=lambda: _env("SSO_SERVER_LOG"))
    session_dbs: dict = field(default_factory=lambda: dict(
        kv.split("=", 1) for kv in (_env("LLM_WIKI_SESSION_DBS") or "").split(",") if "=" in kv))
    session_max_age_h: float = field(default_factory=lambda: float(_env("LLM_WIKI_SESSION_MAX_AGE_HOURS", "168")))
    session_idle_h: float = field(default_factory=lambda: float(_env("LLM_WIKI_SESSION_IDLE_HOURS", "12")))   # v1.2 Q-27
    restart_cmd: str | None = field(default_factory=lambda: _env("QA_SSO_RESTART_CMD"))
    dotted_url: str | None = field(default_factory=lambda: (_env("LLM_WIKI_DOTTED_URL") or "").rstrip("/") or None)
    dotted_token: str | None = field(default_factory=lambda: _env("LLM_WIKI_DOTTED_TOKEN"))
    cookie_secure: bool = field(default_factory=lambda: _bool("LLM_WIKI_COOKIE_SECURE", True))
    log_files: list = field(default_factory=lambda: [p for p in (_env("LLM_WIKI_LOG_FILES") or "").split(",") if p])
    start_cmd: str | None = field(default_factory=lambda: _env("LLM_WIKI_START_CMD"))
    wiki_repo: str = field(default_factory=lambda: _env("LLM_WIKI_REPO_DIR", str(HERE.parents[1])))
    stub_url: str | None = field(default_factory=lambda: (_env("STUB_SSO_URL") or "").rstrip("/") or None)
    stubbed_url: str | None = field(default_factory=lambda: (_env("LLM_WIKI_STUBBED_URL") or "").rstrip("/") or None)
    throttle_phase: bool = field(default_factory=lambda: _bool("QA_SSO_THROTTLE_PHASE", False))
    instances: dict = field(default_factory=dict)

    def __post_init__(self):
        seen = set()
        primary = _env("LLM_WIKI_BASE_URL")
        if primary:
            mode = _env("LLM_WIKI_AUTH_MODE", "sso")
            reads = _bool("LLM_WIKI_PROTECT_READS", False)
            self.instances[f"{mode}{'_reads' if reads else ''}"] = Instance(
                f"{mode}{'_reads' if reads else ''}", primary.rstrip("/"), mode, reads)
            seen.add(primary.rstrip("/"))
        for name, var, mode, reads in [("static", "LLM_WIKI_STATIC_URL", "static", False),
                                       ("static_reads", "LLM_WIKI_STATIC_READS_URL", "static", True),
                                       ("sso", "LLM_WIKI_SSO_URL", "sso", False),
                                       ("both", "LLM_WIKI_BOTH_URL", "both", False),
                                       ("sso_reads", "LLM_WIKI_SSO_READS_URL", "sso", True),
                                       ("both_reads", "LLM_WIKI_BOTH_READS_URL", "both", True)]:
            url = (_env(var) or "").rstrip("/")
            if url and url not in seen:
                self.instances.setdefault(name, Instance(name, url, mode, reads))
                seen.add(url)

    @property
    def any_wiki(self) -> bool:
        return bool(self.instances)


CFG = Cfg()

# Opt-in: a plain `pytest` of the repo (testpaths = tests) must not collect, import or run this suite (it needs
# servers, PyJWT, and its static checks are QA gates, not unit tests). Enable explicitly with QA_SSO=1
# (run/pytest.sh does). Base URLs alone do not enable it: stale SSO_* variables in a shell must not send the
# suite to somebody else's server.
QA_ENABLED = _bool("QA_SSO", False)
if not QA_ENABLED:
    collect_ignore_glob = ["test_qasso_*.py"]


def _port_8000_guard():
    from urllib.parse import urlsplit
    urls = [CFG.sso_url, CFG.stub_url, CFG.stubbed_url, CFG.sso_allapps_url, CFG.dotted_url] + \
        [i.url for i in CFG.instances.values()]
    bad = [u for u in urls if u and (urlsplit(u).port == 8000)]
    if bad and not _bool("QA_SSO_ALLOW_PORT_8000", False):
        pytest.exit(f"refusing to run against port 8000 ({bad}); use the dedicated QA ports (README)", returncode=4)

ALL_INSTANCE_NAMES = ["static", "static_reads", "sso", "both", "sso_reads", "both_reads"]


def pytest_configure(config):
    for m in ("qa_sso: llm-wiki SSO login black-box QA (tests/qa_sso)",
              "needs_wiki: needs a running llm-wiki-svc instance",
              "needs_sso: needs a running SSO server",
              "needs_manage: needs SSO_MANAGE_CMD (seed / grant / revoke)",
              "needs_stub: needs the stub SSO + an llm-wiki instance pointed at it",
              "needs_key: needs SSO_TEST_PRIVATE_KEY_PATH",
              "slow: waits for token expiry or rate-limit windows (> 30 s)",
              "throttle: SSO at spec throttle rates; run alone (QA_SSO_THROTTLE_PHASE=1)",
              "static_check: reads repo files / git only, no server",
              "grey: reads/ages the QA instance's own session DB (SESSION_DB_PATH under run/state)",
              "restart: restarts an llm-wiki QA instance by PID (QA_SSO_RESTART_CMD)"):
        config.addinivalue_line("markers", m)


def pytest_collection_modifyitems(config, items):
    here = str(HERE)
    if QA_ENABLED and any(str(i.fspath).startswith(here) for i in items):
        _port_8000_guard()
    for item in items:
        if not str(item.fspath).startswith(here):
            continue
        item.add_marker(pytest.mark.qa_sso)
        if item.get_closest_marker("static_check"):
            continue
        if item.get_closest_marker("throttle") and not CFG.throttle_phase:
            item.add_marker(pytest.mark.skip(reason="throttle phase only (QA_SSO_THROTTLE_PHASE=1, SSO at spec rates)"))
        elif not item.get_closest_marker("throttle") and CFG.throttle_phase:
            item.add_marker(pytest.mark.skip(reason="functional phase only (QA_SSO_THROTTLE_PHASE is set)"))
        if not (CFG.sso_url or CFG.any_wiki):
            item.add_marker(pytest.mark.skip(reason="black-box QA: set SSO_BASE_URL / LLM_WIKI_BASE_URL "
                                                    "(tests/qa_sso/README.md)"))


# ------------------------------------------------------------------------------------------------ basics
@pytest.fixture(scope="session")
def cfg() -> Cfg:
    return CFG


@pytest.fixture(scope="session")
def sso_url(cfg) -> str:
    if not cfg.sso_url:
        pytest.skip("SSO_BASE_URL not set")
    return cfg.sso_url


@pytest.fixture(scope="session")
def sso_http(sso_url):
    with httpx.Client(base_url=sso_url, timeout=30, follow_redirects=False) as c:
        yield c


def instance_or_skip(cfg: Cfg, name: str) -> Instance:
    inst = cfg.instances.get(name)
    if not inst:
        pytest.skip(f"llm-wiki instance {name!r} not configured (LLM_WIKI_{name.upper()}_URL)")
    return inst


@pytest.fixture(scope="session")
def wiki(cfg) -> Instance:
    """The instance that runs SSO login flows: sso, else both, else the primary."""
    for name in ("sso", "both", "sso_reads", "both_reads"):
        if name in cfg.instances:
            return cfg.instances[name]
    pytest.skip("no llm-wiki instance with AUTH_MODE sso/both configured")


@pytest.fixture(scope="session")
def static_token(cfg) -> str:
    if not cfg.static_token:
        pytest.skip("LLM_WIKI_STATIC_TOKEN not set")
    return cfg.static_token


# ------------------------------------------------------------------------------------------------ SSO side
@pytest.fixture(scope="session")
def sso_admin(cfg):
    if not cfg.manage_cmd:
        pytest.skip("SSO_MANAGE_CMD not set")
    from sso_admin import SSOAdmin
    return SSOAdmin(cfg.manage_cmd, cfg.sso_repo)


@pytest.fixture(scope="session", autouse=False)
def seeded(sso_admin):
    """Seed once per session (idempotent; resets every qa_wiki_* user)."""
    return sso_admin.seed()


@pytest.fixture
def reset_user(sso_admin, seeded):
    """Reset the named users before and after the test (mutating tests)."""
    names: list[str] = []

    def _reset(*users):
        for u in users:
            sso_admin.call("reset_user", u)
            names.append(u)
    yield _reset
    for u in names:
        sso_admin.call("reset_user", u)


def sso_login(sso_http, username, password=None, client="native"):
    import qasso_data as D
    for attempt in range(4):
        r = sso_http.post("/api/v1/auth/login", json={"username": username,
                                                      "password": D.PASSWORD if password is None else password,
                                                      "client": client})
        if r.status_code != 429 or attempt == 3:
            return r
        time.sleep(min(int(r.headers.get("Retry-After", "5")), 61))
    return r


@pytest.fixture(scope="session")
def tokens(sso_http, seeded):
    """Cached native-client token pairs per username: tokens('qa_wiki_direct') -> login JSON."""
    cache: dict = {}

    def _get(username, fresh=False):
        import jwt_forge
        if username in cache and jwt_forge.payload(cache[username]["access_token"])["exp"] - time.time() < 45:
            fresh = True    # short QA access TTLs (SSO_ACCESS_TTL_MIN=2): never hand out a nearly expired token
        if fresh or username not in cache:
            r = sso_login(sso_http, username)
            assert r.status_code == 200, (username, r.status_code, r.text)
            cache[username] = r.json()
        return cache[username]
    return _get


@pytest.fixture(scope="session")
def jwks(sso_http):
    r = sso_http.get("/.well-known/jwks.json")
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="session")
def signing_key(cfg):
    if not cfg.private_key_path:
        pytest.skip("SSO_TEST_PRIVATE_KEY_PATH not set")
    import jwt_forge
    return jwt_forge.load_private_key(cfg.private_key_path)


@pytest.fixture(scope="session")
def service_auth():
    import qasso_data as D
    return (D.SERVICE_CLIENT_ID, D.SERVICE_CLIENT_SECRET)


@pytest.fixture(scope="session")
def login_client_auth():
    """The ServiceClient llm-wiki uses for /login and /refresh delegation (v1.1 item 4)."""
    import qasso_data as D
    return (D.LOGIN_CLIENT_ID, D.LOGIN_CLIENT_SECRET)


@pytest.fixture(scope="session")
def forge(jwks, signing_key, cfg):
    """forge(base_token, **claim_overrides, _key=None, _kid=None, _drop=()) -> token signed by the QA SSO key."""
    import jwt_forge

    def _f(base_token, _key=None, _kid="real", _drop=(), **overrides):
        pl = jwt_forge.claims_like(base_token, **overrides)
        for k in _drop:
            pl.pop(k, None)
        kid = jwt_forge.header(base_token).get("kid") if _kid == "real" else _kid
        return jwt_forge.sign(pl, _key or signing_key, kid)
    return _f


# ------------------------------------------------------------------------------------------------ llm-wiki side
@pytest.fixture
def browser_factory():
    from wiki_client import Browser
    made = []

    def _make(inst_or_url, source_ip=None):
        b = Browser(inst_or_url.url if isinstance(inst_or_url, Instance) else inst_or_url, source_ip=source_ip)
        made.append(b)
        return b
    yield _make
    for b in made:
        b.close()


@pytest.fixture
def logged_in(browser_factory, wiki, seeded):
    """logged_in('qa_wiki_direct', inst=None) -> Browser with a fresh session on that instance."""
    import qasso_data as D

    def _login(username, inst=None):
        b = browser_factory(inst or wiki)
        b.login(username, D.PASSWORD)
        return b
    return _login


def access_log_lines(cfg, needle: str) -> int:
    if not cfg.sso_access_log or not os.path.isfile(cfg.sso_access_log):
        pytest.skip("SSO_ACCESS_LOG not set")
    time.sleep(0.3)
    with open(cfg.sso_access_log, errors="replace") as fh:
        return sum(1 for line in fh if needle in line)


# ------------------------------------------------------------------------------------------------ stub SSO
class StubControl:
    def __init__(self, url):
        self.c = httpx.Client(base_url=url, timeout=10)

    def reset(self):
        assert self.c.post("/__qa/reset").status_code == 200

    def config(self, **kw):
        r = self.c.post("/__qa/config", json=kw)
        assert r.status_code == 200, r.text
        return r.json()

    def calls(self) -> dict:
        return self.c.get("/__qa/calls").json()

    def count(self, path) -> int:
        return self.calls()["counts"].get(path, 0)


@pytest.fixture
def stub(cfg):
    if not (cfg.stub_url and cfg.stubbed_url):
        pytest.skip("STUB_SSO_URL / LLM_WIKI_STUBBED_URL not set")
    s = StubControl(cfg.stub_url)
    s.reset()
    yield s
    s.reset()
    s.c.close()


@pytest.fixture
def stubbed(cfg) -> Instance:
    if not cfg.stubbed_url:
        pytest.skip("LLM_WIKI_STUBBED_URL not set")
    return Instance("stubbed", cfg.stubbed_url, "sso", False)


# ------------------------------------------------------------------------------------------------ v1.1 sessions
@pytest.fixture
def session_db(cfg):
    """session_db(instance_name) -> SessionStore on that instance's SESSION_DB_PATH (QA DB only)."""
    from session_store import SessionStore

    def _open(name):
        path = cfg.session_dbs.get(name)
        if not path:
            pytest.skip(f"LLM_WIKI_SESSION_DBS has no entry for {name!r}")
        return SessionStore(path)
    return _open


@pytest.fixture
def restart(cfg):
    """restart('sso') restarts one llm-wiki instance by PID (run/restart.sh) and waits for /healthz."""
    import shlex
    import subprocess

    def _restart(name):
        if not cfg.restart_cmd:
            pytest.skip("QA_SSO_RESTART_CMD not set")
        p = subprocess.run(shlex.split(cfg.restart_cmd.format(name=name)), capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    return _restart


def instance_name_of(cfg, inst) -> str:
    for name, i in cfg.instances.items():
        if i.url == inst.url:
            return name
    return inst.name
