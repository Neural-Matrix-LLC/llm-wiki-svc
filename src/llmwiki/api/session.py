"""Browser-session and MCP auth middleware (docs/sso-integration-design.md B4, v1.1, v1.2).

Plain ASGI (not ``BaseHTTPMiddleware``), so streaming responses - the mounted MCP app - pass
through untouched.

:class:`SessionAuthMiddleware` (AUTH_MODE=sso|both): for a request carrying the
``llmwiki_session`` cookie (an opaque session id), look the session up in the server-side store,
refresh its SSO access token when it expires within 60 s (one refresh per session at a time: a
process-local asyncio lock here, and ``BEGIN IMMEDIATE`` in the store across workers), and leave
in ``request.state``:

* ``auth_principal`` / ``auth_session`` - the signed-in principal (``via="session"``) and its
  record;
* ``auth_problem`` - None, ``"expired"`` (unknown/idle id, refresh refused: cookie cleared),
  ``"forbidden"`` (permission gone: session deleted, cookie cleared), ``"unavailable"`` (SSO down
  and the access token expired) or ``"misconfigured"`` (the SSO refused our ServiceClient); in
  the last two the session is kept and routes answer 503.

:class:`MCPAuthMiddleware`: wraps the mounted MCP app. sso/both (v1.1 item 1): Bearer only - an
SSO access token with the permission, or the static token in ``both``. static with
SSO_PROTECT_READS=true (v1.2 C-12): the static token, with the Phase 0 401 body. MCP clients are
not browsers, so the session cookie is deliberately not accepted there (no CSRF surface on /mcp).

:func:`client_ip_of`: the browser IP sent to the SSO (v1.2 C-11): the TCP peer, or
``CLIENT_IP_HEADER`` when the peer is one of ``TRUSTED_PROXY_IPS``; derived by
``sso_client.get_client_ip`` (SSO design v1.2 §4.5, the same algorithm as the SSO).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from http.cookies import SimpleCookie
from typing import Any

import anyio
from sso_client import get_client_ip, normalize_ip

from llmwiki import tools
from llmwiki.config import Settings

logger = logging.getLogger(__name__)

SKIP_PREFIXES = ("/mcp", "/channels/", "/healthz")
#: Pages that show or set session state: never cached (QA Q-20). Only in sso/both (this
#: middleware is not installed in static mode, whose /dashboard stays byte-for-byte unchanged).
NO_STORE_PATHS = frozenset({"/login", "/logout", "/dashboard",
                            # Starlette's 307 trailing-slash redirects to the above
                            "/login/", "/logout/", "/dashboard/"})


def read_cookie(scope: Mapping[str, Any], name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            jar: SimpleCookie = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:  # malformed Cookie header: treat as absent
                continue
            if name in jar:
                return jar[name].value
    return None


def read_header(scope: Mapping[str, Any], name: str) -> str:
    wanted = name.lower().encode()
    for key, value in scope.get("headers", []):
        if key == wanted:
            return str(value.decode("latin-1"))
    return ""


def client_ip_of(scope: Mapping[str, Any], cfg: Settings) -> str | None:
    """The browser IP to pass to the SSO (design v1.2 C-11/Q-25).

    The TCP peer as uvicorn reports it, unless ``CLIENT_IP_HEADER`` is set AND the peer is in
    ``TRUSTED_PROXY_IPS``: then that header. A comma list (``X-Forwarded-For``) is read right to
    left, skipping trusted proxies; the first other valid IP wins. A missing or malformed header
    falls back to the peer. None (the SSO then uses our own address) for a non-IP peer.

    Implemented by ``sso_client.get_client_ip``. Without ``CLIENT_IP_HEADER`` no header is read
    at all (unchanged behaviour), so the trusted list is only passed when a header is configured.
    IPs are normalised the SSO's way (``::ffff:a.b.c.d`` -> ``a.b.c.d``, IPv6 compressed).
    """
    client = scope.get("client")
    peer = client[0] if client else None
    header = cfg.client_ip_header.strip()
    if not header:
        return normalize_ip(peer)
    return get_client_ip(peer, scope.get("headers", []), tuple(cfg.trusted_proxy_networks), header)


def cookie_header(cfg: Settings, name: str, value: str, max_age: int,
                  samesite: str = "lax") -> str:
    """One Set-Cookie value: HttpOnly, Secure (configurable), Path=/."""
    parts = [f"{name}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", f"SameSite={samesite}"]
    if cfg.session_cookie_secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(cfg: Settings, name: str) -> str:
    return cookie_header(cfg, name, '""', 0)


class SessionAuthMiddleware:
    def __init__(self, app: Any, cfg: Settings) -> None:
        self.app = app
        self.cfg = cfg
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) > 10_000:  # bounded: drop idle locks
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        service = tools.auth_service(self.cfg)
        state = scope.setdefault("state", {})
        state["auth_principal"] = None
        state["auth_session"] = None
        state["auth_problem"] = None
        raw = read_cookie(scope, service.session_cookie)
        clear = False
        if raw:
            async with self._lock(hashlib.sha256(raw.encode()).hexdigest()):
                clear = await anyio.to_thread.run_sync(self._resolve, service, raw, state,
                                                       client_ip_of(scope, self.cfg))
        no_store = scope.get("path", "") in NO_STORE_PATHS
        if not clear and not no_store:
            await self.app(scope, receive, send)
            return
        header = clear_cookie_header(self.cfg, service.session_cookie).encode("latin-1")
        prefix = f"{service.session_cookie}=".encode()

        async def send_wrapped(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # a route that set the session cookie itself (POST /login) wins
                if clear and not any(k == b"set-cookie" and v.startswith(prefix)
                                     for k, v in headers):
                    headers.append((b"set-cookie", header))
                if no_store:  # every response: pages, redirects, errors
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapped)

    def _resolve(self, service: Any, raw: str, state: dict, client_ip: str | None) -> bool:
        """Fill ``state``; return True when the browser's cookie must be cleared."""
        rec = service.get_session(raw)
        if rec is None:
            state["auth_problem"] = "expired"
            return True
        try:
            rec, principal = service.session_principal(rec, client_ip)
        except tools.PermissionDenied:
            logger.info("session of %s no longer has %s; signed out", rec.username,
                        service.required_permission)
            state["auth_problem"] = "forbidden"
            return True
        except tools.SSOMisconfigured:  # logged as an error by the service; session kept
            state["auth_problem"] = "misconfigured"
            return False
        except tools.SSOUnavailable as exc:
            logger.warning("session refresh impossible, SSO unavailable: %s", exc)
            state["auth_problem"] = "unavailable"  # session kept (v1.1 Q-05)
            return False
        except tools.AuthError as exc:
            logger.info("session of %s ended: %s", rec.username, exc)
            state["auth_problem"] = "expired"
            return True
        state["auth_principal"] = principal
        state["auth_session"] = rec
        return False


class MCPAuthMiddleware:
    """Bearer auth in front of the mounted MCP ASGI app (sso/both: v1.1 item 1; static with
    SSO_PROTECT_READS=true: v1.2 C-12, Phase 0 401 body)."""

    def __init__(self, app: Any, cfg: Settings) -> None:
        self.app = app
        self.cfg = cfg

    def __getattr__(self, name: str) -> Any:  # e.g. .lifespan / .state of the wrapped app
        return getattr(self.app, name)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = read_header(scope, "authorization")
        scheme, _, token = auth.partition(" ")
        if self.cfg.auth_mode == "static":  # the same 401 as every static-mode route
            status, body, headers = 401, {"detail": "invalid or missing bearer token"}, []
        else:
            status, body, headers = 401, {"detail": "invalid or missing credentials"}, [
                (b"www-authenticate", b'Bearer error="invalid_token"')]
        if scheme.lower() == "bearer" and token.strip():
            service = tools.auth_service(self.cfg)
            try:
                await anyio.to_thread.run_sync(service.authenticate_bearer, token.strip(),
                                               client_ip_of(scope, self.cfg))
            except tools.PermissionDenied:
                status, headers = 403, []
                body = {"detail": "forbidden", "error": "permission_denied"}
            except tools.SSOUnavailable as exc:  # fail closed (sso-client decision 14)
                logger.warning("MCP bearer check impossible, SSO unavailable: %s", exc)
                status, headers = 503, [(b"retry-after", b"5")]
                body = {"detail": "Login service unavailable"}
            except tools.AuthError as exc:
                logger.info("MCP bearer rejected: %s", exc)
            else:
                await self.app(scope, receive, send)
                return
        # compact, exactly like FastAPI's JSONResponse: the static-mode 401 must be byte-identical
        # to every other static route's 401 (v1.2 C-12, QA REG-14b)
        payload = json.dumps(body, separators=(",", ":")).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode()), *headers]})
        await send({"type": "http.response.body", "body": payload})
