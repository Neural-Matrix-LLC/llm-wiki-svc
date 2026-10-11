"""Browser-session middleware (docs/sso-integration-design.md B4).

Installed only when AUTH_MODE != static.

Per request carrying the ``llmwiki_session`` cookie: verify it, refresh the SSO access token
server-side when it expires within 60 s (rotating both tokens in the cookie), and leave the outcome
in ``request.state``:

* ``auth_principal`` - the signed-in :class:`~llmwiki.tools.Principal` (``via="session"``), or None;
* ``auth_problem`` - None, ``"expired"`` (cookie bad / refresh refused: the cookie is cleared) or
  ``"forbidden"`` (the permission was revoked: the cookie is cleared, routes answer 403).

A plain ASGI middleware (not ``BaseHTTPMiddleware``), so streaming responses - the mounted MCP
app - pass through untouched; ``/mcp`` is skipped altogether.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http.cookies import SimpleCookie
from typing import Any

import anyio

from llmwiki import tools
from llmwiki.config import Settings

logger = logging.getLogger(__name__)

SKIP_PREFIXES = ("/mcp", "/channels/", "/healthz")


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


def session_cookie_header(cfg: Settings, name: str, value: str, max_age: int,
                          samesite: str = "lax") -> str:
    """One Set-Cookie value: HttpOnly, Secure (configurable), Path=/."""
    parts = [f"{name}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", f"SameSite={samesite}"]
    if cfg.session_cookie_secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(cfg: Settings, name: str) -> str:
    return session_cookie_header(cfg, name, '""', 0)


class SessionAuthMiddleware:
    def __init__(self, app: Any, cfg: Settings) -> None:
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        service = tools.auth_service(self.cfg)
        state = scope.setdefault("state", {})
        state["auth_principal"] = None
        state["auth_problem"] = None
        raw = read_cookie(scope, service.session_cookie)
        set_cookie: str | None = None
        if raw:
            set_cookie = await anyio.to_thread.run_sync(self._resolve, service, raw, state)

        if set_cookie is None:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                prefix = f"{service.session_cookie}=".encode()
                # a route that set the session cookie itself (login/logout) wins
                if not any(k == b"set-cookie" and v.startswith(prefix) for k, v in headers):
                    headers.append((b"set-cookie", set_cookie.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _resolve(self, service: Any, raw: str, state: dict) -> str | None:
        """Fill ``state``; return a Set-Cookie value to add (rotated or cleared), or None."""
        session = service.load_session(raw)
        if session is None:
            state["auth_problem"] = "expired"
            return clear_cookie_header(self.cfg, service.session_cookie)
        try:
            session, principal, rotated = service.session_principal(session)
        except tools.PermissionDenied:
            logger.info("session of %s no longer has %s; signed out", session.username,
                        service.required_permission)
            state["auth_problem"] = "forbidden"
            return clear_cookie_header(self.cfg, service.session_cookie)
        except tools.AuthError as exc:
            logger.info("session of %s ended: %s", session.username, exc)
            state["auth_problem"] = "expired"
            return clear_cookie_header(self.cfg, service.session_cookie)
        except tools.SSOUnavailable as exc:
            # keep the cookie: the next request can refresh once the SSO is back
            logger.warning("session refresh impossible, SSO unavailable: %s", exc)
            state["auth_problem"] = "unavailable"
            return None
        state["auth_principal"] = principal
        state["auth_session"] = session
        if rotated:
            return session_cookie_header(self.cfg, service.session_cookie,
                                         service.dump_session(session), session.max_age())
        return None
