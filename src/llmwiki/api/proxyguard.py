"""D-19: llm-wiki decides the client IP itself (``CLIENT_IP_HEADER`` + ``TRUSTED_PROXY_IPS``,
api/session.py ``client_ip_of``). uvicorn's ``--proxy-headers`` (on by default, trusting
``FORWARDED_ALLOW_IPS``, default ``127.0.0.1,::1``) would rewrite ``scope["client"]`` from
``X-Forwarded-For`` *before* that check runs, so any same-host or misconfigured peer could choose
the IP the SSO binds and throttles on.

The Dockerfile starts uvicorn with ``--no-proxy-headers``. Because other launch paths (a plain
``uvicorn llmwiki.api.app:app``, test harnesses, ``python -m uvicorn``) default it on, importing the
app also neutralises the wrapper: uvicorn's ``Config.load()`` imports the app *before* it wraps it
in ``uvicorn.config.ProxyHeadersMiddleware``, so replacing that name here makes the wrap a no-op in
every worker. Nothing else in uvicorn is touched; the scheme is not rewritten either (cookies use
``SESSION_COOKIE_SECURE``, not the request scheme).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class IgnoreProxyHeaders:
    """Stands in for uvicorn's ProxyHeadersMiddleware: passes the scope through untouched."""

    def __init__(self, app, trusted_hosts=None) -> None:  # same signature as uvicorn's
        self.app = app
        if trusted_hosts:
            logger.info("uvicorn proxy headers ignored (forwarded_allow_ips=%s): llm-wiki uses "
                        "CLIENT_IP_HEADER/TRUSTED_PROXY_IPS only", trusted_hosts)

    async def __call__(self, scope, receive, send) -> None:
        await self.app(scope, receive, send)


def disable_uvicorn_proxy_headers() -> bool:
    """Make uvicorn's proxy-header rewriting a no-op in this process. True if uvicorn is present."""
    try:
        import uvicorn.config as uvicorn_config
    except ImportError:  # not served by uvicorn
        return False
    uvicorn_config.ProxyHeadersMiddleware = IgnoreProxyHeaders  # type: ignore[attr-defined,misc]
    return True
