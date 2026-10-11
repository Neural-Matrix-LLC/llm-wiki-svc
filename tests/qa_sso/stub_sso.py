"""A controllable stand-in for the SSO server, for failure modes the real one cannot produce on demand.

    python stub_sso.py --port 8033 --upstream http://127.0.0.1:8030 \
        --signing-key run/state/keys/private.pem --issuer http://127.0.0.1:8030 --pidfile run/pids/stub.pid

Point one llm-wiki instance at it (SSO_BASE_URL=http://127.0.0.1:8033, SSO_ISSUER=<real issuer>); the
default JWKS URL then also goes through the stub, which proxies it. Stdlib + PyJWT/cryptography only.

Per-endpoint modes (POST /__qa/config {"login": "...", "refresh": "...", "logout": "...", ...}):
  proxy (default)       forward to the real SSO
  mint                  login/refresh: mint tokens with the QA signing key (exp = now + access_ttl), with
                        rotation + reuse detection (a refresh token works once, like the real SSO)
  invalid_credentials | account_inactive | rate_limited | server_error | garbage | bad_request
  hang                  sleep hang_seconds (default 15) then answer 504
  close                 drop the connection without a response
  forged                login/refresh: 200 with tokens signed by a random attacker key
  token_401 | token_403 refresh: 401 token_invalid / 403 account_inactive
  invalid_client        401 invalid_client (v1.2 C-19: wrong ServiceClient credentials)
Other knobs: access_ttl (s), refresh_ttl (s), perms (list), retry_after (s), username.
GET /__qa/calls -> {"counts": {path: n}, "last": {path: body}, "auth": {path: {"scheme", "client_id"}}};
POST /__qa/reset clears counters + config. "auth" records how llm-wiki authenticated (v1.1 item 4: HTTP Basic as
its ServiceClient on /login and /refresh); the Basic secret is never recorded.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULTS = {"login": "proxy", "refresh": "proxy", "logout": "proxy", "access_ttl": 900,
            "refresh_ttl": 7 * 86400, "perms": ["application.app1"], "retry_after": 42,
            "hang_seconds": 15, "username": None}
STATE = {"config": dict(DEFAULTS), "counts": {}, "last": {}, "auth": {}, "used_refresh": set(), "minted_refresh": set()}
LOCK = threading.Lock()
ARGS = None
KEY = None
KID = None


def _err(code, error, detail):
    return code, {"error": error, "detail": detail}


def _mint(username, attacker=False):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    cfg = STATE["config"]
    now = int(time.time())
    base = {"iss": ARGS.issuer, "aud": ARGS.audience, "sub": "990001", "username": username,
            "email": f"{username}@example.invalid", "is_staff": False, "groups": [], "perms": cfg["perms"]}
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if attacker else KEY
    access = jwt.encode({**base, "token_type": "access", "jti": uuid.uuid4().hex, "iat": now,
                         "exp": now + int(cfg["access_ttl"])}, key, algorithm="RS256", headers={"kid": KID})
    rjti = uuid.uuid4().hex
    refresh = jwt.encode({"iss": ARGS.issuer, "aud": ARGS.audience, "sub": "990001", "token_type": "refresh",
                          "jti": rjti, "iat": now, "exp": now + int(cfg["refresh_ttl"])}, key,
                         algorithm="RS256", headers={"kid": KID})
    STATE["minted_refresh"].add(rjti)
    user = {"id": 990001, "username": username, "email": base["email"], "groups": [], "perms": cfg["perms"]}
    return 200, {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                 "expires_in": int(cfg["access_ttl"]), "user": user}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # one line per request on stderr
        super().log_message(fmt, *a)

    def _send(self, code, obj=None, raw=None, headers=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if raw is None else "text/html")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _proxy(self, method, raw):
        req = urllib.request.Request(ARGS.upstream + self.path, data=raw if method != "GET" else None, method=method)
        for k in ("Content-Type", "Authorization", "Accept", "Origin", "Cookie"):
            if self.headers.get(k):
                req.add_header(k, self.headers[k])
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data, code, hdrs = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            data, code, hdrs = e.read(), e.code, e.headers
        self.send_response(code)
        for k in ("Content-Type", "Retry-After", "Set-Cookie", "WWW-Authenticate", "Cache-Control"):
            for v in hdrs.get_all(k) or []:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/__qa/calls":
            with LOCK:
                return self._send(200, {"counts": STATE["counts"], "last": STATE["last"], "auth": STATE["auth"],
                                        "config": STATE["config"]})
        self._count(b"")
        return self._proxy("GET", b"")

    def _auth_info(self):
        import base64
        h = (self.headers.get("Authorization") or "").split()
        if not h:
            return {"scheme": None, "client_id": None}
        info = {"scheme": h[0].lower(), "client_id": None}
        if info["scheme"] == "basic" and len(h) == 2:
            try:
                info["client_id"] = base64.b64decode(h[1]).decode().partition(":")[0]
            except Exception:  # noqa: BLE001
                info["client_id"] = "<undecodable>"
        return info

    def _count(self, raw):
        path = self.path.split("?")[0]
        with LOCK:
            STATE["auth"][path] = self._auth_info()
            STATE["counts"][path] = STATE["counts"].get(path, 0) + 1
            try:
                STATE["last"][path] = json.loads(raw or b"null")
            except ValueError:
                STATE["last"][path] = raw.decode(errors="replace")

    def do_POST(self):
        raw = self._body()
        path = self.path.split("?")[0]
        if path == "/__qa/config":
            with LOCK:
                STATE["config"].update(json.loads(raw or b"{}"))
                return self._send(200, STATE["config"])
        if path == "/__qa/reset":
            with LOCK:
                STATE.update(config=dict(DEFAULTS), counts={}, last={}, auth={}, used_refresh=set(), minted_refresh=set())
            return self._send(200, {"ok": True})
        self._count(raw)
        endpoint = {"/api/v1/auth/login": "login", "/api/v1/auth/refresh": "refresh",
                    "/api/v1/auth/logout": "logout"}.get(path)
        mode = STATE["config"].get(endpoint, "proxy") if endpoint else "proxy"
        cfg = STATE["config"]
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if mode == "proxy":
            return self._proxy("POST", raw)
        if mode == "hang":
            time.sleep(float(cfg["hang_seconds"]))
            return self._send(504, {"error": "timeout", "detail": "stub hang"})
        if mode == "close":
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return None
        if mode == "garbage":
            return self._send(200, raw=b"<html>definitely not json</html>")
        if mode == "server_error":
            return self._send(500, {"error": "server_error", "detail": "Internal error."})
        if mode == "bad_request":
            return self._send(400, {"error": "invalid_request", "detail": "stub: bad request",
                                    "fields": {"username": ["stub"]}})
        if mode == "invalid_credentials":
            return self._send(*_err(401, "invalid_credentials", "Invalid username or password."))
        if mode == "account_inactive":
            return self._send(*_err(403, "account_inactive", "Account is inactive."))
        if mode == "rate_limited":
            return self._send(429, {"error": "rate_limited", "detail": f"Too many attempts. Try again in {cfg['retry_after']} s."},
                              headers={"Retry-After": str(cfg["retry_after"])})
        if mode == "invalid_client":  # v1.2 C-19: the SSO refuses our ServiceClient credentials
            return self._send(401, {"error": "invalid_client", "detail": "Invalid client credentials."},
                              headers={"WWW-Authenticate": 'Basic realm="sso-service-client"'})
        if mode == "token_401":
            return self._send(*_err(401, "token_invalid", "Token is invalid or expired."))
        if mode == "token_403":
            return self._send(*_err(403, "account_inactive", "Account is inactive."))
        if mode == "forged":
            return self._send(*_mint(body.get("username") or "qa_forged", attacker=True))
        if mode == "mint":
            if endpoint == "logout":
                return self._send(204, raw=b"")
            if endpoint == "refresh":
                import jwt
                try:
                    pl = jwt.decode(body.get("refresh_token", ""), KEY.public_key(), algorithms=["RS256"],
                                    audience=ARGS.audience, issuer=ARGS.issuer)
                except Exception:  # noqa: BLE001
                    return self._send(*_err(401, "token_invalid", "Token is invalid or expired."))
                with LOCK:
                    if pl.get("jti") in STATE["used_refresh"] or pl.get("jti") not in STATE["minted_refresh"]:
                        return self._send(*_err(401, "token_invalid", "Token is invalid or expired."))
                    STATE["used_refresh"].add(pl["jti"])
                return self._send(*_mint(cfg.get("username") or "qa_wiki_stub"))
            return self._send(*_mint(cfg.get("username") or body.get("username") or "qa_wiki_stub"))
        return self._send(500, {"error": "stub", "detail": f"unknown mode {mode}"})


def main():
    global ARGS, KEY, KID
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8033)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--issuer", required=True)
    ap.add_argument("--audience", default="myfin-services")
    ap.add_argument("--signing-key", required=True, help="the QA SSO's private key (PEM), for mint mode")
    ap.add_argument("--pidfile")
    ARGS = ap.parse_args()
    from cryptography.hazmat.primitives import serialization
    with open(ARGS.signing_key, "rb") as fh:
        KEY = serialization.load_pem_private_key(fh.read(), password=None)
    with urllib.request.urlopen(ARGS.upstream + "/.well-known/jwks.json", timeout=10) as r:
        jwks = json.load(r)
    KID = jwks["keys"][0]["kid"]
    if ARGS.pidfile:
        with open(ARGS.pidfile, "w") as fh:
            fh.write(str(os.getpid()))
    ThreadingHTTPServer((ARGS.host, ARGS.port), H).serve_forever()


if __name__ == "__main__":
    main()
