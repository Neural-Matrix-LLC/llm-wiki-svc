"""Server-to-server calls to the SSO (design B4): login (native client), refresh, logout.

Tokens never reach the browser: this service keeps them in its server-side session store.
Login and refresh authenticate as this app's ServiceClient (HTTP Basic ``SSO_CLIENT_ID`` /
``SSO_CLIENT_SECRET``) and pass the browser's IP as ``client_ip``, so the SSO's per-IP throttle
applies to the end user, not to this server (design v1.1 item 4).
"""

from __future__ import annotations

import httpx

from llmwiki.auth.principal import SSOLoginError, SSOMisconfigured, SSOUnavailable

LOGIN_PATH = "/api/v1/auth/login"
REFRESH_PATH = "/api/v1/auth/refresh"
LOGOUT_PATH = "/api/v1/auth/logout"


class SSOClient:
    def __init__(self, base_url: str, client: httpx.Client, client_id: str = "",
                 client_secret: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.http = client
        self.auth: httpx.BasicAuth | None = (
            httpx.BasicAuth(client_id, client_secret) if client_id and client_secret else None)

    def _post(self, path: str, body: dict, client_ip: str | None = None) -> httpx.Response:
        if client_ip and self.auth:
            body = {**body, "client_ip": client_ip}
        try:
            if self.auth is not None:
                return self.http.post(self.base_url + path, json=body, auth=self.auth,
                                      headers={"Accept": "application/json"})
            return self.http.post(self.base_url + path, json=body,
                                  headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise SSOUnavailable(f"{type(exc).__name__} calling the SSO") from exc

    @staticmethod
    def _error(response: httpx.Response) -> Exception:
        if response.status_code >= 500:
            return SSOUnavailable(f"SSO answered {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if body.get("error") == "invalid_client":
            return SSOMisconfigured("the SSO refused SSO_CLIENT_ID/SSO_CLIENT_SECRET "
                                    "(invalid_client)")
        retry_after: int | None = None
        raw = response.headers.get("Retry-After")
        if raw and raw.strip().isdigit():
            retry_after = int(raw.strip())
        return SSOLoginError(str(body.get("error") or f"http_{response.status_code}"),
                             str(body.get("detail") or ""), response.status_code, retry_after)

    def _json(self, response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise self._error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise SSOUnavailable("SSO answered non-JSON") from exc
        if not isinstance(body, dict) or not body.get("access_token"):
            raise SSOUnavailable("SSO answered without an access token")
        return body

    def login(self, username: str, password: str, client_ip: str | None = None) -> dict:
        """``POST /api/v1/auth/login`` with ``client: native`` -> access + refresh token."""
        return self._json(self._post(LOGIN_PATH, {"username": username, "password": password,
                                                  "client": "native"}, client_ip))

    def refresh(self, refresh_token: str, client_ip: str | None = None) -> dict:
        """``POST /api/v1/auth/refresh`` -> a rotated token pair."""
        return self._json(self._post(REFRESH_PATH, {"refresh_token": refresh_token}, client_ip))

    def logout(self, refresh_token: str, client_ip: str | None = None) -> None:
        """``POST /api/v1/auth/logout``: blacklists the refresh token. Best effort. Passes the
        browser's ``client_ip`` like login/refresh (SSO ARCHITECTURE D11)."""
        response = self._post(LOGOUT_PATH, {"refresh_token": refresh_token}, client_ip)
        if response.status_code not in (200, 204):
            raise self._error(response)
