"""httpx-based clients for black-box tests: a cookie-less API client and a 'browser' with a manual cookie jar.

The browser keeps cookies itself (it never lets httpx drop a Secure cookie on plain-http test servers) and
records every raw Set-Cookie, so cookie flags can be asserted exactly. Redirects are never followed
automatically: tests assert the 303 and its Location.
"""
from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
SESSION_COOKIE = "llmwiki_session"   # design B4


@dataclass
class SetCookie:
    name: str
    value: str
    attrs: dict            # lower-cased attribute names -> value ("" for flags)
    raw: str

    @property
    def deleted(self) -> bool:
        ma = self.attrs.get("max-age")
        return self.value in ("", '""') or (ma is not None and ma.lstrip("-").isdigit() and int(ma) <= 0) \
            or "1970" in self.attrs.get("expires", "")


def parse_set_cookies(response: httpx.Response) -> list[SetCookie]:
    out = []
    for raw in response.headers.get_list("set-cookie"):
        parts = [p.strip() for p in raw.split(";")]
        name, _, value = parts[0].partition("=")
        attrs = {}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            attrs[k.strip().lower()] = v.strip()
        out.append(SetCookie(name.strip(), value.strip(), attrs, raw))
    return out


def set_cookie(response: httpx.Response, name: str) -> SetCookie | None:
    found = [c for c in parse_set_cookies(response) if c.name == name]
    return found[-1] if found else None


@dataclass
class Form:
    action: str
    method: str
    fields: dict           # name -> value (hidden + text inputs as rendered)
    user_field: str | None
    password_field: str | None
    raw: str


_FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.I | re.S)
_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.I | re.S)
_ATTR_RE = re.compile(r"""([a-zA-Z_:-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")


def _attrs(s: str) -> dict:
    return {m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else
                                              m.group(4) if m.group(4) is not None else m.group(5))
            for m in _ATTR_RE.finditer(s)}


def find_forms(page: str) -> list[Form]:
    forms = []
    for m in _FORM_RE.finditer(page):
        fa = _attrs(m.group("attrs"))
        fields, user_f, pw_f = {}, None, None
        for im in _INPUT_RE.finditer(m.group("body")):
            a = _attrs(im.group("attrs"))
            name, typ = a.get("name"), a.get("type", "text").lower()
            if not name:
                continue
            if typ == "password":
                pw_f = name
                continue
            if typ in ("text", "email") and user_f is None:
                user_f = name
            if typ in ("hidden", "text", "email"):
                fields[name] = a.get("value", "")
        forms.append(Form(fa.get("action", ""), fa.get("method", "get").lower(), fields, user_f, pw_f, m.group(0)))
    return forms


def login_form(page: str) -> Form:
    forms = [f for f in find_forms(page) if f.password_field]
    assert forms, "no form with a password field on the login page"
    return forms[0]


def logout_form(page: str) -> Form | None:
    forms = [f for f in find_forms(page) if "logout" in f.action]
    return forms[0] if forms else None


def decode_session(value: str) -> dict | None:
    """Try to read a cookie as v1.0's signed b64(json) session. v1.1 item 2: the cookie must be an opaque
    random id, so for a conforming implementation this returns None (or something without tokens)."""
    try:
        data = value.strip('"').split(".")[0]
        return json.loads(base64.b64decode(data + "=" * (-len(data) % 4)))
    except Exception:  # noqa: BLE001
        try:
            data = value.strip('"').split(".")[0]
            return json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
        except Exception:  # noqa: BLE001
            return None


def tokens_in(obj) -> list[str]:
    """Every JWT-looking string inside a decoded session dict."""
    out = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += tokens_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out += tokens_in(v)
    elif isinstance(obj, str) and obj.count(".") == 2 and obj.startswith("eyJ"):
        out.append(obj)
    return out


_META_RE = re.compile(r"<meta\b(?P<attrs>[^>]*)>", re.I | re.S)


def csrf_meta(page: str) -> str | None:
    """v1.1 item 3: the session's csrf value is exposed to the dashboard page in a meta tag (name not fixed by
    the design: any <meta name=...csrf...> is accepted)."""
    for m in _META_RE.finditer(page):
        a = _attrs(m.group("attrs"))
        if "csrf" in (a.get("name", "") + a.get("id", "")).lower() and a.get("content"):
            return a["content"]
    return None


def client(base_url: str, source_ip: str | None = None, **kw) -> httpx.Client:
    """httpx client, optionally bound to a loopback source address (127.0.0.x) so the server sees a
    distinct client IP (throttle / client_ip delegation cases)."""
    transport = httpx.HTTPTransport(local_address=source_ip) if source_ip else None
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT, follow_redirects=False,
                        transport=transport, **kw)


@dataclass
class Browser:
    """A minimal browser: manual cookie jar, no automatic redirects, form helpers."""
    base_url: str
    jar: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    source_ip: str | None = None

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self._c = client(self.base_url, self.source_ip)

    def close(self):
        self._c.close()

    def _cookie_header(self) -> dict:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.jar.items())} if self.jar else {}

    def _absorb(self, r: httpx.Response) -> httpx.Response:
        for c in parse_set_cookies(r):
            if c.deleted:
                self.jar.pop(c.name, None)
            else:
                self.jar[c.name] = c.value
        self.history.append(r)
        return r

    def request(self, method: str, path: str, headers: dict | None = None, **kw) -> httpx.Response:
        h = {"Accept": "text/html,application/xhtml+xml"} | self._cookie_header() | (headers or {})
        return self._absorb(self._c.request(method, path, headers=h, **kw))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post_form(self, path: str, data: dict, headers: dict | None = None, **kw):
        h = {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.base_url} | (headers or {})
        return self.request("POST", path, headers=h, content=urlencode(data), **kw)

    def open_login(self, next_path: str | None = None) -> tuple[httpx.Response, Form]:
        r = self.get("/login" + (f"?{urlencode({'next': next_path})}" if next_path is not None else ""))
        assert r.status_code == 200, (r.status_code, r.text[:500])
        return r, login_form(r.text)

    def submit_login(self, username: str, password: str, next_path: str | None = None,
                     drop_fields: tuple = (), override: dict | None = None, headers: dict | None = None):
        _, form = self.open_login(next_path)
        data = dict(form.fields)
        data[form.user_field or "username"] = username
        data[form.password_field] = password
        for f in drop_fields:
            data.pop(f, None)
        data.update(override or {})
        action = form.action or "/login"
        if action.startswith("http"):
            action = urlsplit(action).path + (("?" + urlsplit(action).query) if urlsplit(action).query else "")
        return self.post_form(action, data, headers=headers)

    def submit_login_while_signed_in(self, username: str, password: str) -> httpx.Response:
        """GET /login 303s a signed-in browser (Q-08 behaviour). To POST a login anyway (re-login / Q-10), take
        a fresh login form + its login-CSRF cookie from a cookieless browser and submit it with this jar."""
        tmp = Browser(self.base_url, source_ip=self.source_ip)
        try:
            _, form = tmp.open_login()
            for k, v in tmp.jar.items():
                if k != SESSION_COOKIE:
                    self.jar[k] = v
        finally:
            tmp.close()
        data = dict(form.fields)
        data[form.user_field or "username"] = username
        data[form.password_field] = password
        action = form.action or "/login"
        if action.startswith("http"):
            action = urlsplit(action).path
        return self.post_form(action, data)

    def login(self, username: str, password: str, next_path: str | None = None) -> httpx.Response:
        r = self.submit_login(username, password, next_path)
        assert r.status_code == 303, (r.status_code, r.text[:800])
        assert SESSION_COOKIE in self.jar, f"no {SESSION_COOKIE} cookie after login: {r.headers.get_list('set-cookie')}"
        return r

    @property
    def session_value(self) -> str | None:
        return self.jar.get(SESSION_COOKIE)

    def csrf_cookie_names(self) -> list[str]:
        return [k for k in self.jar if k != SESSION_COOKIE]

    def session_csrf(self) -> str:
        """The session's csrf value from the dashboard meta tag (v1.1 item 3)."""
        r = self.get("/dashboard")
        assert r.status_code == 200, r.status_code
        tok = csrf_meta(r.text)
        assert tok, "dashboard must expose the session csrf value in a <meta> tag (v1.1 item 3)"
        return tok

    def logout(self) -> httpx.Response:
        """Submit the dashboard's logout form (hidden csrf field included, v1.1 item 3)."""
        form = logout_form(self.get("/dashboard").text)
        assert form is not None, "dashboard must show a logout form (B4 'Signed in as ... · Log out')"
        return self.post_form(form.action or "/logout", dict(form.fields))

    def write(self, method: str, path: str, csrf: str | None = None, **kw) -> httpx.Response:
        """A cookie-authenticated API write, optionally with X-CSRF-Token."""
        headers = {"Accept": "application/json"} | kw.pop("headers", {})
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        return self.request(method, path, headers=headers, **kw)


def api(base_url: str, source_ip: str | None = None) -> httpx.Client:
    """A plain API client: no cookies kept, no redirects followed."""
    return client(base_url, source_ip, headers={"Accept": "application/json"})


def resolve_like_browser(base_url: str, location: str) -> str:
    """Resolve a Location header as a browser would: strip tab/CR/LF anywhere and leading/trailing C0+space,
    treat backslash as slash for http(s) (WHATWG URL parser), then join with the base."""
    loc = re.sub(r"[\t\r\n]", "", location).strip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f"
                                                    "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b"
                                                    "\x1c\x1d\x1e\x1f ")
    loc = loc.replace("\\", "/")
    m = re.match(r"^(https?):(?!//)(.*)$", loc, re.I)
    if m and m.group(1).lower() != urlsplit(base_url).scheme.lower():
        loc = f"{m.group(1)}://{m.group(2).lstrip('/')}"   # http base + "https:evil" -> https://evil
    return urljoin(base_url + "/", loc)
