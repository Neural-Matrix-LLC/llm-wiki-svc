"""``/login`` and ``/logout``: the server-rendered SSO login (docs/sso-integration-design.md B4).

Mounted only when AUTH_MODE is ``sso`` or ``both`` (static mode: 404, design v1.1 item 5). The
browser never sees an SSO token: this service calls the SSO's native-client login server-side (as
its ServiceClient, with the browser's IP) and keeps the tokens in the server-side session store;
the ``llmwiki_session`` cookie is only an opaque session id (v1.1 item 2).

CSRF: the login form (no session yet) uses a double-submit token (random value in a SameSite=Strict
cookie plus a hidden field). Once signed in, the session's own csrf value is required: as the hidden
field of the logout form, or as ``X-CSRF-Token`` on any other cookie-authenticated write (v1.1 item
3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from llmwiki import tools
from llmwiki.api.session import clear_cookie_header, client_ip_of, cookie_header, read_cookie
from llmwiki.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_NEXT = "/dashboard"
CSRF_MAX_AGE = 2 * 3600


def _cfg() -> Settings:
    """The live settings (tests monkeypatch ``llmwiki.config.settings``)."""
    from llmwiki import config

    return config.settings


def safe_next(target: str | None) -> str:
    """Only same-origin relative paths; anything else becomes ``/dashboard`` (no open redirect)."""
    if not target or len(target) > 2048:
        return DEFAULT_NEXT
    if any(ch in target for ch in "\\\r\n\t") or any(ord(ch) < 0x20 for ch in target):
        return DEFAULT_NEXT
    if not target.startswith("/") or target.startswith("//"):
        return DEFAULT_NEXT
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return DEFAULT_NEXT
    if parts.path.startswith(("/login", "/logout")):
        return DEFAULT_NEXT
    return target


def login_csrf_for(request: Request) -> tuple[str, Callable[[Response], None]]:
    """The login form's CSRF value and a function that sets its double-submit cookie."""
    service = tools.auth_service(_cfg())
    current = request.cookies.get(service.login_csrf_cookie)
    if current and len(current) >= 32:
        return current, lambda response: None
    token = service.new_csrf_token()

    def set_cookie(response: Response) -> None:
        response.headers.append("set-cookie", cookie_header(
            _cfg(), service.login_csrf_cookie, token, CSRF_MAX_AGE, samesite="strict"))

    return token, set_cookie


_STYLE = """
:root { --fg:#1b1b1b; --muted:#6b6b6b; --bg:#fff; --line:#e4e4e4; --bar:#3b6ea5; --bad:#b3261e; }
@media (prefers-color-scheme: dark) { :root { --fg:#eaeaea; --muted:#9a9a9a; --bg:#151515;
  --line:#2e2e2e; --bar:#7aa7d8; --bad:#ff6b5e; } }
body { font: 15px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); margin: 0;
  padding: 24px 16px; max-width: 420px; margin-inline: auto; }
h1 { font-size: 22px; margin: 0 0 4px; } .muted { color: var(--muted); } .bad { color: var(--bad); }
form.login { border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-top: 16px; }
label { display: block; margin: 10px 0 4px; color: var(--muted); }
input[type=text], input[type=password] { width: 100%; box-sizing: border-box; padding: 8px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
button { margin-top: 14px; padding: 8px 14px; border: 0; border-radius: 6px; background: var(--bar);
  color: #fff; font: inherit; cursor: pointer; }
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(title)}"
            "</title><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>{_STYLE}</style></head><body>{body}</body></html>")


def login_page(request: Request, *, next_url: str, error: str = "", username: str = "",
               status: int = 200, headers: dict | None = None) -> HTMLResponse:
    csrf, set_csrf = login_csrf_for(request)
    error_html = f"<p class='bad' role='alert'>{escape(error)}</p>" if error else ""
    body = (
        "<h1>llmwiki <span class='muted'>sign in</span></h1>"
        "<p class='muted'>Sign in with your central account.</p>"
        f"{error_html}"
        "<form class='login' method='post' action='/login'>"
        f"<input type='hidden' name='csrf_token' value='{escape(csrf)}'>"
        f"<input type='hidden' name='next' value='{escape(next_url)}'>"
        "<label for='username'>Username or email</label>"
        f"<input id='username' name='username' type='text' autocomplete='username' required "
        f"autofocus value='{escape(username)}'>"
        "<label for='password'>Password</label>"
        "<input id='password' name='password' type='password' autocomplete='current-password' "
        "required>"
        "<button type='submit'>Sign in</button></form>"
    )
    response = HTMLResponse(_page("llmwiki - sign in", body), status_code=status, headers=headers)
    response.headers["Cache-Control"] = "no-store"
    set_csrf(response)
    return response


def no_access_message() -> str:
    perm = _cfg().sso_required_permission
    return f"Your account doesn't have access to llm-wiki ({perm}). Ask an admin."


def no_access_page(request: Request) -> HTMLResponse:
    """403 after a refresh dropped the permission (the middleware already deleted the session)."""
    return login_page(request, next_url=DEFAULT_NEXT, error=no_access_message(), status=403)


UNAVAILABLE = "Login service unavailable. Please try again in a moment."
MISCONFIGURED = "Login service misconfigured. Please tell the administrator."


def unavailable_page(misconfigured: bool = False) -> HTMLResponse:
    """503: the SSO is unreachable (session kept, Q-05) or refused our ServiceClient (C-19)."""
    body = (f"<h1>llmwiki</h1><p class='bad'>{MISCONFIGURED if misconfigured else UNAVAILABLE}"
            "</p>")
    return HTMLResponse(_page("llmwiki", body),
                        status_code=503, headers={"Retry-After": "30", "Cache-Control": "no-store"})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = None) -> Response:
    """The login form. Already signed in: straight to ``next``."""
    target = safe_next(next)
    if getattr(request.state, "auth_principal", None) is not None:
        return RedirectResponse(target, status_code=303)
    return login_page(request, next_url=target)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    next: str = Form(DEFAULT_NEXT),
) -> Response:
    cfg = _cfg()
    service = tools.auth_service(cfg)
    target = safe_next(next)
    username = username.strip()
    if not service.csrf_ok(request.cookies.get(service.login_csrf_cookie), csrf_token):
        logger.info("login form rejected: CSRF token missing or mismatched")
        return login_page(request, next_url=target, username=username, status=403,
                          error="Your login form expired. Please try again.")
    if not username or not password:
        return login_page(request, next_url=target, username=username, status=400,
                          error="Enter your username or email and your password.")
    try:
        session_id, rec, principal = service.login(username, password,
                                                   client_ip_of(request.scope, cfg))
    except tools.PermissionDenied:
        logger.info("login without %s: %s", service.required_permission, username)
        return login_page(request, next_url=target, username=username, status=403,
                          error=no_access_message())
    except tools.SSOLoginError as exc:
        logger.info("login refused by the SSO (%s): %s", exc.code, username)
        return _login_error(request, target, username, exc)
    except tools.SSOMisconfigured as exc:  # v1.2 C-19
        logger.error("login impossible: %s", exc)
        return _login_503(request, target, username, MISCONFIGURED)
    except tools.SSOUnavailable as exc:  # v1.2 C-14: 503 everywhere
        logger.warning("login service unavailable: %s", exc)
        return _login_503(request, target, username, UNAVAILABLE)
    except tools.AuthError as exc:  # the SSO answered a token we cannot verify (iss/keys)
        logger.error("SSO login returned an unverifiable token (SSO_ISSUER/JWKS?): %s", exc)
        return _login_503(request, target, username, MISCONFIGURED)
    logger.info("login ok: %s (sub %s)", principal.username, principal.sub)
    # session rotation: the browser's previous session (if any) ends here; the new session has a
    # new id, cookie and csrf value
    previous = getattr(request.state, "auth_session", None) or service.get_session(
        read_cookie(request.scope, service.session_cookie))
    service.retire(previous, rec)
    response = RedirectResponse(target, status_code=303)
    response.headers.append("set-cookie", cookie_header(
        cfg, service.session_cookie, session_id, service.session_max_age(rec)))
    # the login double-submit cookie has done its job; the session carries its own csrf value
    response.headers.append("set-cookie", clear_cookie_header(cfg, service.login_csrf_cookie))
    response.headers["Cache-Control"] = "no-store"
    return response


def _login_503(request: Request, target: str, username: str, message: str) -> HTMLResponse:
    response = login_page(request, next_url=target, username=username, status=503, error=message)
    response.headers["Retry-After"] = "30"
    return response


def _login_error(request: Request, target: str, username: str, exc: Exception) -> HTMLResponse:
    code = getattr(exc, "code", "")
    if code == "invalid_credentials":
        return login_page(request, next_url=target, username=username, status=401,
                          error="Wrong username or password.")
    if code == "account_inactive":
        return login_page(request, next_url=target, username=username, status=403,
                          error="Please activate your account first (check your email), "
                                "or ask an admin.")
    if code == "rate_limited":
        wait = getattr(exc, "retry_after", None) or 60
        return login_page(request, next_url=target, username=username, status=429,
                          error=f"Too many attempts, try again in {wait} s.",
                          headers={"Retry-After": str(wait)})
    if code == "invalid_request":
        return login_page(request, next_url=target, username=username, status=400,
                          error="Enter your username or email and your password.")
    return login_page(request, next_url=target, username=username, status=401,
                      error="Login failed. Please try again.")


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")) -> Response:
    """Delete the session, discard the SSO refresh token, clear the cookie, back to ``/login``.

    CSRF: the session's csrf value as the form field or the X-CSRF-Token header.
    """
    cfg = _cfg()
    service = tools.auth_service(cfg)
    session = getattr(request.state, "auth_session", None)
    if session is None:  # no live session: just clear whatever cookie there is
        raw = read_cookie(request.scope, service.session_cookie)
        rec = service.get_session(raw)
    else:
        rec = session
    if rec is not None:
        supplied = csrf_token or request.headers.get(service.csrf_header, "")
        if not service.csrf_ok(rec.csrf, supplied):
            return HTMLResponse(_page("llmwiki", "<p class='bad'>Invalid logout form. Go back "
                                                 "and try again.</p>"), status_code=403)
        service.logout(rec)
    response = RedirectResponse("/login", status_code=303)
    response.headers.append("set-cookie", clear_cookie_header(cfg, service.session_cookie))
    return response
