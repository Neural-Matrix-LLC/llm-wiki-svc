"""One object per Settings that the HTTP layer uses for every auth decision (design B2-B4, v1.1)."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import replace

import httpx
import sso_client

from llmwiki.auth.client import SSOClient
from llmwiki.auth.principal import (
    STATIC_PRINCIPAL,
    AuthError,
    PermissionDenied,
    Principal,
    SSOLoginError,
    SSOMisconfigured,
    SSOUnavailable,
)
from llmwiki.auth.session import (
    COOKIE_NAME,
    CSRF_HEADER,
    LOGIN_CSRF_COOKIE,
    SessionRecord,
    SessionStore,
    csrf_ok,
    new_csrf_token,
)
from llmwiki.auth.sso import (
    JWKSCache,
    SSOVerifier,
    check_permission,
    http_fetch,
    looks_like_jwt,
    principal_from_claims,
)
from llmwiki.config import Settings

logger = logging.getLogger(__name__)

#: Refresh the access token server-side when it expires within this many seconds (design B4).
REFRESH_MARGIN_S = 60
SSO_TIMEOUT_S = 10.0  # login / refresh / logout / JWKS (interactive, rare)


class AuthService:
    """Everything api/ needs, behind one object (api/ may not import llmwiki.auth itself)."""

    session_cookie = COOKIE_NAME
    login_csrf_cookie = LOGIN_CSRF_COOKIE
    csrf_header = CSRF_HEADER
    new_csrf_token = staticmethod(new_csrf_token)
    csrf_ok = staticmethod(csrf_ok)

    def __init__(self, cfg: Settings, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self.mode = cfg.auth_mode
        self.required_permission = cfg.sso_required_permission
        self._http: httpx.Client | None = None
        self._transport = transport
        self._clock = clock
        self._verifier: SSOVerifier | None = None
        self._introspector: sso_client.SSOClient | None = None
        self._store: SessionStore | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def sso_enabled(self) -> bool:
        return self.mode in ("sso", "both")

    @property
    def static_enabled(self) -> bool:
        return self.mode in ("static", "both")

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=SSO_TIMEOUT_S, transport=self._transport)
        return self._http

    @property
    def client(self) -> SSOClient:
        return SSOClient(self.cfg.sso_base_url, self.http, self.cfg.sso_client_id,
                         self.cfg.sso_client_secret.get_secret_value())

    @property
    def verifier(self) -> SSOVerifier:
        if self._verifier is None:
            jwks = JWKSCache(self.cfg.effective_sso_jwks_url, http_fetch(self.http),
                             clock=self._clock)
            self._verifier = SSOVerifier(jwks, self.cfg.sso_issuer, self.cfg.sso_audience)
        return self._verifier

    @property
    def introspector(self) -> sso_client.SSOClient:
        """The SSO permission query (``sso-client``, SSO design v1.2 §4.4): current permissions,
        revocation and client-IP binding. Active answers cached 30 s; fail closed."""
        if self._introspector is None:
            base = self.cfg.sso_base_url.rstrip("/")
            config = sso_client.SSOConfig(
                base_url=base, client_id=self.cfg.sso_client_id,
                client_secret=self.cfg.sso_client_secret.get_secret_value(),
                allow_http=base.startswith("http://"),
                # D-17: every request waits on this; bounded by SSO_INTROSPECT_TIMEOUT_S/_RETRIES
                timeout=self.cfg.sso_introspect_timeout_s,
                connect_timeout=min(1.0, self.cfg.sso_introspect_timeout_s),
                retries=self.cfg.sso_introspect_retries)
            self._introspector = sso_client.SSOClient(config, http=self.http)
        return self._introspector

    def introspect(self, token: str, client_ip: str | None) -> Principal:
        """Ask the SSO about ``token`` as seen from ``client_ip`` and require the permission.

        AuthError: inactive (expired, revoked, suspended, IP mismatch under ENFORCE);
        PermissionDenied: active without ``SSO_REQUIRED_PERMISSION``; SSOUnavailable (fail closed,
        -> 503) / SSOMisconfigured (the SSO refused our ServiceClient)."""
        try:
            result = self.introspector.check(token, client_ip)
        except sso_client.TokenInactive as exc:
            raise AuthError(f"token inactive at the SSO ({exc.reason})") from exc
        except sso_client.NotAuthenticated as exc:
            raise AuthError("no token") from exc
        except sso_client.SSOProtocolError as exc:
            if exc.status_code == 401:
                raise SSOMisconfigured("the SSO refused SSO_CLIENT_ID/SSO_CLIENT_SECRET "
                                       "(invalid_client) on introspect") from exc
            if exc.status_code == 400:  # e.g. a token over the SSO's size limit: not a credential
                raise AuthError(f"SSO rejected the token as malformed ({exc})") from exc
            raise SSOUnavailable(str(exc)) from exc
        except sso_client.SSOUnavailable as exc:
            raise SSOUnavailable(str(exc)) from exc
        user = result.user
        principal = Principal(sub=user.id if user else "", username=user.username if user else "",
                              email=user.email if user else "",
                              is_staff=bool(user and user.is_staff),
                              groups=user.groups if user else (),
                              perms=tuple(sorted(result.permissions)))
        return check_permission(principal, self.required_permission)

    @property
    def store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore(self.cfg.effective_session_db_path,
                                       int(self.cfg.session_max_age_hours * 3600),
                                       idle_s=int(self.cfg.session_idle_hours * 3600))
        return self._store

    def init_sessions(self) -> int:
        """Create the session table if needed and prune expired rows (at start). Returns pruned."""
        self.store.init()
        return self.store.prune()

    # ------------------------------------------------------------------ bearer tokens
    def static_matches(self, supplied: str) -> bool:
        return secrets.compare_digest(supplied, self.cfg.ingest_api_token.get_secret_value())

    def authenticate_bearer(self, token: str, client_ip: str | None = None) -> Principal:
        """v1.1 item 6: a JWT goes to SSO verification (sso/both); in ``both`` a failed
        verification falls back to the constant-time static compare. PermissionDenied (a valid SSO
        token without the permission) never falls back.

        v1.2b: the local JWKS check is a cheap pre-check; the SSO's introspect answer (current
        permissions, revocation, IP binding for ``client_ip``) decides. SSOUnavailable -> 503."""
        if looks_like_jwt(token) and self.sso_enabled:
            try:
                self.verifier.verify(token)
            except AuthError:
                if self.static_enabled and self.static_matches(token):
                    return STATIC_PRINCIPAL
                raise
            return self.introspect(token, client_ip)
        if self.static_enabled and self.static_matches(token):
            return STATIC_PRINCIPAL
        raise AuthError("invalid bearer token")

    # ------------------------------------------------------------------ browser session
    def _verified(self, body: dict) -> tuple[str, str, int, Principal]:
        access = str(body["access_token"])
        refresh = str(body.get("refresh_token") or "")
        if not refresh:
            raise SSOUnavailable("SSO answered without a refresh token")
        claims = self.verifier.claims(access)
        principal = check_permission(principal_from_claims(claims), self.required_permission)
        return access, refresh, int(claims["exp"]), principal

    def login(self, username: str, password: str,
              client_ip: str | None = None) -> tuple[str, SessionRecord, Principal]:
        """SSO password login -> ``(session_id, record, principal)``.

        PermissionDenied: no session is created and the refresh token is discarded at the SSO.
        """
        body = self.client.login(username, password, client_ip)
        try:
            access, refresh, exp, principal = self._verified(body)
        except PermissionDenied:
            self._discard(str(body.get("refresh_token") or ""), client_ip)
            raise
        session_id, rec = self.store.create(username=principal.username or username,
                                            sub=principal.sub, access=access, refresh=refresh,
                                            access_exp=exp)
        return session_id, rec, replace(principal, via="session")

    def _discard(self, refresh_token: str, client_ip: str | None = None) -> None:
        if not refresh_token:
            return
        try:
            self.client.logout(refresh_token, client_ip)
        except (SSOLoginError, SSOUnavailable) as exc:
            log = logger.error if isinstance(exc, SSOMisconfigured) else logger.warning
            log("SSO logout failed (%s); the refresh token expires on its own",
                type(exc).__name__)

    def get_session(self, session_id: str | None) -> SessionRecord | None:
        return self.store.get(session_id)

    def session_principal(self, rec: SessionRecord,
                          client_ip: str | None = None) -> tuple[SessionRecord, Principal]:
        """Principal for a session; rotates its tokens first when the access token is due.

        * refresh refused by the SSO -> row deleted, AuthError;
        * refreshed tokens lack the permission -> row deleted, new refresh token discarded,
          PermissionDenied (v1.1 Q-06);
        * SSO unreachable / 5xx -> row kept; the current access token is used while still valid,
          else SSOUnavailable (-> 503, v1.1 Q-05).
        """
        if rec.access_expires_within(REFRESH_MARGIN_S):
            denied: list[PermissionDenied] = []

            def do_refresh(current: SessionRecord) -> tuple[str, str, int]:
                body = self.client.refresh(current.refresh, client_ip)
                try:
                    access, refresh, exp, _ = self._verified(body)
                except PermissionDenied as exc:
                    self._discard(str(body.get("refresh_token") or ""), client_ip)
                    denied.append(exc)
                    raise
                return access, refresh, exp

            try:
                fresh, _ = self.store.refresh_locked(rec.id_hash, rec.refresh,
                                                     REFRESH_MARGIN_S, do_refresh)
            except SSOLoginError as exc:
                self.store.delete(rec.id_hash)
                raise AuthError(f"session refresh refused ({exc.code})") from exc
            except PermissionDenied:
                self.store.delete(rec.id_hash)
                raise
            except SSOUnavailable as exc:
                if isinstance(exc, SSOMisconfigured):
                    logger.error("session refresh: %s", exc)
                if rec.access_expires_within(0):
                    raise
                logger.warning("SSO unreachable for a session refresh; "
                               "using the current access token")
                fresh = rec
            except AuthError:
                self.store.delete(rec.id_hash)  # the SSO handed out a token we cannot verify
                raise
            if fresh is None:
                raise AuthError("session ended")
            rec = fresh
        # v1.2b: local pre-check, then the SSO decides (current permissions, revocation, IP
        # binding for the browser's IP). SSO down -> SSOUnavailable (fail closed: 503, session kept)
        try:
            self.verifier.verify(rec.access)
            principal = self.introspect(rec.access, client_ip)
        except PermissionDenied:
            self.store.delete(rec.id_hash)
            raise
        except SSOUnavailable:
            raise
        except AuthError:
            self.store.delete(rec.id_hash)  # revoked / IP mismatch under ENFORCE: session over
            raise
        return rec, replace(principal, via="session")

    def logout(self, rec: SessionRecord | None, client_ip: str | None = None) -> None:
        """Ends the session; the SSO logout carries the browser's ``client_ip`` (fixes D11)."""
        if rec is not None:
            self.store.delete(rec.id_hash)
            self._discard(rec.refresh, client_ip)

    def retire(self, old: SessionRecord | None, new: SessionRecord,
               client_ip: str | None = None) -> None:
        """Session rotation after a successful login: delete the session the browser had before
        and discard its refresh token at the SSO, best effort. Never raises - nothing here may
        block the login that already succeeded."""
        if old is None or old.id_hash == new.id_hash:
            return
        try:
            self.store.delete(old.id_hash)
        except Exception:  # pragma: no cover - SQLite failure; the row expires on its own
            logger.exception("could not delete the previous session of %s", old.username)
        try:
            self._discard(old.refresh, client_ip)  # logs SSOLoginError / SSOUnavailable itself
        except Exception as exc:
            logger.warning("SSO logout of the previous session failed (%s)", type(exc).__name__)
        logger.info("session rotated on login: previous session of %s deleted", old.username)

    def session_max_age(self, rec: SessionRecord) -> int:
        return self.store.remaining_s(rec)
