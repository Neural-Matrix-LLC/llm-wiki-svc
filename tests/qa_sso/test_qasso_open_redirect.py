"""REDIR-*: `next` on /login is restricted to same-origin relative paths; otherwise /dashboard (design B4)."""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

import qasso_data as D
from wiki_client import resolve_like_browser

pytestmark = [pytest.mark.needs_wiki, pytest.mark.needs_sso]

EVIL = [
    "https://evil.example/x", "http://evil.example", "HTTPS://EVIL.EXAMPLE", "//evil.example", "///evil.example",
    "////evil.example", "\\\\evil.example", "/\\evil.example", "\\/evil.example", "/\\/evil.example",
    "/%2F/evil.example", "%2F%2Fevil.example", "/%5Cevil.example", "%5C%5Cevil.example",
    "/\t/evil.example", "/\n/evil.example", "/%09/evil.example", " //evil.example", "\x00//evil.example",
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "data:text/html,<script>alert(1)</script>",
    "https:evil.example", "http:evil.example", "//evil.example@127.0.0.1", "/login?next=https://evil.example",
    "https://evil.example%2F@", "//%65vil.example", "\u3002evil.example", "//evil\u3002example",
]
SAFE = ["/dashboard", "/usage?month=2026-10", "/page/some-slug", "/dashboard#frag"]


def _login(wiki, browser_factory, nxt):
    b = browser_factory(wiki)
    r = b.submit_login("qa_wiki_direct", D.PASSWORD, next_path=nxt)
    assert r.status_code == 303, (nxt, r.status_code)
    return r.headers["location"]


@pytest.mark.parametrize("nxt", EVIL, ids=[repr(e) for e in EVIL])
def test_REDIR_01_evil_next_falls_back(wiki, browser_factory, seeded, nxt):
    loc = _login(wiki, browser_factory, nxt)
    resolved = urlsplit(resolve_like_browser(wiki.url, loc))
    assert resolved.scheme in ("http", "https") and resolved.netloc == urlsplit(wiki.url).netloc, (nxt, loc)
    assert not loc.lower().lstrip().startswith(("javascript:", "data:"))
    assert loc == "/dashboard" or loc.startswith("/"), loc


@pytest.mark.parametrize("nxt", SAFE)
def test_REDIR_02_safe_next_kept(wiki, browser_factory, seeded, nxt):
    loc = _login(wiki, browser_factory, nxt)
    assert loc in (nxt, nxt.split("#")[0]), (nxt, loc)


def test_REDIR_03_next_via_hidden_field_tampered(wiki, browser_factory, seeded):
    """The POSTed next (not just the GET query) is validated: tamper the hidden field."""
    b = browser_factory(wiki)
    _, form = b.open_login("/dashboard")
    next_fields = [k for k, v in form.fields.items() if v == "/dashboard"] or ["next"]
    r = b.submit_login("qa_wiki_direct", D.PASSWORD, next_path="/dashboard",
                       override={next_fields[0]: "https://evil.example"})
    assert r.status_code == 303
    assert urlsplit(resolve_like_browser(wiki.url, r.headers["location"])).netloc == urlsplit(wiki.url).netloc


def test_REDIR_04_next_as_query_on_post(wiki, browser_factory, seeded):
    """POST /login?next=https://evil.example (query instead of field) is validated too."""
    b = browser_factory(wiki)
    _, form = b.open_login()
    data = dict(form.fields)
    data[form.user_field or "username"] = "qa_wiki_direct"
    data[form.password_field] = D.PASSWORD
    r = b.post_form("/login?next=https%3A%2F%2Fevil.example", data)
    assert r.status_code == 303
    assert urlsplit(resolve_like_browser(wiki.url, r.headers["location"])).netloc == urlsplit(wiki.url).netloc


def test_REDIR_05_logout_redirect_is_fixed(wiki, logged_in):
    from wiki_client import logout_form
    b = logged_in("qa_wiki_logout")
    form = logout_form(b.get("/dashboard").text)
    assert form is not None
    r = b.post_form("/logout?next=https://evil.example", dict(form.fields))
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
