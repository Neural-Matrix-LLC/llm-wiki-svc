# llm-wiki-svc SSO login: QA results (test round 1, design v1.2)

> **Status: COMPLETE** (2026-10-10 22:20 PT). Plan: `SSO-LOGIN-TEST-PLAN.md` v1.2. Raw evidence (gitignored):
> `tests/qa_sso/run/logs/` (junit in `run/logs/junit/`).

## Progress log (America/Phoenix)

| Time | Phase | State | Evidence |
|---|---|---|---|
| 21:05 | SHAs + tree: llm-wiki `feat/sso-login` @ 22e77e5, sso-server `feat/app-permissions` @ bae173b; only untracked `docs/qa/`, `tests/qa_sso/`; design copies identical | done | `git status` |
| 21:10 | v1.2 alignment of plan + tests | done (tests); plan text update pending | `tests/qa_sso/` |
| 21:17 | Existing suite 22e77e5 vs bca2bef (worktrees `/workspace/wt/`, own venvs, Python 3.11.14) | done | `run/logs/junit/existing-*.xml`, `run/logs/existing/*.txt` |
| 21:51 | qa_sso Python 3.11 functional (re-run after triage fixes) + restart | done: 1030 pass / 1 fail / 164 skip / 1 xfail; restart 2/2 | `run/logs/junit/py3.11-{functional,restart}.xml` |
| 21:54 | qa_sso Python 3.11 throttle phase | done: 22 pass / 0 fail | `run/logs/junit/py3.11-throttle.xml` |
| 21:54 | qa_sso Python 3.13 functional | **interrupted** after ~1 min (no junit written); run chain + stack stopped by PID at 21:56, ports 8030-8042 clear, git clean in both repos | `run/logs/py3.13-interrupted/` |
| 22:15 | qa_sso Python 3.13 functional + restart (re-run from scratch) | done: 1030 pass / 1 fail / 164 skip / 1 xfail; restart 2/2 | `run/logs/junit/py3.13-{functional,restart}.xml` |
| 22:18 | qa_sso Python 3.13 throttle phase | done: 22 pass / 0 fail | `run/logs/junit/py3.13-throttle.xml` |
| 22:19 | All QA processes stopped by recorded PID; ports 8030-8042 free | done | `run/pids/` empty |

**Redone because of the interruption:** only the Python 3.13 functional phase. It had been running for about
1 minute (no junit written yet) when the session was cut; I stopped that run chain and the 3.13 stack by PID and
re-ran 3.13 functional + restart + throttle from a wiped state dir. The 3.11 phases and the existing-suite runs had
intact junit and logs, so they were not repeated. Partial logs are kept in `run/logs/py3.13-interrupted/`.

## 1. Versions under test

| | |
|---|---|
| llm-wiki-svc | `feat/sso-login` @ **22e77e5** (clean; only QA's untracked `docs/qa/`, `tests/qa_sso/`) |
| sso-server | `feat/app-permissions` @ **bae173b** (clean) |
| Design | `docs/sso-integration-design.md` with v1.1 changes and v1.2 clarifications; the copies in the two repos are byte-identical |
| Interpreters (from `/proc/PID/exe` of every recorded server PID) | 3.11 run: CPython **3.11.14** for all 11 processes (2 SSO gunicorn, stub, 8 uvicorn) and pytest. 3.13 run: CPython **3.13.16** for all 11 and pytest, also after the SESS-19/20 restart. Venvs: `run/venvs/{sso,wiki}-py3.1x` |
| Ports | 8030-8042 only (8038 unused on purpose = "SSO down", 8039 = throw-away startup processes). Every process stopped by recorded PID |
| DBs | Throw-away: `run/state-pyX.Y/` wiped at each stack start (SSO SQLite `sso-qa_sso-test.sqlite3`, session DBs, keys) |
| `SSO_SUPERUSER_ALL_APPS` | Tested **both on and off** in every run: SSO :8030 (default, true) and a second SSO :8040 on the same DB with `false` (PERM-17..20; all pass). The decision is still with Thomas |

## 2. qa_sso results per area

The 3.11 and 3.13 results are the same, case for case. "skip" counts matrix n/a cells, phase-gated cases and the
remaining by-design skips (LOGOUT-07 Q-15, SESS-21 manual hourly prune). JWTV has one xfail (F-01).

| Area | 3.11 pass / fail / skip | 3.13 pass / fail / skip |
|---|---|---|
| PERM + APPCMD + PREF (SSO side) | 77 / 0 / 0 | 77 / 0 / 0 |
| AUTH_MODE matrix | 495 / 0 / 135 | 495 / 0 / 135 |
| JWT verification | 48 / 0 / 1 (xfail) | 48 / 0 / 1 (xfail) |
| Static fallback (D-09) | 29 / 0 / 0 | 29 / 0 / 0 |
| 401 vs 403 | 16 / 0 / 0 | 16 / 0 / 0 |
| Login page | 28 / 0 / 0 | 28 / 0 / 0 |
| SSO failure mapping (stub) | 12 / 0 / 0 | 12 / 0 / 0 |
| Open redirect | 38 / 0 / 0 | 38 / 0 / 0 |
| CSRF | 58 / 0 / 7 | 58 / 0 / 7 |
| Cookies | 10 / 0 / 0 | 10 / 0 / 0 |
| Sessions (incl. restart phase) | 36 / 0 / 1 | 36 / 0 / 1 |
| Logout | 10 / 0 / 1 | 10 / 0 / 1 |
| MCP / scripts | 27 / 0 / 0 | 27 / 0 / 0 |
| Static regression | 99 / **1** / 0 | 99 / **1** / 0 |
| Startup | 43 / 0 / 0 | 43 / 0 / 0 |
| Logs | 6 / 0 / 0 | 6 / 0 / 0 |
| Throttle (separate phase, spec rates) | 20 / 0 / 0 | 20 / 0 / 0 |
| **Total** | **1,052 / 1 / 165** | **1,052 / 1 / 165** |

### 2.1 Failures

**F-02, REG-14b, Low: the `/mcp` 401 body in static + `SSO_PROTECT_READS=true` is not byte-identical to the Phase 0 401.**
`src/llmwiki/api/session.py:222` (`MCPAuthMiddleware`) builds the body with `json.dumps(body)`. That adds a space
after the colon, while FastAPI's `JSONResponse` is compact. v1.2 C-12 says "with the Phase 0 401 body".
```bash
# instance: AUTH_MODE=static SSO_PROTECT_READS=true (QA port 8041)
curl -s -X POST http://127.0.0.1:8041/mcp/ -H 'Accept: application/json, text/event-stream' \
     -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
#   actual:   {"detail": "invalid or missing bearer token"}      (45 bytes)
curl -s -X POST http://127.0.0.1:8041/ingest -H 'Content-Type: application/json' -d '{"text":"x"}'
#   expected: {"detail":"invalid or missing bearer token"}       (44 bytes, same as every static route)
```
The status and the JSON value are correct. Only clients that compare bytes or Content-Length see the difference.
Fix: `json.dumps(body, separators=(",", ":"))`, or render the body through `JSONResponse`.

### 2.2 Informational / pre-existing (not failures of this round)

| ID | Sev | Finding |
|---|---|---|
| F-01 | Info | JWTV-03 (xfail): an access token with a numeric *string* `exp` (`"1791692880"`) is accepted. PyJWT 2.15 runs `int()` on it, while RFC 7519 asks for a JSON number. Only the holder of the SSO signing key can mint such a token, so there is no attack path. `src/llmwiki/auth/sso.py:111` (`jwt.decode`) |
| PRE-01 | Low | Pre-existing at bca2bef and not SSO-related: `GET /sources/<malformed id>` returns **500**, from a `ValueError` in `storage/layout.py:141` `_check_id` reached through `routes.py:324`. Repro: `curl -i http://127.0.0.1:8035/sources/qa-no-such-source` → 500 (expected 404/422). The matrix probe now uses a well-formed unknown id |
| PRE-02 | Low | Pre-existing Phase 0 behaviour: `/dashboard?token=<INGEST_API_TOKEN>` puts the static token in the URL, and uvicorn's access log prints it (`"GET /dashboard?token=… HTTP/1.1" 200`). The SSO work adds no new token leak: LOG-01..06 pass, ignoring request lines that the harness itself sent with `?token=`. Suggest dropping `?token=` once sso/both is the default |
| OBS-1 | Info | With `CLIENT_IP_HEADER` empty, uvicorn's own `--proxy-headers` default trusts `X-Forwarded-For` from **127.0.0.1**. A request from 127.0.0.1 with `X-Forwarded-For: 198.51.100.66` reached the SSO as `client_ip=198.51.100.66`; the same header from 127.0.0.80 was ignored (SSO saw 127.0.0.80). This is documented in README §auth and harmless in the compose deployment, where the cloudflared peer is not 127.0.0.1. Anyone who runs a host-local reverse proxy should know about it |

Triage fixes to QA's own tests during this round: these were test bugs, not product bugs. Session-store guard path,
an HTML-escaped apostrophe in the no-permission message, the echoed username in LOGIN-06, re-login needing a fresh
login form (GET /login redirects a signed-in user), SESS-08b keeping a 2 s refreshed TTL (which forced a refresh on
every request), an httpx trailing-space header, and Basic-vs-Bearer semantics. The 3.11 functional phase was re-run
after these fixes. The table above shows the re-run.

## 3. Existing llm-wiki suite: 22e77e5 vs base bca2bef

Run in clean worktrees `/workspace/wt/llm-wiki-{22e77e5,bca2bef}` with their own venvs (`uv sync --frozen --extra dev`,
Python 3.11.14), `env -i` (no `.env`), default `-m 'not integration'`.

| | passed | failed | skipped | deselected |
|---|---|---|---|---|
| 22e77e5 | 1001 | 4 | 1 | 30 |
| bca2bef | 798 | 4 | 1 | 7 |

The same 4 tests fail on both: `test_channels.py::test_email_webhook_ingests_bare_url_body`,
`::test_email_webhook_ingests_an_attachment_and_the_cover_note`,
`::test_email_bracket_prefix_is_an_explicit_domain_and_stripped_from_the_title`, and
`test_tools_and_mcp.py::test_mcp_is_reachable_at_slash_mcp`. The error is the same on both:
`RuntimeError: missing required configuration: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL`, raised
from `factory._build_object_store` through `compile_worker`. The Developer's "needs R2/cloud env" describes the
symptom. **The root cause is test isolation, and it predates this branch:** the tests monkeypatch
`llmwiki.config.settings` with a local/memory `Settings`, but `factory.py:36` binds
`from llmwiki.config import settings as default_settings` at import time, so the ingest worker still builds from the
process-env defaults (`storage_backend="r2"`). With `STORAGE_BACKEND=local` the next failure is `CF_ACCOUNT_ID`.
With the full offline env it is `openrouter not active`. These tests would only pass with real cloud credentials,
i.e. by talking to real services. Not SSO-related. Worth a ticket.

## 4. The Developer's choices beyond the design

| Choice | Verified behaviour | Verdict |
|---|---|---|
| `/mcp` takes **Bearer only** | A valid session cookie (with or without X-CSRF-Token) gives **401** `{"detail":"invalid or missing credentials"}` + `WWW-Authenticate: Bearer error="invalid_token"`. Never a 303. Cookie + valid Bearer gives 200 (MCP-10, CSRF-21, MATRIX mcp `session_ok`). In static + PROTECT_READS, `/mcp` needs the static token (C-12) | **Accept.** The design said "require_auth's rules", which would include the cookie. Leaving the cookie out removes any cross-site/CSRF surface on a JSON-RPC endpoint that has write tools, and MCP clients are not browsers. Please add one line to the design so docs and code agree. Only blemish: F-02 bytes |
| `CLIENT_IP_HEADER` needs `TRUSTED_PROXY_IPS` | sso/both: header without a trusted list → exit 1 with a `TRUSTED_PROXY_IPS` message; an unparsable CIDR → exit 1 (START-12). static ignores it (START-12b). With `TRUSTED_PROXY_IPS=127.0.0.70/32`: from the trusted peer, XFF is used, read right to left and skipping trusted hops; a missing or malformed header falls back to the peer. From an untrusted peer the header is ignored and the SSO sees the peer IP (START-14, 6 cases) | **Accept, matches v1.2 C-11 and is the safe failure mode.** Note OBS-1 (uvicorn's own 127.0.0.1 trust when the header is unset) |
| `last_seen` written at most once a minute | No write while `last_seen` < 60 s old (3 requests leave the aged value unchanged). At ≥ 60 s it is rewritten to now (±5 s). `created_at` never moves (SESS-22). Logout right after a skipped write kills a copied cookie at once; a session with a fresh `last_seen` still dies at the 168 h absolute limit (SESS-23). Idle 12 h works at the minute granularity (SESS-17 under/over) | **Accept.** The cost is at most 60 s of idle-timeout imprecision against a 12 h limit. Code: `auth/session.py:179` |

## 5. Rechecks

| Item | Status |
|---|---|
| **D-08** (`tests/unit/test_layering.py`) | **Resolved.** The file is byte-identical to bca2bef (reverted in 9f6a15a). It passes on both SHAs (88 cases at 22e77e5 vs 81 at base; new auth modules are picked up automatically). REG-10 passes |
| **D-16** (session DB file mode) | **Resolved.** The DB and its `-wal`/`-shm` files are 0600 under umask 022 (SESS-04). A directory the app creates is 0700 (START-13b). A pre-existing DB with 0644/0640/0604 makes the app refuse to start, with a `chmod 600` hint (START-13). Code: `auth/session.py:99-109` |
| **C-11** (client IP behind a proxy) | **Resolved per v1.2.** See §4 row 2. For production, set `CLIENT_IP_HEADER=cf-connecting-ip` + `TRUSTED_PROXY_IPS=<cloudflared container net>`. Manual THR-M1 (a real tunnel) is still open |

All other v1.2 answers are implemented as specified and pass on both Pythons: C-12 (except the F-02 bytes),
C-13/Q-30 (403 `{"error":"csrf_failed"}`, HEAD/OPTIONS unchecked), C-14 (503 at login/refresh), C-19 (SSO 401
`invalid_client` even with the correct password; llm-wiki logs an ERROR and returns 503 "Login service
misconfigured"), Q-26 (Max-Age = remaining lifetime), Q-27 (idle 12 h), Q-28 (400 `invalid_request`), Q-31, Q-14.

## 6. Blocked / not covered

- THR-M1 (real cloudflared tunnel), COOKIE-M1 (real browsers), the hourly prune (SESS-21; covered on start by
  SESS-20) and the rest of the manual checklist (24 items) were not run.
- The existing suite was run on 3.11 only (the task asked for 22e77e5 vs bca2bef, not per Python version).

## 7. Open questions needing a decision

1. **`SSO_SUPERUSER_ALL_APPS` default** (Thomas). Both settings work (PERM-17..20).
2. Q-08: `GET /login` while signed in now gives 303 to the sanitised `next`. Confirm.
3. Q-10: a failed re-login keeps the existing session. Confirm.
4. Q-15: "log out everywhere" in llm-wiki (SSO `/logout-all`)?
5. Q-20: `Cache-Control: no-store` on `/login` and `/dashboard` (recommended).
6. Q-21: should llm-wiki rate-limit `/login` itself, or rely on the SSO throttle only?
7. Q-32: session DB location for non-local storage backends (default is `{LOCAL_STORAGE_PATH}/sessions.sqlite3`).
8. Q-12, Q-19, Q-23: refresh-token `perms`, status/format of failed-login and HTML 403 pages (not pinned).
9. Design text for the `/mcp` Bearer-only choice and the 60 s `last_seen` granularity (doc only).
10. PRE-01, PRE-02 and the factory `default_settings` test-isolation issue: ticket them separately (outside SSO scope).

---

# Regression @ 6520890

> **Status: COMPLETE** (2026-10-10, 23:26 PT). Progress log at the end of this section.

## Summary

- **llm-wiki-svc** `feat/sso-login` @ **6520890**, clean apart from QA files; 01f43a8 (our round-1 commit) is an ancestor.
- **sso-server** `feat/app-permissions` @ **03516ce**, clean; `bae173b..03516ce` = 1 file, `docs/sso-integration-design.md` (+13), docs only.
- **Doc drift (Info):** the two copies of `sso-integration-design.md` differ in one bullet. The llm-wiki copy (current)
  has the "successful login rotates the session" note; the sso-server copy lacks it and instead says "only a successful
  login sets a new session cookie (Q-10)". The behaviour matches the llm-wiki copy.
- Interpreters checked: every server process (SSO x2, stub, 9 llm-wiki instances) ran on CPython 3.11.14 in the 3.11 run
  and 3.13.16 in the 3.13 run, including after the restart test (`run/logs/py*/interpreters*.txt`). pytest used the same
  versions. Ports 8030-8042 only; stopped by recorded PID; all ports free afterwards; throwaway SQLite DBs under `run/state-py*`.

### qa_sso counts (pass / fail / skip; skip includes 1 xfail, JWTV-03)

| Area (file) | py3.11 | py3.13 |
|---|---|---|
| 401_403 (SEM) | 16/0/0 | 16/0/0 |
| auth_matrix | 495/0/135 | 495/0/135 |
| cookies | 10/0/0 | 10/0/0 |
| csrf | 58/0/7 | 58/0/7 |
| fallback (FB) | 29/0/0 | 29/0/0 |
| jwt_verification | 48/0/1 | 48/0/1 |
| login | 28/0/0 | 28/0/0 |
| login_sso_errors | 12/0/0 | 12/0/0 |
| logout | 10/0/1 | 10/0/1 |
| mcp_static | 27/0/0 | 27/0/0 |
| open_redirect | 38/0/0 | 38/0/0 |
| **r2_fixes (NS/RED/ROT/SID, new)** | **45/6/0** | **45/6/0** |
| session (incl. 2 restart) | 36/0/1 | 36/0/1 |
| sso_app_perm_command | 23/0/0 | 23/0/0 |
| sso_perms (SSO_SUPERUSER_ALL_APPS on and off) | 47/0/0 | 47/0/0 |
| sso_refresh_perms | 7/0/0 | 7/0/0 |
| startup | 43/0/0 | 43/0/0 |
| static_regression (functional + throttle phase) | 100/0/0 + 2 | 100/0/0 + 2 |
| zy_logs | 6/0/0 | 6/0/0 |
| zz_throttle (throttle phase) | 20/0/0 | 20/0/0 |
| **Total** | **1,100 / 6 / 165** | **1,100 / 6 / 165** |

py3.11: the first functional pass (before RED-02/LOG-03 were split and SID-07 added) gave 1076/5/165; the final 3.11 numbers
come from a full re-run with the final tests (`junit/py3.11-supp.xml`, 1078/6/165 incl. restart) + the throttle phase
(`junit/py3.11-throttle.xml`). py3.13: `junit/py3.13-{functional,restart,throttle}.xml`.

### The requested checks

| Check | Result |
|---|---|
| REG-14b static + `SSO_PROTECT_READS` `/mcp` 401 byte-identical to route 401 | **PASS** (F-02 fixed by 0a41970) |
| `Cache-Control: no-store`, sso/both (NS-01): GET/POST /login (form, wrong pw, success 303, signed-in 303, `next=//evil`), /dashboard (200, anon 303, bad Bearer 401), /logout (POST 303, GET) | **PASS**, exactly one header each |
| no-store not spread to API routes (NS-02) | **PASS** |
| static/static_reads: no Cache-Control on /dashboard (200/401), /login (404), /logout (NS-03); REG-* byte snapshot | **PASS**, byte-identical |
| Path variants (NS-04) | /dashboard/ and /login/ 307 to the canonical path without no-store, /Dashboard 404 - no content, Info |
| token= redaction in uvicorn access log (RED-01: `?token=`, `&token=`, `TOKEN=`, `access_token=`, `;token=`, repeated; 200/401/404/405) | **PASS** |
| Error path: 500 with `?token=` (RED-03) incl. traceback in uvicorn.error | **PASS**, token not logged |
| Other sinks: all 9 wiki logs, both SSO logs, stub log, whole run (LOG-01..06, LOG-03 strict again) | **PASS** except RED-02 below |
| Percent-encoded name `?%74oken=<static>` (RED-02) | **FAIL (Low)** - authenticates, logged in clear |
| Session rotation (ROT-01 same/other user, COOKIE-08) | **PASS**: new id + CSRF, old row deleted, row count unchanged, old cookie JSON 401 / HTML 303 → /login, SSO `/auth/refresh` with old refresh → 401 |
| Rotation when SSO logout fails (ROT-03: 500 and connection close) | **PASS**: login succeeds, old row deleted, one logout call carrying the old refresh token |
| Failed re-login keeps the session (ROT-02, COOKIE-09, Q-10) / GET /login signed in 303 → /dashboard (LOGIN-16, Q-08) | **PASS** |
| GET /sources/<malformed> (SID-01, 6 ids x static/both) | **PASS** 404, no traceback |
| POST /compile/<malformed> (SID-03), the other HTTP route taking an id | **FAIL (Low)** 500 |
| MCP compile_update malformed (SID-04) | **PASS** `isError: true`, "not a valid source_id: '...'" |
| MCP tools taking ids (SID-05) | compile_update is the only one; inventory pinned |
| CLI `compile`/`source` malformed (SID-06, incl. `""`, `../../etc/passwd`) | **PASS** exit 1, one line on stderr, no traceback |
| Well-formed unknown id on compile (SID-07, pre-existing PRE-03) | **FAIL (Low, pre-existing)** HTTP 500, CLI traceback |

### Failures

**F-R2-01 (Low) POST /compile/<malformed id> → 500, not 404.** `src/llmwiki/api/routes.py:433-435` calls
`tools.compile_update` without the `except tools.InvalidSourceId` mapping that `source_status` (routes.py:321-327) got;
6520890 claims "one InvalidSourceId for HTTP, MCP and CLI". Protected route (token needed), no detail leaked, but the
uvicorn.error log gets a full traceback per call.
Repro: `curl -si -X POST -H "Authorization: Bearer $STATIC" http://127.0.0.1:8035/compile/qa-no-such-source` →
`HTTP/1.1 500 Internal Server Error` / `Internal Server Error`; expected 404 `{"detail":"not a valid source_id"}`.
Same on both mode (8031). Fix: the same try/except as `source_status`.

**F-R2-02 (Low) token redaction misses a percent-encoded parameter name.** `src/llmwiki/config.py:24` matches the literal
text `token=`; uvicorn logs the raw query, while Starlette decodes `%74oken` to `token`, so the request authenticates and
the static token is logged in clear. Repro: `curl -s -o /dev/null "http://127.0.0.1:8035/dashboard?%74oken=$STATIC"`
→ 200; `wiki-static.log`: `"GET /dashboard?%74oken=qa-static-... HTTP/1.1" 200 OK`. Expected `[redacted]`. Only a client
that deliberately encodes the name triggers it (browsers and our scripts don't). Fix: redact by parsing the query with
`urllib.parse.parse_qsl` and comparing decoded names, or percent-decode before matching.

**PRE-03 (Low, pre-existing, not a regression) compile of a well-formed but unknown id.** `tools.py:235`
`pipeline.load_meta` raises `ObjectNotFound`, not mapped anywhere: `POST /compile/00000000000000aa` → 500 (traceback in
the log), `llmwiki compile 00000000000000aa` → exit 1 with a traceback. `GET /sources/<same id>` answers 200
`state: failed, error: unknown source`. Suggest mapping `ObjectNotFound` to 404 / a clean CLI message.

### Q-12 / Q-19 / Q-23 (accepted as is): safety review

- Q-12: refresh tokens carry no `perms` (PERM-14 passes) and a refresh token presented as a Bearer gets 401 (FB-07
  `refresh_type`, JWTV). Nothing authorises on a refresh token. Safe.
- Q-19 failed-login page (401 HTML) and Q-23 HTML 403 page: same message for wrong password / unknown user / inactive
  (LOGIN-06, normalised compare), the no-permission page creates no session (LOGIN-09), and every response on those paths
  is now `no-store` (NS-01). No token, CSRF value or account-existence signal in them. Nothing unsafe found.

### Existing llm-wiki suite @ 6520890

Clean detached worktree (removed afterwards), `uv sync --frozen --extra dev`, `env -i`:
py3.11.14 and py3.13.16 both `4 failed, 1047 passed, 1 skipped, 32 deselected` (22e77e5 was 1001 passed). The 4 are the
known factory-isolation failures (3 in `test_channels`, `test_mcp_is_reachable_at_slash_mcp`), all
`RuntimeError: missing required configuration: R2_ACCESS_KEY_ID, ...`. The TODOS ticket exists: `TODOS.md:7` "Test isolation: factory/tools hold an import-time copy of `settings`" (cause `factory.py:18`).
Logs: `run/logs/existing/6520890-py3.{11,13}.txt`, `run/logs/junit/existing-6520890-py3.{11,13}.xml`.

## Progress log

| Time (PT) | Phase | State |
|---|---|---|
| 22:40 | llm-wiki `feat/sso-login` @ 6520890, clean, 01f43a8 is an ancestor (9 commits on top). sso-server @ 03516ce, clean; `bae173b..03516ce` touches only `docs/sso-integration-design.md` (+13 lines) | done |
| 22:45 | Plan/tests updated: new `test_qasso_r2_fixes.py` (NS/RED/ROT/SID, 50 items); LOG-03 strict again; COOKIE-08 (old cookie dies); LOGIN-16/COOKIE-09 pinned to the confirmed Q-08/Q-10 behaviour; LOGOUT-07 skip reason = Q-15 out of scope. 1,248 items collected | done |
| 22:44 | Existing suite @ 6520890 (clean worktree, removed afterwards): py3.11.14 and py3.13.16 both `4 failed, 1047 passed, 1 skipped, 32 deselected`; the 4 are the known factory-isolation failures (R2_* RuntimeError); TODOS.md "Test isolation: factory/tools hold an import-time copy of settings" exists | done |
| 22:56 | qa_sso py3.11 functional (+ throttle phase 22/0): 5 failed, 1076 passed, 164 skipped, 1 xfailed; restart 2 passed. Failures: SID-03 x4 (POST /compile/<malformed> = 500), LOG-03 (token logged with a percent-encoded `%74oken=` name, from the RED-02 probe). Tests then adjusted so this finding is counted once (RED-02 strict, LOG-03 skips that probe line); SID-07 added (pre-existing, well-formed unknown id) | done |
| 23:11 | qa_sso py3.13 (final tests): functional 1076/6/165, restart 2/0, throttle 22/0 | done |
| 23:24 | qa_sso py3.11 full re-run with final tests: 1078/6/165 (incl. restart); throttle phase 22/0 from the first pass | done |

---

# Quick check @ ba2097f

> **Status: COMPLETE** (2026-10-11, 00:12 PT). Progress log at the end of this section.

## Summary

- llm-wiki-svc `feat/sso-login` @ **ba2097f**, clean; cf7130e is an ancestor.
- **sso-server is at b46bc94, not 03516ce.** That is one more docs-only commit by Philip (23:27 PT, "docs: sync
  integration design (session rotation note)", `docs/sso-integration-design.md` +4/-1). 03516ce is its parent and the
  tree is clean. The two design-doc copies are now identical, so the round-2 drift note is resolved. No code change.
- Server interpreters: all 11 processes (SSO x2, stub, 9 llm-wiki) on CPython 3.11.14 / 3.13.16, also after the
  restart test; pytest on the same version. Ports 8030-8042, PID stop, all ports free at the end, throwaway DBs.

### Counts (pass / fail / skip; skips include 1 xfail JWTV-03 and the 20 throttle tests skipped in the functional phase)

Every area except r2_fixes is identical on both Pythons and fully green: SEM 16, auth matrix 495 (135 skip), cookies 10,
CSRF 58 (7 skip), fallback 29, JWT verification 48 (1 skip), login 28, login SSO errors 12, logout 10 (1 skip),
MCP 27, open redirect 38, session 36 (1 skip, incl. 2 restart), app-perm command 23, perms 47 (SSO_SUPERUSER_ALL_APPS on
and off), refresh perms 7, startup 43, static regression 102 (100 + 2 throttle phase), logs 6, throttle 20.

| | py3.11 | py3.13 |
|---|---|---|
| r2_fixes (NS/RED/ROT/SID) | 91 / 8 / 0 | 91 / 8 / 0 |
| **Total** | **1,146 / 8 / 165** | **1,146 / 8 / 165** |

How the numbers were assembled: full functional + restart run on each Python (`junit/py3.x-{functional,restart}.xml`),
then, because RED-02's case-variant expectations were a QA test bug fixed after that run (see below), `r2_fixes` and
`zy_logs` were re-run on a fresh stack (`junit/py3.x-supp.xml`) and the throttle phase run (`junit/py3.x-throttle.xml`).
The table uses the supp results for those two files and the full run for everything else. The first run's 13 failures
per Python were: the same 8 below, 4 QA-bug cases (`%54OKEN`, `T%6fKeN` expected 200), and LOG-03 for the same leak.

### Requested repros

| Check | Result |
|---|---|
| F-R2-01: POST /compile/<malformed> (3 ids) → 404 `{"detail":"not a valid source_id"}`, static and both (SID-03) | **PASS** |
| F-R2-02: `%74oken`, `t%6Fken`, `%74%6F%6B%65%6E` authenticate as before (200) and are redacted; `%54OKEN`, `T%6fKeN`, `access%5Ftoken`, `Access_%54oken`, `+token` keep their previous status (401 static / 303 both) and are redacted; checked in every log file (9 wiki, 2 SSO, stub), static and both (RED-02) | **PASS** |
| F-R2-02 double-encoded: `%2574oken`, `t%256Fken`, `%25%37%34oken`, `%2574%256Fken` | status unchanged (401/303) but **value logged in clear: FAIL (Low)**, F-R3-01 |
| Plain `token=` spellings, 500 path, all sinks (RED-01, RED-03, LOG-01..06 incl. strict LOG-03) | **PASS** |
| PRE-03: POST /compile/<well-formed unknown> → 404 `{"detail":"no such source: '00000000000000aa'"}`, static and both (SID-07) | **PASS** |
| PRE-03: MCP compile_update unknown → `isError`, "no such source", no traceback (SID-07b) | **PASS** |
| PRE-03: `llmwiki compile <unknown>` → exit 1, one error line (stderr otherwise only the usual INFO backend lines), empty stdout, no traceback (SID-07c); malformed ids one line (SID-06) | **PASS** |
| GET /sources/<well-formed unknown> | **200** `state: failed, error: unknown source` (SID-02), **not 404**: see "Static byte identity" |
| 307 trailing-slash redirects (NS-04, was the round-2 Info note): GET /dashboard/, /login/, /logout/, POST /logout/, /dashboard/?month= → 307 to the slash-less path (query kept), exactly one `no-store` in sso and both; static and static_reads: /dashboard/ 307 with no Cache-Control, /login/ and /logout/ 404 with none; /Dashboard 404 (NS-05) | **PASS** |
| Session rotation, Q-08/Q-10, no-store pages (ROT-*, NS-01..03) | **PASS** (unchanged) |

### Static byte identity

- REG suite (102 incl. the route snapshot `snapshots/static_routes_bca2bef.json`, exact 401 bodies/headers, REG-14b) and
  NS-03/NS-04: **PASS** on both Pythons. No Cache-Control anywhere in static mode, 307 included.
- In-process comparison bca2bef worktree vs ba2097f (static mode, offline backends; `logs/probe-ba2097f/`):
  identical for `GET /sources/<well-formed unknown>` (200, same body apart from timestamps), `/dashboard`, `/dashboard/`.
  Three answers differ, all 500 → 404: `GET /sources/qa-no-such-source` (malformed), `POST /compile/qa-no-such-source`,
  `POST /compile/00000000000000aa`. At bca2bef these were bare `500 Internal Server Error` crashes (PRE-01/PRE-03);
  **intended fixes, not regressions**.
- The well-formed unknown id on `GET /sources` was **not** changed to 404 (still 200 "failed / unknown source", as at
  bca2bef, and `llmwiki source <id>` still prints that status with exit 0). That keeps static mode identical, but it is
  not "404 for /sources". **Decision needed (Philip):** keep it (static unchanged) or make it 404 (a visible static-mode
  change). The tests pin the current behaviour (SID-02, SID-07d).

### New failure

**F-R3-01 (Low) double-encoded token parameter names are logged in clear.** `src/llmwiki/config.py:31-33`
(`_is_token_name`) decodes the name once, like Starlette. A name such as `%2574oken` is `%74oken` to Starlette, so it is
not a credential (status unchanged, 401 static / 303 both) and the redactor leaves its value alone.
Repro: `curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8035/dashboard?month=2026-10&%2574oken=$STATIC"` → 401;
`wiki-static.log`: `"GET /dashboard?month=2026-10&%2574oken=qa-static-... HTTP/1.1" 401`. Same for `t%256Fken`,
`%25%37%34oken`, `%2574%256Fken`, and on both mode (8031). Only a client with a double-encoding bug triggers it, and the
value is a real token then. Fix: decode repeatedly (until stable, bounded) before comparing, which errs towards redacting.

### Existing suite @ ba2097f (py3.11.14)

Clean detached worktree (removed): `4 failed, 1074 passed, 1 skipped, 32 deselected`. The 4 known R2_* factory-isolation
failures (3 `test_channels`, `test_mcp_is_reachable_at_slash_mcp`); TODOS.md:7 ticket still there.

## Progress log


| Time (PT) | Phase | State |
|---|---|---|
| 23:34 | llm-wiki `feat/sso-login` @ ba2097f, clean, cf7130e is an ancestor (4 commits: 554bb18, 27017fd, f5c6230, ba2097f). **sso-server is at b46bc94, not 03516ce**: one more docs-only commit by Philip (23:27 PT, "sync integration design (session rotation note)", +4/-1 in `docs/sso-integration-design.md`), 03516ce is its parent, tree clean. Both design-doc copies are now identical (the round-2 drift note is resolved) | done |
| 23:35 | Static byte check in-process (FastAPI TestClient, static mode, offline backends) bca2bef worktree vs ba2097f: `logs/probe-ba2097f/` | done |
| 23:36 | Tests updated: NS-04 strict (307 trailing slash, 5 paths x 4 instances), NS-05, RED-02 (12 encoded/double-encoded/mixed names x static/both, all log files), SID-02 (unchanged unknown-id status), SID-06 one line, SID-07/07b/07c/07d; LOG-03 strict. 1,297 items | done |
| 23:37 | Existing suite @ ba2097f, py3.11.14 (clean worktree, removed): `4 failed, 1074 passed, 1 skipped, 32 deselected`; the 4 known R2_* factory-isolation failures | done |
| 00:01 | qa_sso full functional + restart: py3.11 (23:48) and py3.13 (00:01) each 1117/13/165 + restart 2/0; identical failures. 4 of the 13 were a QA bug (Starlette query names are case-sensitive, so `%54OKEN`/`T%6fKeN` never authenticated); RED-02 fixed, LOG-03 skips RED-02's double-encoded probe lines (reported once by RED-02). Throttle phase didn't start (typo in the QA wrapper script), so it was run in the supplemental pass | done |
| 00:06 | Supplemental on a fresh stack per Python: r2_fixes + zy_logs 97/8 (py3.13 00:03, py3.11 00:06), throttle phase 22/0 each | done |
