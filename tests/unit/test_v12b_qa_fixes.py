"""QA round on a9b744e: D-17 (bounded introspect), D-18 (Retry-After), D-19 (proxy headers)."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
from tests.sso_fake import access_claims
from tests.unit import test_auth_routes as _routes
from tests.unit.test_auth_routes import bearer, login, make_settings

from llmwiki.api.session import client_ip_of
from llmwiki.auth.principal import SSOUnavailable
from llmwiki.auth.service import AuthService

key = _routes.key
sso = _routes.sso
build = _routes.build

# ---------------------------------------------------------------- D-17
def test_introspect_defaults_are_bounded(tmp_path) -> None:
    cfg = make_settings(tmp_path, auth_mode="sso")
    assert cfg.sso_introspect_timeout_s == 2.5 and cfg.sso_introspect_retries == 0
    conf = AuthService(cfg).introspector.config
    assert conf.timeout == 2.5 and conf.retries == 0 and conf.connect_timeout <= 1.0


@pytest.mark.parametrize("over", [{"sso_introspect_timeout_s": 0}, {"sso_introspect_timeout_s": 31},
                                  {"sso_introspect_retries": 2}, {"sso_introspect_retries": -1}])
def test_introspect_bounds_are_validated(tmp_path, over) -> None:
    cfg = make_settings(tmp_path, auth_mode="sso", **over)
    with pytest.raises(RuntimeError, match="SSO_INTROSPECT"):
        cfg.require_auth_config()


@pytest.fixture
def hung_sso():
    """Accepts TCP connections and never answers."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                continue

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    t.join()
    for c in held:
        c.close()
    srv.close()


@pytest.mark.parametrize("retries,limit", [(0, 1.4), (1, 2.6)])
def test_hung_sso_is_503_within_timeout_times_attempts(tmp_path, hung_sso, retries, limit) -> None:
    cfg = make_settings(tmp_path, auth_mode="sso", sso_base_url=hung_sso,
                        sso_introspect_timeout_s=1.0, sso_introspect_retries=retries)
    svc = AuthService(cfg)
    t0 = time.monotonic()
    with pytest.raises(SSOUnavailable):
        svc.introspect("a.b.c", "203.0.113.5")
    took = time.monotonic() - t0
    assert 0.9 * (1 + retries) <= took < limit, took


# ---------------------------------------------------------------- D-18
def test_api_503_carries_retry_after(build, sso) -> None:
    client = build(auth_mode="sso")
    login(client)
    sso.down = True
    r = client.get("/usage")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_bearer_503_carries_retry_after(build, sso, key) -> None:
    client = build(auth_mode="sso")
    token = key.sign(access_claims())
    assert client.get("/usage", headers=bearer(token)).status_code == 200  # JWKS cached
    sso.down = True
    r = client.get("/usage", headers=bearer(key.sign(access_claims())))
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_misconfigured_503_carries_retry_after(build, sso, key) -> None:
    client = build(auth_mode="sso")
    sso.introspect_failure = "401"
    r = client.get("/usage", headers=bearer(key.sign(access_claims())))
    assert r.status_code == 503 and r.json() == {"detail": "Login service misconfigured"}
    assert r.headers["retry-after"] == "30"


# ---------------------------------------------------------------- D-19
def _scope(peer: str, headers: dict[str, str]) -> dict:
    return {"type": "http", "client": (peer, 40000),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}


@pytest.mark.parametrize("peer", ["198.51.100.9", "127.0.0.1"])
def test_forged_xff_ignored_without_client_ip_header(tmp_path, peer) -> None:
    cfg = make_settings(tmp_path, auth_mode="sso", trusted_proxy_ips="127.0.0.1/32")
    assert client_ip_of(_scope(peer, {"X-Forwarded-For": "203.0.113.66"}), cfg) == peer


def test_forged_header_from_an_untrusted_peer_ignored(tmp_path) -> None:
    cfg = make_settings(tmp_path, auth_mode="sso", client_ip_header="x-real-ip",
                        trusted_proxy_ips="127.0.0.1/32")
    forged = {"X-Real-IP": "203.0.113.66", "X-Forwarded-For": "203.0.113.67"}
    assert client_ip_of(_scope("198.51.100.9", forged), cfg) == "198.51.100.9"
    assert client_ip_of(_scope("127.0.0.1", forged), cfg) == "203.0.113.66"  # trusted: header wins


def test_uvicorn_proxy_headers_cannot_rewrite_the_peer() -> None:
    """A real uvicorn with its defaults (proxy_headers on) and even forwarded_allow_ips='*': a
    same-host client forging X-Forwarded-For still reaches the app as 127.0.0.1."""
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config("tests.unit.ip_probe_app:app", host="127.0.0.1", port=port,
                            proxy_headers=True, forwarded_allow_ips="*", log_level="warning",
                            lifespan="off")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        r = httpx.get(f"http://127.0.0.1:{port}/", headers={"X-Forwarded-For": "203.0.113.66",
                                                            "X-Forwarded-Proto": "https"})
        assert r.json() == {"client": "127.0.0.1"}
        assert type(config.loaded_app).__name__ == "IgnoreProxyHeaders"
    finally:
        server.should_exit = True
        t.join(5)


def test_dockerfile_disables_proxy_headers() -> None:
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath("Dockerfile").read_text()
    cmds = [ln for ln in text.splitlines() if "uvicorn" in ln and "llmwiki.api.app:app" in ln]
    assert len(cmds) == 2 and text.count('"--no-proxy-headers"') == 2

