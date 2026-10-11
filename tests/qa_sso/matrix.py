"""AUTH_MODE matrix (design B1/B3/B4/B5): route probes and the expected outcome per mode x route x credential.

"allow" means: authenticated/authorised - the route's own answer (the probe's exact status where the probe
pins one, else anything except 401/403/303/5xx). Probes on protected routes are chosen so that an allowed
request fails validation *after* auth (422) or hits a missing object (404): no LLM cost, no state change.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOW = "allow"


@dataclass(frozen=True)
class Probe:
    method: str
    path: str
    kwargs: tuple = ()          # tuple of (key, value) for httpx.request
    allowed_status: int | None = None   # exact status when allowed; None = any non-401/403/303/5xx
    deny_only: bool = False     # never send an authorised request (side effects / cost)

    def kw(self) -> dict:
        return {k: v for k, v in self.kwargs}

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


PROTECTED = [  # require_token today (snapshots/static_routes_bca2bef.json)
    Probe("GET", "/usage", (), 200),
    Probe("GET", "/usage", (("params", {"month": "not-a-month"}),), 422),
    Probe("POST", "/ingest", (("json", {}),), 422),
    Probe("POST", "/upload", (), 422),
    Probe("POST", "/feedback", (("json", {}),), 422),
    Probe("PUT", "/domains/qa-sso-probe", (("json", {"description": ["not", "a", "string"]}),), 422),
    Probe("DELETE", "/domains/qa-sso-no-such-domain", (), 404),
    Probe("POST", "/lint", (("params", {"dry_run": "notabool"}),), 422),
    Probe("POST", "/compile/qa-no-such-source", (("params", {"force": "notabool"}),), 422),
    Probe("POST", "/worker/resume", (), None, True),
    Probe("POST", "/synthesize/qa-sso-no-such-domain", (), None, True),
]

READ = [  # open today; protected only with SSO_PROTECT_READS=true
    Probe("GET", "/domains", (), 200),
    Probe("GET", "/concepts", (), 200),
    Probe("GET", "/worker", (), 200),
    Probe("GET", "/search", (), 422),            # q missing -> 422 when the route is reachable
    Probe("GET", "/answer", (), 422),            # q missing -> 422 (never runs the LLM)
    Probe("GET", "/page/qa-sso-no-such-page", (), 404),
    # a well-formed but unknown id: a malformed one is a pre-existing 500 (ValueError in storage/layout.py
    # _check_id, also at bca2bef; reported as PRE-01, not an SSO issue)
    Probe("GET", "/sources/00000000000000aa", (), None),
]

HEALTH = [Probe("GET", "/healthz", (), 200)]

MCP_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "qa-sso", "version": "0"}}}
MCP = [Probe("POST", "/mcp/", (("json", MCP_INIT),
                               ("headers", {"Accept": "application/json, text/event-stream"})), 200)]

DASHBOARD = [Probe("GET", "/dashboard", (), 200)]

# credential name -> category
CREDS = {
    "none": "none",
    "static_ok": "static_ok",
    "static_wrong": "static_bad",
    "static_query_ok": "static_query_ok",     # ?token=<INGEST_API_TOKEN> (dashboard only)
    "basic_scheme": "static_bad",             # Authorization: Basic <...>
    "sso_direct": "sso_ok",
    "sso_group": "sso_ok",
    "sso_super": "sso_ok",
    "sso_none": "sso_noperm",
    "sso_staff": "sso_noperm",
    "sso_other_app": "sso_noperm",
    "sso_expired": "sso_bad",
    "sso_wrong_aud": "sso_bad",
    "sso_wrong_iss": "sso_bad",
    "sso_attacker_key": "sso_bad",
    "sso_alg_none": "sso_bad",
    "sso_hs256_confusion": "sso_bad",
    "sso_refresh_token": "sso_bad",
    "sso_tampered_perms": "sso_bad",           # perms added to a no-perm token, old signature kept
    "garbage_jwt": "sso_bad",
    "session_ok": "session_ok",                # browser session cookie (login through this instance)
}


def _accepts(mode: str) -> tuple[bool, bool]:
    return mode in ("static", "both"), mode in ("sso", "both")


def expect(mode: str, protect_reads: bool, kind: str, cred: str):
    """-> ALLOW | tuple of acceptable statuses | ("skip", reason).  Design v1.0 + v1.1 changes."""
    cat = CREDS[cred]
    static_on, sso_on = _accepts(mode)
    if kind == "healthz":
        if cat == "static_query_ok":
            return ("skip", "n/a: ?token= exists only on /dashboard")
        return ALLOW
    if cat == "session_ok" and mode == "static":
        return ("skip", "n/a: no browser session in AUTH_MODE=static (v1.1 item 5: no /login)")
    if cat == "static_query_ok" and kind != "dashboard":
        return ("skip", "n/a: ?token= exists only on /dashboard")
    if kind == "read":
        if not protect_reads:
            return ALLOW
        kind = "protected"      # v1.1 item 5: in static mode PROTECT_READS requires the static token
    if kind == "protected":
        return {
            "none": (401,), "static_bad": (401,), "sso_bad": (401,),
            "static_ok": ALLOW if static_on else (401,),
            "sso_ok": ALLOW if sso_on else (401,),
            # v1.1 item 6: a VERIFIED token without the permission is 403 in both mode (no fallback);
            # in static mode it is just a wrong static token
            "sso_noperm": (403,) if sso_on else (401,),
            "session_ok": ALLOW,    # sent with X-CSRF-Token (v1.1 item 3); CSRF itself: test_qasso_csrf.py
        }[cat]
    if kind == "dashboard":
        if cred == "basic_scheme" and mode != "static":
            return (303,)   # v1.2 Q-31 is about an explicit *Bearer*; a Basic header is no credential -> /login
        if mode == "static":
            return {"none": (401,), "static_bad": (401,), "sso_bad": (401,), "sso_ok": (401,),
                    "sso_noperm": (401,), "static_ok": ALLOW, "static_query_ok": ALLOW}[cat]
        # v1.1 item 7 (Q-07): explicit invalid Bearer -> 401; no credentials at all -> 303 /login
        return {
            "none": (303,),
            "static_query_ok": ALLOW if static_on else (303,),     # ?token= disabled in sso mode = no credential
            "static_ok": ALLOW if static_on else (401,),
            "static_bad": (401,), "sso_bad": (401,),
            "sso_ok": ALLOW, "sso_noperm": (403,), "session_ok": ALLOW,
        }[cat]
    if kind == "mcp":
        if cat == "static_query_ok":
            return ("skip", "n/a: ?token= exists only on /dashboard")
        if mode == "static":
            if not protect_reads:
                return ALLOW        # v1.1 item 1: unchanged (open) in static mode, startup WARNING only
            # v1.2 C-12: static + SSO_PROTECT_READS=true -> the static token on /mcp too (Phase 0 401)
            return ALLOW if cat == "static_ok" else (401,)
        if cat == "session_ok":
            return (401,)           # Developer's choice (api/session.py MCPAuthMiddleware): Bearer only on /mcp
        # v1.1 item 1: same rules as require_auth (sso: SSO token + perm; both: SSO token or static token)
        return {"none": (401,), "static_bad": (401,), "sso_bad": (401,),
                "static_ok": ALLOW if static_on else (401,),
                "sso_ok": ALLOW, "sso_noperm": (403,)}[cat]
    raise ValueError(kind)
