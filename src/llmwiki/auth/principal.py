"""Who is calling, and the errors authentication can end in (design B2/B3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. ``via="static"`` is the ``INGEST_API_TOKEN`` holder."""

    sub: str
    username: str
    email: str = ""
    is_staff: bool = False
    groups: tuple[str, ...] = field(default_factory=tuple)
    perms: tuple[str, ...] = field(default_factory=tuple)
    via: Literal["sso", "static", "session"] = "sso"

    def has_perm(self, perm: str) -> bool:
        return self.via == "static" or perm in self.perms


#: The principal for a caller holding the static token: all routes, no SSO identity.
STATIC_PRINCIPAL = Principal(sub="static", username="static-token", via="static")


class AuthError(Exception):
    """Missing, malformed, expired or otherwise invalid credentials -> 401."""


class PermissionDenied(Exception):
    """Valid identity without the required permission -> 403 (never 401: no refresh loop)."""

    def __init__(self, permission: str, principal: Principal | None = None) -> None:
        super().__init__(f"missing permission {permission}")
        self.permission = permission
        self.principal = principal


class SSOUnavailable(Exception):
    """The SSO server could not be reached or answered with a 5xx / non-JSON body."""


class SSOMisconfigured(SSOUnavailable):
    """The SSO refused this app's ServiceClient credentials (401 ``invalid_client``, design v1.2
    C-19): an operator error. Handled like an outage (sessions kept, 503) but logged as an error
    and shown as "Login service misconfigured"."""


class SSOLoginError(Exception):
    """The SSO server refused a login/refresh: ``code`` is its ``error`` field (spec §6.8)."""

    def __init__(self, code: str, detail: str = "", status: int = 400,
                 retry_after: int | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.status = status
        self.retry_after = retry_after
