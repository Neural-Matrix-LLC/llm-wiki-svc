"""Server-to-server calls to the SSO (design B4): login (native client), refresh, logout.

Tokens never reach browser JavaScript: this service holds them in its signed session cookie.
"""

from __future__ import annotations

import httpx

from llmwiki.auth.principal import SSOLoginError, SSOUnavailable

LOGIN_PATH = "/api/v1/auth/login"
REFRESH_PATH = "/api/v1/auth/refresh"
LOGOUT_PATH = "/api/v1/auth/logout"


class SSOClient:
    def __init__(self, base_url: str, client: httpx.Client) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = client

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
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

    def login(self, username: str, password: str) -> dict:
        """``POST /api/v1/auth/login`` with ``client: native`` -> access + refresh token."""
        return self._json(self._post(LOGIN_PATH, {"username": username, "password": password,
                                                  "client": "native"}))

    def refresh(self, refresh_token: str) -> dict:
        """``POST /api/v1/auth/refresh`` -> a rotated token pair."""
        return self._json(self._post(REFRESH_PATH, {"refresh_token": refresh_token}))

    def logout(self, refresh_token: str) -> None:
        """``POST /api/v1/auth/logout``: blacklists the refresh token. Best effort."""
        response = self._post(LOGOUT_PATH, {"refresh_token": refresh_token})
        if response.status_code not in (200, 204):
            raise self._error(response)
