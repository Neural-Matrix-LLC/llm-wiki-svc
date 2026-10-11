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
