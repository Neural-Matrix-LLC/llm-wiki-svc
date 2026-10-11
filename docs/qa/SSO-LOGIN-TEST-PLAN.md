# llm-wiki-svc SSO login and `application.app1`: QA test plan (v1.2 + regression @ 6520890 + rechecks @ ba2097f, 2be7913)

| | |
|---|---|
| Design under test | `docs/sso-integration-design.md` **v1.0 + "v1.1 changes" + "v1.2 clarifications"** (later sections authoritative). Committed in both repos (llm-wiki 3cb5320, sso-server a4a8e0f); the two copies are **byte-identical** at 22e77e5 / bae173b (C-15 closed) |
| Repos / branches | llm-wiki-svc `feat/sso-login` @ **22e77e5** (clean apart from QA's untracked files); sso-server `feat/app-permissions` @ **bae173b** (clean). Results: `docs/qa/SSO-LOGIN-RESULTS.md` |
| Basis | The design. Code is used only for paths and setting names (`SESSION_DB_PATH`, `SESSION_MAX_AGE_HOURS`, `SSO_CLIENT_ID/SECRET`, `<meta name="csrf-token">`, sso-server `SSO_SUPERUSER_ALL_APPS` env) and for today's static behaviour (snapshot at bca2bef) |
| Automation | `tests/qa_sso/` (black-box plus a few marked grey-box session-store cases, opt-in with `QA_SSO=1`). See `tests/qa_sso/README.md` |
| Previous version | v1.0 of this plan (same IDs where a case survived; changed cases are marked **v1.1**) |
| Author / date | QA, 2026-10-10 |

## 0b. Regression round @ 6520890 (Philip's decisions, 2026-10-10)

- Fixes under test: REG-14b compact MCP 401 (0a41970), `Cache-Control: no-store` in sso/both (4be5a1f), `token=`
  redaction in uvicorn's access/error loggers (d8b1e89), `GET /sources/<malformed>` 404 (e336dcb), session rotation
  on re-login (a13bc48), one `InvalidSourceId` for HTTP/MCP/CLI (6520890).
- Decisions: Q-08 and Q-10 current behaviour confirmed (LOGIN-16 and COOKIE-09 now assert it exactly); Q-15 out of
  scope (LOGOUT-07 skipped with that reason); Q-20 no-store in sso/both only, static byte-identical (NS-01..04);
  Q-21 no local `/login` rate limit (nothing to test beyond THR); Q-32 data-dir default (START-11); Q-12/19/23
  current behaviour accepted. Developer's `/mcp` Bearer-only (MCP-10, CSRF-21) and 60 s `last_seen` (SESS-22/23)
  accepted. `SSO_SUPERUSER_ALL_APPS` still pending Thomas: both values stay under test (PERM-17..20).
- New file `tests/qa_sso/test_qasso_r2_fixes.py` (section 3.19). LOG-03 is strict again (no `?token=` exclusion);
  COOKIE-08 now also asserts the pre-login cookie dies on re-login.

## 0c. Recheck @ 2be7913 (2026-10-11)

- **Decision (Philip, 2026-10-11):** `GET /sources/<well-formed unknown id>` keeps returning 200
  `{"state":"failed","error":"unknown source",...}` for static compatibility (not 404). Pinned by SID-02 and, for the
  CLI, SID-07d. POST /compile and MCP compile_update with the same id stay 404 / tool error (SID-07, SID-07b).
- Redaction now decodes parameter names until stable, at most 5 rounds, and redacts anything still encoded past the
  cap. New file `tests/qa_sso/test_qasso_r3_recheck.py`:

| ID | Steps | Expected |
|---|---|---|
| RED-10 (x48) | `/dashboard?month=..&<'token' encoded 1..8 times, 3 encoders>=<static>` on static and both | status as at ba2097f (only a name that Starlette's single decode turns into `token` authenticates: 200; else 401 static / 303 both); `[redacted]` in the access line; token in no log |
| RED-11 (x10) | harmless name `month` encoded 1, 2, 5, 6, 7 times (2 encoders), static | 401 as before; value kept up to 5 rounds, redacted past the cap (acceptable over-redaction, recorded) |
| RED-12 (x10) | long `%25` nesting (2,000 / 3,500 levels), 4,000-char `%25` run, 300 six-times-encoded params, `%` garbage; static and both | response < 2 s, `/healthz` 200 right after, status unchanged (or a 400/414/431 from the HTTP layer), token in no log |
| RED-13 | grey box: `redact_tokens` on 1 MB pathological strings | each call < 1 s; nested token names redacted; a bare `%` run (never decodes) is not a token name |

## 0a. What changed in v1.2 (summary)

| v1.2 clarification | Effect on the plan / tests |
|---|---|
| D-16: session DB 0600, dir 0700 if created; refuse to start if group/world-readable | SESS-04 strict (0600, no group bits), new START-13 (0644/0640/0604 refused), START-13b (created 0600/0700, -wal/-shm) |
| C-11/Q-25: `CLIENT_IP_HEADER` honoured only from `TRUSTED_PROXY_IPS` | New START-12 (header without trusted list / bad CIDR refuse; developer's choice), START-12b (static ignores it), START-14 (trusted vs untrusted peer, XFF right-to-left, malformed header) |
| C-12: static + `SSO_PROTECT_READS=true` also protects `/mcp` with the static token | REG-14 rewritten, new REG-14b (401 bytes equal a route 401), MATRIX `static_reads` mcp cells |
| C-13/Q-30: CSRF on POST/PUT/PATCH/DELETE only; failure 403 `{"error":"csrf_failed"}` | CSRF-11..13/19/20 strict body; CSRF-22 (form field not a substitute, QA judgment); new CSRF-23 (HEAD/OPTIONS not checked) |
| C-14: SSO unreachable → 503 everywhere, login included | LERR-03/04 now 503 |
| C-19/C-18: wrong ServiceClient credentials → SSO 401 `invalid_client`, llm-wiki ERROR log + "Login service misconfigured" 503 | THR-11 rewritten (401 invalid_client even with the right password, Basic challenge), new LERR-09 (stub mode `invalid_client`) |
| Q-28: invalid `client_ip` with valid credentials → 400 `invalid_request` | THR-14 strict 400 |
| Q-26: cookie Max-Age = remaining absolute lifetime; Q-27: `SESSION_IDLE_HOURS` default 12 | COOKIE-02 strict, new COOKIE-02b; SESS-17 now idle under/over 12 h |
| Q-31: invalid explicit Bearer → 401 even with a valid cookie; Q-14: missing `kid` → reject | SEM-12/CSRF-18 unchanged (strict 401); JWTV-09 strict 401. Basic (non-Bearer) header on HTML = no credential → 303 (SEM-10, MATRIX dashboard `basic_scheme`) |
| Developer's choices beyond the design | MCP-10 (cookie on `/mcp` → 401, Bearer only), CSRF-21 strict 401; SESS-22 (last_seen written ≤ once/min), SESS-23 (does not affect logout or absolute expiry); START-12 |
| Older questions not in v1.2 (QA judgment) | Q-08 → LOGIN-16 and Q-10 → COOKIE-09 are now observation tests with safety invariants (no longer skipped); Q-32 → START-11 tests the implemented default `{LOCAL_STORAGE_PATH}/sessions.sqlite3` |

## 0. What changed in v1.1 (summary)

| Design item | Effect on the plan |
|---|---|
| 1 MCP protected in sso/both; static open + startup WARNING | D-01 resolved. MATRIX mcp column fixed (no `?`); MCP-04/04b/04c/04d/05/09; START-07/08; REG-14/15 |
| 2 Server-side SQLite sessions, opaque 256-bit id, absolute 168 h, prune, per-session lock | D-02, D-04, D-05, D-13 resolved by design. COOKIE-* and SESS-* rewritten (opaque id, small constant size, row deleted on logout/refresh failure, race with 2 workers, absolute vs idle, file mode, restart survival, prune); LOGOUT-03 now expected to pass |
| 3 X-CSRF-Token on cookie-authenticated non-GET; Bearer exempt | D-06 / Q-04 resolved. CSRF-11..22 |
| 4 ServiceClient Basic + `client_ip` at the SSO; llm-wiki sends the browser IP | D-03 resolved by design. THR-* rewritten (per-source-IP isolation, spoof ignored, invalid IPs, login log, refresh); LOGIN-15/15b |
| 5 Static byte-for-byte, no `/login`/`/logout` (404), PROTECT_READS uses the static token | D-07, D-08, Q-01, Q-03 resolved. REG-01 exact inventory, REG-02/04/05 exact headers and bytes, REG-12/13; new instance `static_reads` |
| 6 JWT-verify-then-static fallback in both; refuse changeme / < 24 chars in sso/both, warn in static | D-09, Q-16 resolved. New FB-* (instance `dotted`), START-05/05b/05c |
| 7 SSO down during refresh keeps the session (continue / 503); refresh without perm deletes the session (403); invalid explicit Bearer on HTML = 401 | Q-05, Q-06, Q-07 resolved. SESS-09/09b/10/11, SEM-10/11/12, MATRIX dashboard cells pinned |
| 8 `perms` always a list; superuser-all switchable (`SSO_SUPERUSER_ALL_APPS`, default true); Q-13 rule | Q-11, Q-13, Q-17 (pending Thomas) resolved. PERM-01 strict, PERM-16..20 (2nd SSO with the flag off), APPCMD-06/10 |
| 9 `SESSION_SECRET` dropped; revocation window accepted | SESSION_SECRET cases removed (old START-01 variant, COOKIE-02/03/05/08, LOG-05); START-09; D-10 accepted |

## 1. Scope

In scope:
- **SSO side (Part A + v1.1 items 4 and 8):** the `application` app, `create_app_permission`, the `perms` claim
  in access tokens, the login/refresh `user` object, `/me` and `/introspect`, re-computation on refresh, the
  shared-table safety net, `SSO_SUPERUSER_ALL_APPS`, and `client_ip` delegation by a ServiceClient.
- **llm-wiki side (Part B + v1.1 items 1 to 3, 5 to 7, 9):** settings and fail-fast, verification, the API
  dependency and `AUTH_MODE` matrix, the login page, server-side sessions, CSRF, refresh, logout, the
  dashboard, MCP protection, the static-token fallback, scripts and channels, and the static regression.

Out of scope: SSO sprint-1 functionality (covered by `sso-server/qa/TEST-PLAN.md`), production deployment (on
hold), Cloudflare Access configuration (M-07), myFinance itself (only the shared-table risk is exercised).

## 2. Environment and assumptions

- Local, dedicated ports, **never 8000**; started and stopped by recorded PID only (`tests/qa_sso/run/*.sh`):

  | port | process |
  |---|---|
  | 8030 | SSO (gunicorn, 2 workers, SQLite + file cache under `run/state/`) |
  | 8040 | 2nd SSO process on the **same** DB/keys/issuer with `SSO_SUPERUSER_ALL_APPS=false` |
  | 8031 | llm-wiki `both` (primary `LLM_WIKI_BASE_URL`) |
  | 8034 | llm-wiki `sso` (login flows, cookies, CSRF, restart cases) |
  | 8035 | llm-wiki `AUTH_MODE` unset (static default) |
  | 8041 | llm-wiki `static` + `SSO_PROTECT_READS=true` |
  | 8036 | llm-wiki `sso` + `SSO_PROTECT_READS=true` |
  | 8037 | llm-wiki `sso`, required perm `application.qa_app2` |
  | 8042 | llm-wiki `both` with a JWT-shaped `INGEST_API_TOKEN` (D-09 fallback) |
  | 8032 | llm-wiki `sso` against the stub, **2 uvicorn workers** (refresh race across processes) |
  | 8033 | stub SSO (proxy / mint / fail; records `client_ip` and the Basic client id) |
  | 8038 | nothing (SSO down) |
  | 8039 | throw-away processes of the startup cases |
- Every llm-wiki instance: `SSO_CLIENT_ID/SECRET` of the seeded ServiceClient `qa-wiki-login`, its own
  `SESSION_DB_PATH=run/state/wiki-NAME/sessions.sqlite3`, static token ≥ 24 chars, no `SESSION_SECRET`, offline
  backends, `LOG_LEVEL=DEBUG`, empty cwd, `SESSION_COOKIE_SECURE` default `true`, the **default umask** (the session
  store's file mode is tested, not fixed by the harness).
- SSO: 3072-bit key, access TTL 2 min, throttles raised in the functional phase and at spec rates (10/min IP,
  5/min user, 60/min refresh) in the throttle phase. Seeded ServiceClients: `qa-wiki-introspect`, `qa-wiki-login`
  (active), `qa-wiki-login-inactive`.
- Seed: synthetic `qa_wiki_*` users through `manage.py shell` with the test-DB guard. v1.1 adds
  `qa_wiki_super_app1` (superuser + direct app1), `qa_wiki_sess`, `qa_wiki_sess2`, `qa_wiki_thr_deleg`.
- **Distinct client IPs** come from binding the test client to loopback addresses 127.0.0.21 to .62 (Linux routes
  all of 127/8 to `lo`); llm-wiki sees them as `request.client.host`, the SSO as `REMOTE_ADDR`.
- **Grey-box cases** (marker `grey`) read the QA instance's own SQLite session store and, for lifetime and prune
  cases, move `created_at`/`last_seen` into the past. Writes are refused outside `tests/qa_sso/run/state/`.
- "200"/"allowed" = the route's own answer (200, or 422/404 for the side-effect-free probes in `matrix.py`).
  Cookie-authenticated non-GET probes in the matrix send the session's `X-CSRF-Token`; CSRF itself is §3.10.
- A1. HTML/JSON split is per route (`/dashboard`, `/login` HTML; the rest JSON), not by `Accept` (SEM-08).
- A2. Failure status for a missing/wrong `X-CSRF-Token` is not specified: 403 or 400 accepted (Q-30).

## 3. Test cases

Format: **ID**: preconditions → steps → expected. *Auto* = automated (file in brackets); *Manual*;
*Skip(Q-nn)* = skipped until the open question is decided. **v1.1** marks new or changed cases.

### 3.1 SSO perms claim (PERM, PREF, APPCMD), design A1 to A4 + v1.1 item 8

`[test_qasso_sso_perms.py]` Precondition: SSO on `feat/app-permissions`, migrated, seeded.

| ID | Steps | Expected |
|---|---|---|
| PERM-01 (×11 users) **v1.1** | Native login per user; decode access token | `perms` **present** and == sorted application.* of user + groups (superusers: every `application.*` row); a missing claim now **fails** (Q-11 resolved) |
| PERM-02 | `qa_wiki_direct` | app1 in `perms` |
| PERM-03 | `qa_wiki_group` | app1 in `perms`; group in `groups` |
| PERM-04 | `qa_wiki_both` | `["application.app1"]`, no duplicates |
| PERM-05 | `qa_wiki_super` (no explicit grants), flag default | all application.* |
| PERM-06 (×4) **v1.1** | none / staff / non-app / other-app users | `perms` is a list without app1 |
| PERM-07 | Users holding `auth.view_user` | Non-application perms never exported |
| PERM-08 (×2) | Inactive with grant, inactive superuser | 403 `account_inactive`, no tokens |
| PERM-09 | `get_all_permissions` for inactive users | `[]` |
| PERM-10 (×3) | Login `user` object | == token claim |
| PERM-11 (×4) | `/me` | == token claim |
| PERM-12 (×4) | `/introspect` | `active:true`, == claim |
| PERM-13 | Mixed user | JSON array, never string/null |
| PERM-14 | Refresh token | `perms` present? xfail(Q-12, still open) |
| PERM-15 | `qa_wiki_many` (40 groups) | Claims complete; sizes recorded (no longer feeds the cookie: tokens are server-side) |
| PERM-16 (×2) **v1.1** | No-grant users: token, login `user`, `/me`, `/introspect` | `perms == []` in all four (never absent, never null) |
| PERM-17 (×2) **v1.1** | `qa_wiki_super`, `qa_wiki_super_app1` on the default SSO (flag true) | every application.* |
| PERM-18 (×4) **v1.1** | Same DB via the 8040 SSO (`SSO_SUPERUSER_ALL_APPS=false`): super, super_app1, direct, mixed | `[]`, `[app1]`, `[app1]`, `[app1, qa_app2]` consistently in token, `user`, `/me`, `/introspect` |
| PERM-19 **v1.1** | Flag off: superuser refresh | Refreshed token still `[]` |
| PERM-20 **v1.1** | Flag off: superuser token at llm-wiki | 403 |

`[test_qasso_sso_refresh_perms.py]` (mutable users are reset before and after each test)

| ID | Steps | Expected |
|---|---|---|
| PREF-01 | Login without app1 → grant directly → introspect old access → refresh | Old access token unchanged (no app1) until exp; introspect of the old token **does** show app1 (fresh from DB); refreshed access + `user` show app1 |
| PREF-02 | Login with app1 → revoke → introspect → refresh | Old token keeps app1 until exp (≤ access TTL). Introspect: active, no app1. Refreshed token: no app1 |
| PREF-03 | Group member → removed from group → refresh | app1 dropped |
| PREF-04 | app1 removed from the group itself → refresh (restored afterwards) | app1 dropped for the member |
| PREF-05 | Added to the app1 group → refresh | app1 added |
| PREF-06 | Login → deactivate → refresh; introspect old access | Refresh 403 `account_inactive`; introspect `active:false`, no perms |
| PREF-07 | Set superuser → refresh → unset → refresh | app1 appears, then disappears |

Consequence documented in the plan and tested on the llm-wiki side (JWTV-12, SESS-32): llm-wiki verifies
locally and never introspects, so a revoked user keeps access **until their current access token is
refreshed** (≤ 15 min with the default TTL; for a browser session, until the 60 s refresh window). D-10: accepted and documented in v1.1 item 9.

`[test_qasso_sso_app_perm_command.py]`

| ID | Steps | Expected |
|---|---|---|
| APPCMD-01 | After migrate | `application.app1` exists |
| APPCMD-02 | `create_app_permission qa_app3` twice | Exit 0 both times, exactly one row (idempotent) |
| APPCMD-03 (×11) | Bad codenames: `App1`, `1app`, `a`, `app-1`, `app 1`, `app1;drop`, `_app`, 64×`x`, `app1\n`, empty, `äpp` | Non-zero exit, no row created |
| APPCMD-04 (×4) | Boundaries: `ab`, 63 chars, `app_2`, `z9` | Accepted |
| APPCMD-05 | Create `qa_app4`; superuser logs in | `application.qa_app4` in superuser's perms with no code change |
| APPCMD-06 | Delete `application.qa_app2` (command-created; simulated `remove_stale_contenttypes`) → `migrate` | **Not** recreated (v1.1 item 8, Q-13 resolved: only code-defined permissions come back; documented); app1 still present. Restored with the command afterwards |
| APPCMD-10 | Delete `application.app1` (code-defined) → `migrate` | Recreated by post-migrate; every app1 grant is lost (A3 warning). Re-seeded afterwards |
| APPCMD-07 | `showmigrations application` | `0001` applied (migration owned by `application`) |
| APPCMD-08 | List application perms | No `add/change/delete/view_applicationaccess` (`default_permissions = ()`) |
| APPCMD-09 | `ApplicationAccess.objects.count()` | 0 (anchor table never holds rows) |
| APPCMD-M1 *Manual* | Django admin: filtered "application permissions" list; assign app1 via the stock User and Group admin | Visible to staff; both assignment paths work (PERM-02/03 then pass) |
| APPCMD-M2 *Manual* | Review the README for the A3 warning and the "how to add app2" recipe | Present, loud, correct |

### 3.2 llm-wiki AUTH_MODE matrix (MATRIX), design B1/B3/B4/B5 + v1.1 items 1, 3, 5, 6, 7

`[test_qasso_auth_matrix.py]`: one parametrised test, **630 items** = **6** instance configs (static, static +
PROTECT_READS **v1.1**, sso, both, sso + PROTECT_READS, both + PROTECT_READS) × 5 route classes × 21 credentials.
Probes and credentials as in v1.0 (protected: `/usage`, `/ingest`, `/upload`, `/feedback`, `PUT/DELETE /domains`,
`/lint`, `/compile`, deny-only `/worker/resume`, `/synthesize`; read: `/domains`, `/concepts`, `/worker`,
`/search`, `/answer`, `/page`, `/sources`; healthz; dashboard; MCP initialize). The session-cookie credential
now carries the session's `X-CSRF-Token` (v1.1 item 3). In static mode every 401 must have **no**
`WWW-Authenticate` (v1.1 item 5); in sso/both every 401 has `WWW-Authenticate: Bearer`.

**Protected (write/admin) routes**

| credential | static | sso | both |
|---|---|---|---|
| no credential | 401 | 401 | 401 |
| Bearer INGEST_API_TOKEN | 200 | 401 | 200 |
| wrong static / Basic scheme | 401 | 401 | 401 |
| SSO token **with** app1 | 401 | 200 | 200 |
| SSO token **without** app1 (verified) | 401 | 403 | 403 (no fallback: v1.1 item 6) |
| bad SSO token (expired, wrong aud/iss, attacker key, alg none, HS256, refresh token, tampered, garbage) | 401 | 401 | 401 (verification fails → static compare fails) |
| session cookie + X-CSRF-Token | n/a (no `/login`) | 200 | 200 |

**Read routes** (`/healthz` always 200; `/login*` public where it exists)

| credential | static | **static + PROTECT_READS (v1.1)** | sso | both | sso + PR | both + PR |
|---|---|---|---|---|---|---|
| no credential | 200 | 401 | 200 | 200 | 401 | 401 |
| Bearer INGEST_API_TOKEN | 200 | 200 | 200 | 200 | 401 | 200 |
| wrong static / Basic | 200 | 401 | 200 | 200 | 401 | 401 |
| SSO token with app1 | 200 | 401 | 200 | 200 | 200 | 200 |
| SSO token without app1 | 200 | 401 | 200 | 200 | 403 | 403 |
| bad SSO token | 200 | 401 | 200 | 200 | 401 | 401 |
| session cookie | n/a | n/a | 200 | 200 | 200 | 200 |

**`/dashboard` (HTML)**, v1.1 item 7 (Q-07): explicit invalid Bearer → 401; no credentials → 303

| credential | static | sso | both |
|---|---|---|---|
| no credential | 401 | 303 `/login?next=/dashboard` | 303 |
| Bearer INGEST_API_TOKEN | 200 | **401** | 200 |
| wrong static / Basic | 401 | **401** | **401** |
| `?token=` INGEST_API_TOKEN | 200 | 303 (query form disabled = no credential) | 200 |
| SSO token with app1 | 401 | 200 | 200 |
| SSO token without app1 | 401 | 403 | 403 |
| bad SSO token | 401 | **401** | **401** |
| session cookie | n/a | 200 | 200 |

**`/mcp` (initialize)**, v1.1 item 1

| credential | static | sso | both |
|---|---|---|---|
| no credential | 200 (open, WARNING at startup) | 401 | **401** |
| Bearer INGEST_API_TOKEN | 200 | 401 | 200 |
| wrong static / Basic | 200 | 401 | 401 |
| SSO token with app1 | 200 | 200 | 200 |
| SSO token without app1 | 200 | 403 | **403** |
| bad tokens | 200 | 401 | **401** |

JSON 403 body is exactly `{"detail":"forbidden","error":"permission_denied"}`. Every 303 points to `/login?next=…`.

### 3.3 Token verification (JWTV), design B2

`[test_qasso_jwt_verification.py]`: each case uses a token signed with the real QA key and **one** property
changed. Probe: `GET /usage`.

| ID | Case | Expected |
|---|---|---|
| JWTV-01 | Valid token with app1 | 200 |
| JWTV-02 (×2) | exp 20 s ago / 45 s ago | 200 / 401 (30 s leeway) |
| JWTV-03 (×12) | iat future, nbf future, exp missing, iat missing, token_type missing / `refresh` / `ACCESS`, aud missing, iss missing, aud near-miss, iss with trailing slash, exp as string | 401 |
| JWTV-04 | `aud` as an array containing `myfin-services` | 200 (RFC 7519; documents behaviour) |
| JWTV-05 (×3) | RS384, RS512, PS256 with the real key | 401 (RS256 only) |
| JWTV-06 (×4) | HS256 with the public key as secret (SPKI PEM, without newline, PKCS1, DER) | 401 |
| JWTV-07 (×4) | `alg` none/None/NONE/nOnE | 401 |
| JWTV-08 | Flipped signature bit; perms added to a no-perm token without re-signing | 401 |
| JWTV-09 | No `kid` (Q-14 part 2, still open: 200 or 401, never 500); `jku`/`x5u`/embedded `jwk` pointing at an attacker key | 401 for every header injection |
| JWTV-10 (×11) | Validly signed, malformed perms: string `"application.app1"`, `"xapplication.app1y"`, object `{"application.app1":true}`, nested list, wrong case, leading/trailing space, `application.app10`, `application.*`, `[]`, claim absent | 403 (or 401), **never 200**. Catches `in` on a string or dict |
| JWTV-11 | `perms:[]`, `is_staff:true`, groups incl. `admins` | 403 |
| JWTV-12 | Valid token of a deleted user (`sub` unknown) | 200 (stateless; documents the revocation window) |
| JWTV-13 | 6 tokens with random unknown kids within a few seconds | Each 401; SSO JWKS hits increase by ≤ 1 (≤ 1 refetch per 60 s) |
| JWTV-14 (×4) | `bearer` / `BEARER` / double space / trailing space | 200 or 401, never 500 |
| JWTV-15 | 12 KB claim padding | 200/400/401/413/431, never 500 |
| JWTV-16 | Three-part non-JWTs (`a.b.c`, `..`, …) on the sso/both instances (static token is not JWT-shaped there) | 401 (fallback compare fails); the JWT-shaped static token case is FB-* |
| JWTV-17 | Instance requiring `application.qa_app2` | qa_app2-only user 200; app1-only user 403; mixed user 200 |
| JWTV-M1 *Manual* | JWKS key rotation: add a second key at the SSO, sign with it, restart the SSO only | llm-wiki accepts the new kid after ≤ 1 refetch; tokens with the old kid keep working while it is published |
| JWTV-M2 *Manual* | JWKS cache 1 h: stop the SSO after the first verification | Valid tokens keep verifying while the SSO is down (cache), new kids fail cleanly with 401 |

### 3.4 Static-token fallback in `both` mode (FB), v1.1 item 6 (D-09 / Q-16)

`[test_qasso_fallback.py]` Instance `dotted` (8042, `both`): its `INGEST_API_TOKEN` is a base64url JSON header
(`alg: RS256`) + payload + random signature, so it parses as a JWT and fails verification.

| ID | Case | Expected |
|---|---|---|
| FB-01 | Harness check | The token parses as a JWT, ≥ 24 chars |
| FB-02 (×9) | JWT-shaped static token on every side-effect-free protected probe | Allowed (snapshot status) |
| FB-03 | Same token on `/dashboard` and MCP initialize | 200 / 200 |
| FB-04 (×5) | Near misses: last char flipped, dropped, appended, other signature, case-swapped | 401 |
| FB-05 | Valid SSO token with app1 | 200 (SSO path still first) |
| FB-06 (×3) | **Verified** SSO tokens without app1 (none, staff, other app) | 403 exact body: no fallback to a static compare (neither 401 nor 200) |
| FB-07 (×6) | Expired, wrong aud, wrong iss, attacker key, alg none, refresh token | 401 with `WWW-Authenticate: Bearer` |
| FB-08 | `sso` instance: plain and JWT-shaped static tokens | 401 (no fallback in sso mode) |
| FB-09 | JWT-shaped token of `dotted` sent to the regular `both` instance | 401 |
| FB-10 | Browser login on `dotted` | Normal session (fallback is Bearer-only) |
| FB-M1 *Manual* | Code review | Fallback compare is the same constant-time `hmac.compare_digest` path; no timing difference between "JWT failed" and "not a JWT" worth exploiting |

### 3.5 401 vs 403, HTML vs JSON (SEM), design B2/B3/B4 + v1.1 item 7

`[test_qasso_401_403.py]`

| ID | Case | Expected |
|---|---|---|
| SEM-01 | SSO token without app1 on a JSON route | 403, exact body, no `WWW-Authenticate` |
| SEM-02 (×3) | Missing, non-JWT, malformed bearer | 401 + `WWW-Authenticate: Bearer` (`error="invalid_token"` when a token was sent) |
| SEM-03 | Bad token / refresh token as bearer | 401, never `permission_denied` |
| SEM-04 | Unauthenticated `/dashboard` vs `/usage` with `Accept: text/html` | 303 `/login?next=/dashboard` / 401 |
| SEM-05 | `/dashboard?month=2026-10` | `next` keeps the query |
| SEM-06 | Bearer without app1 on `/dashboard` | 403 |
| SEM-07 | `sso` + PROTECT_READS | `/healthz` 200, `/login` 200, `/domains` 401 |
| SEM-08 | JSON route, no-perm token, `Accept: text/html` | JSON 403 |
| SEM-09 | Login as a user without app1 | No session row, no cookie |
| SEM-10 (×3) **v1.1** | `/dashboard` with `Bearer garbage`, `Bearer a.b.c`, `Basic …` (sso) | **401**; without credentials 303 (Q-07 resolved) |
| SEM-11 **v1.1** | `/dashboard` with an expired SSO bearer | 401 |
| SEM-12 **v1.1** | Invalid Bearer + valid session cookie (JSON and HTML) | 401: the explicit Bearer decides (Q-31: implied by item 7, not stated for JSON) |

### 3.6 Login page (LOGIN) against the real SSO, design B4 + v1.1 items 4/5

`[test_qasso_login.py]` Instance `sso` (8034). LOGIN-01..14 and 18 unchanged from v1.0.

| ID | Steps | Expected |
|---|---|---|
| LOGIN-01 | `GET /login?next=/dashboard` | 200 HTML; form with username/email + password + hidden CSRF; login CSRF cookie (SameSite=Strict); no session cookie |
| LOGIN-02 (×3) | Direct, group, superuser log in | 303 `/dashboard`; `llmwiki_session` set; "Signed in as <user>" + "Log out" |
| LOGIN-03 | Email as login | Success |
| LOGIN-04 | `next=/usage?month=2026-10` | 303 to exactly that path; session works on the JSON route |
| LOGIN-05 | Wrong password | No session; "Wrong username or password"; password not echoed |
| LOGIN-06 | Unknown user vs known user wrong password | Identical normalised page |
| LOGIN-07 | Inactive, correct password | "Please activate your account…" |
| LOGIN-08 | Inactive, wrong password | "Wrong username or password" |
| LOGIN-09 (×4) | Users without app1 | No-access message; no session |
| LOGIN-10 | User without app1 | Exactly one SSO `/logout` (refresh token discarded) |
| LOGIN-11 (×5) | Degenerate input | Never 500, no session |
| LOGIN-12/13 | Reflected username / `next` | Escaped |
| LOGIN-14 | Successful login response | No JWT in body or `Location` |
| LOGIN-15 **v1.1** | Login through the stub from source 127.0.0.61 | SSO call has `client:"native"`, the username, `client_ip:"127.0.0.61"` and HTTP Basic as `qa-wiki-login` |
| LOGIN-15b **v1.1** | Refresh triggered from 127.0.0.62 | `/refresh` call also carries Basic `qa-wiki-login` and `client_ip:"127.0.0.62"` |
| LOGIN-16 | Signed-in user visits `/login` | Skip(Q-08, still open) |
| LOGIN-17 **v1.1** | `/login` with `AUTH_MODE=static` | **404** (Q-01 resolved; also REG-12) |
| LOGIN-18 | SSO down; `GET /login` | 200 |

### 3.7 SSO failure handling at login (LERR), driven by the stub SSO, design B4

`[test_qasso_login_sso_errors.py]` Instance 8032 → stub 8033.

| ID | Stub mode | Expected (always: no session, no traceback, no JWT in the page) |
|---|---|---|
| LERR-01a | 401 `invalid_credentials` | "Wrong username or password"; raw SSO text not shown |
| LERR-01b | 403 `account_inactive` | "Please activate your account…" |
| LERR-02 | 429, `Retry-After: 42` | "Too many attempts, try again in 42 s" |
| LERR-03 (×3) | Connection closed / 500 / 200 with non-JSON | 502 page "Login service unavailable" (5xx and garbage treated like unreachable, Q-09) |
| LERR-04 (slow) | SSO hangs 25 s | 502 "Login service unavailable" after 9 to 14 s (10 s httpx timeout) |
| LERR-05 | 400 `invalid_request` (unmapped code) | Generic message; SSO detail not leaked (Q-09) |
| LERR-06 | 200 with tokens signed by an attacker key | No session: llm-wiki verifies the returned token (B4) |
| LERR-07 | `invalid_credentials` for known and unknown usernames | Identical outcome |
| LERR-08 | Minted (validly signed) tokens | Control case: session created |
| LERR-M1 *Manual* | SSO stopped entirely (llm-wiki pointed at 8038) | Same as LERR-03, quickly (connection refused) |

### 3.8 Open redirect (REDIR), design B4 "only same-origin relative paths; otherwise /dashboard"

`[test_qasso_open_redirect.py]`: a full successful login per payload. `Location` is resolved the way a
browser does (tab, CR and LF stripped, C0 and space trimmed, `\` read as `/`, cross-scheme `https:host`).

| ID | Cases | Expected |
|---|---|---|
| REDIR-01 (×31) | `https://evil.example/x`, `http://evil.example`, uppercase scheme, `//evil`, `///evil`, `////evil`, `\\evil`, `/\evil`, `\/evil`, `/\/evil`, `/%2F/evil`, `%2F%2Fevil`, `/%5Cevil`, `%5C%5Cevil`, `/<TAB>/evil`, `/<LF>/evil`, `/%09/evil`, leading space, leading NUL, `javascript:alert(1)`, mixed case, `java<TAB>script:`, `data:text/html,…`, `https:evil.example`, `http:evil.example`, `//evil@127.0.0.1`, nested `/login?next=https://evil`, `https://evil%2F@`, `//%65vil.example`, ideographic full stop variants | Resolves to the llm-wiki origin (normally exactly `/dashboard`); never `javascript:`/`data:` |
| REDIR-02 (×4) | `/dashboard`, `/usage?month=2026-10`, `/page/some-slug`, `/dashboard#frag` | Kept as is (fragment may be dropped) |
| REDIR-03 | GET with `next=/dashboard`, hidden field tampered to `https://evil.example` on POST | The POSTed value is validated too |
| REDIR-04 | `POST /login?next=https%3A%2F%2Fevil.example` | Validated |
| REDIR-05 | `POST /logout?next=https://evil.example` | Always 303 to `/login` |

### 3.9 Session cookie (COOKIE), v1.1 item 2: opaque id only

`[test_qasso_cookies.py]`

| ID | Case | Expected |
|---|---|---|
| COOKIE-01 | `llmwiki_session` Set-Cookie | `HttpOnly`; `Secure` per setting; `SameSite=Lax`; `Path=/`; no `Domain` |
| COOKIE-02 **v1.1** | Lifetime | `Max-Age`, if set, ≤ `SESSION_MAX_AGE_HOURS` × 3600 (exact value unspecified: Q-26) |
| COOKIE-03 **v1.1** | Content | Opaque: not a JWT, not `b64(json)`, no username/token inside, **≥ 256 bits** of id (43 b64url / 64 hex chars) |
| COOKIE-04 **v1.1** | Flipped char, all-`A`, empty, 5 000 chars, path/SQL-ish junk | `/usage` 401, `/dashboard` 303, never 500 |
| COOKIE-05 **v1.1** | Three logins | Three different ids |
| COOKIE-06 **v1.1** | Size for direct, 40-groups and superuser users | Same length for all; `name=value` < 200 B, Set-Cookie < 400 B (D-05 resolved) |
| COOKIE-07 | Cookie of one instance sent to another | 401 (separate session DBs) |
| COOKIE-08 **v1.1** | Session fixation: planted `llmwiki_session`, then login; then re-login | New id each time; the planted value is not a session |
| COOKIE-09 | Failed re-login while signed in | Skip(Q-10, still open) |
| COOKIE-M1 *Manual* | Real browsers | As designed; local-dev recipe sets `SESSION_COOKIE_SECURE=false` |

(v1.0 COOKIE-02 "Max-Age = refresh lifetime", COOKIE-03 "decode the signed cookie", COOKIE-05 "tamper the payload"
and COOKIE-08 "forge with SESSION_SECRET" are removed: there is no signed payload any more.)

### 3.10 CSRF (CSRF), design B4 + v1.1 item 3

`[test_qasso_csrf.py]`. Login form: double submit (unchanged). Signed in: the session's csrf value, exposed as
`<meta name="csrf-token">` on the dashboard, is required as `X-CSRF-Token` on every cookie-authenticated
non-GET request (forms: hidden field). Bearer-authenticated requests are exempt.

| ID | Case | Expected |
|---|---|---|
| CSRF-01 | Login POST without the hidden field | 400 or 403, no session |
| CSRF-02 | Without the CSRF cookie | Rejected |
| CSRF-03 | Field ≠ cookie | Rejected |
| CSRF-04 | Field from browser A, cookie from browser B | Rejected (token bound to the client) |
| CSRF-05 | Both empty | Rejected (classic "" == "" bug) |
| CSRF-06 | Rejected login | Never reaches the SSO (access log unchanged) |
| CSRF-07 | 3 fresh login pages | 3 different tokens, each ≥ 22 chars |
| CSRF-08 | `POST /logout` without a token | 400 or 403; session still valid |
| CSRF-09 | `GET /logout` | 404 or 405; session still valid (no logout via `<img>`) |
| CSRF-10 | CSRF cookie attributes | `SameSite=Strict` |
| CSRF-11 (×9) **v1.1** | Every protected non-GET route with the session cookie and **no** `X-CSRF-Token` (incl. deny-only `/worker/resume`, `/synthesize`, sent only like this) | 403 (or 400); never reaches the handler |
| CSRF-12 (×7) **v1.1** | Wrong token (reversed, truncated, all-`A`) | 403/400 |
| CSRF-13 (×7) **v1.1** | Token of another user's session, and of the same user's other session | 403/400 (per-session token) |
| CSRF-14 (×7) **v1.1** | Correct token | Authorised (snapshot status 422/404) |
| CSRF-15 (×14) **v1.1** | Static token and SSO Bearer writes with no `X-CSRF-Token` | Authorised (exempt) |
| CSRF-16 **v1.1** | Cookie GET without header | 200 |
| CSRF-17 **v1.1** | Two sessions' meta tokens | Different, ≥ 22 chars, stable for the session, not inside the cookie |
| CSRF-18 (×3) **v1.1** | Invalid Bearer + valid cookie, no header | Never authorised; 401 expected (Q-31) |
| CSRF-19 **v1.1** | Cross-site multipart `POST /upload` with the cookie and a foreign `Origin` | 403/400 |
| CSRF-20 **v1.1** | Empty `X-CSRF-Token` | 403/400 (`"" == ""` bug) |
| CSRF-21 **v1.1** | MCP POST with the session cookie and no token (sso) | 401/403 (if the MCP wrapper honours cookies, CSRF must apply) |
| CSRF-22 | csrf value as a form field on API writes | Skip(Q-30) |

### 3.11 Server-side sessions (SESS), design B4 + v1.1 items 2 and 7

`[test_qasso_session.py]`. Fast cases: stub `mint` mode on the 2-worker `stubbed` instance (access TTL 65 s,
window opens after ~5 s, refresh tokens single-use). Grey-box cases read/age the instance's own session DB.

| ID | Case | Expected |
|---|---|---|
| SESS-01 *grey* | After login, inspect the session table | Columns per item 2 (id hash, username, sub, access, refresh, access_exp, csrf, created_at, last_seen); a row keyed by SHA-256 of the cookie id |
| SESS-02 *grey* | Search the DB (+ WAL) for the raw cookie id | Absent; its SHA-256 present |
| SESS-03 *grey* | Tokens | access + refresh JWTs in the row, not in the cookie |
| SESS-04 *grey* | File mode of DB, `-wal`, `-shm` | **No world access** (0600 recommended; group access warned). Not in the design (Q-29); likely to fail with the default umask (D-16) |
| SESS-05 | Request inside the 60 s window | Exactly 1 SSO refresh; tokens rotated **in the row**; the session id need not change |
| SESS-06 | Access token with 600 s left | No refresh |
| SESS-07 (×2) | Refresh 401 / 403, HTML route | 303 `/login?next=…`, cookie cleared, **row deleted**, old cookie 401 afterwards |
| SESS-07b (×2) | Same, JSON route | 401 |
| SESS-08 (×2: 8, 20 requests) | Parallel requests with one cookie in the window, spread over 2 workers | **All 200, exactly 1 SSO refresh**, session alive afterwards (D-04 race) |
| SESS-08b | 10 parallel requests after the access token expired | All 200, 1 refresh |
| SESS-09 | Refresh returns a token without app1 (JSON) | 403 exact body; **row deleted**; old cookie then 401 / 303 (Q-06 resolved) |
| SESS-09b | Same on `/dashboard` | 403 HTML page |
| SESS-10 (×4) | SSO 500 / closed / garbage / hang during refresh, access token still valid | 200; session kept; next refresh after recovery works (Q-05 resolved) |
| SESS-11 (×2, slow) | SSO 500 / closed, access token past exp + 30 s leeway | JSON 503 and HTML 503 "Login service unavailable"; cookie and row **kept**; 200 once the SSO is back |
| SESS-12 | Access past exp inside the leeway | Refreshed transparently |
| SESS-13 | Copied cookie after logout in the other browser | 401 |
| SESS-14 *grey* | `created_at` moved to 168 h + 2 min ago | 401 / 303 (absolute lifetime) |
| SESS-15 *grey* | `created_at` 167 h 50 min ago | Still 200 |
| SESS-16 *grey* | Refresh | `created_at` unchanged (no sliding beyond the absolute lifetime) |
| SESS-17 *grey* | `last_seen` 6 days ago, `created_at` recent | Still 200: the design defines **no idle timeout** (Q-27) |
| SESS-18 *grey* | Use the session | `last_seen` updated |
| SESS-19 *restart* | Restart the `sso` instance by PID | The same cookie still works (sessions live in SQLite; item 2's "prune on start" implies persistence) |
| SESS-20 *restart, grey* | Age a row past the lifetime, restart | Row pruned on start |
| SESS-21 | Hourly prune | Skip (manual: needs an hour or a hook) |
| SESS-30 (slow) | Real SSO, rotation | Row's refresh token rotated; the old one is 401 at the SSO |
| SESS-31 (slow) | SSO `logout-all` with the row's access token | Works until the refresh window, then 303 and row deleted (D-10 accepted) |
| SESS-32 (slow) | Revoke app1 while signed in | 200 until the refresh, then 403 and session deleted |
| SESS-M1 *Manual* | Refresh-token expiry (`SSO_REFRESH_TTL_DAYS=1`) | 303 `/login`, row deleted |
| SESS-M2 *Manual* | Two uvicorn workers + `kill -STOP` one mid-refresh | The other worker waits (BEGIN IMMEDIATE) and then reuses the new token; no deadlock beyond the 10 s SSO timeout |

### 3.12 Logout (LOGOUT), design B4 + v1.1 items 2/3

`[test_qasso_logout.py]`

| ID | Case | Expected |
|---|---|---|
| LOGOUT-01 | Logout form | 303 `/login`; cookie deleted; **row deleted**; the row's refresh token is 401 at the SSO |
| LOGOUT-02 | `/dashboard` afterwards | 303 |
| LOGOUT-03 **v1.1** | Replay the cookie copied before logout | **401 / 303 immediately** (D-02 resolved) |
| LOGOUT-03b **v1.1** | Same user, two browsers; log out one | The other session continues |
| LOGOUT-04 (×3) | SSO logout 500 / closed / hang | Local logout < 14 s; row deleted anyway |
| LOGOUT-05 | Stub records the logout call | Body has the row's refresh token |
| LOGOUT-06 | Logout without a session | 303/400/403, never 500 |
| LOGOUT-07 | "Log out everywhere" | Skip(Q-15, still open) |
| LOGOUT-08 **v1.1** | Logout form | Hidden field == the session's csrf (meta tag) |

### 3.13 MCP, scripts, channels (MCP), design B5 + v1.1 item 1

`[test_qasso_mcp_static.py]`

| ID | Case | Expected |
|---|---|---|
| MCP-01 | static, no token | Works (unchanged) |
| MCP-02 | both, static token | Works |
| MCP-03 (×2) | both and sso, SSO token with app1 | Works |
| MCP-04 (×4) **v1.1** | sso: none / static / wrong static / garbage JWT | 401 (D-01 resolved) |
| MCP-04b (×3) **v1.1** | both: none / wrong static / garbage JWT | 401 (Q-02 resolved) |
| MCP-04c **v1.1** | 401 headers | `WWW-Authenticate: Bearer` |
| MCP-04d (×6) **v1.1** | `/mcp`, `/mcp/`, `/mcp/messages`, `/mcp/sse`, `/MCP/`, `/mcp//`, GET and POST, no token | 401/404/405 (or a 307 back to `/mcp/`); never a tool answer |
| MCP-05 (×2) **v1.1** | sso and both, token without app1 | 403 |
| MCP-06 (×2) | Scripts: `POST /ingest` with the static token, static and both | Authorised |
| MCP-07 | sso, static token on REST | 401 |
| MCP-08 | Webhook paths | Never a login redirect or SSO challenge |
| MCP-09 **v1.1** | After initialize, `tools/call` with the session id but no / expired bearer | 401 (auth per request) |
| MCP-M1 *Manual* | `scripts/README.md` `--sso` flow | As documented |

### 3.14 Regression: AUTH_MODE=static byte-for-byte (REG), design B3/B8 + v1.1 item 5

Snapshot as v1.0 (`tests/qa_sso/snapshots/static_routes_bca2bef.json`). Baseline 401 under uvicorn: headers
exactly `date`, `server`, `content-length`, `content-type: application/json`; body compact JSON.

| ID | Case | Expected |
|---|---|---|
| REG-01 **v1.1** | `/openapi.json` inventory | **Exactly** the snapshot routes (no `/login`, `/logout`) |
| REG-02 (×44) **v1.1** | Protected probe × {none, wrong bearer, Basic, JWT-looking} | 401, exact body bytes, **exact header set** (no `WWW-Authenticate`, no Set-Cookie) |
| REG-03 (×9) | Static token | Snapshot status |
| REG-04 **v1.1** | Valid SSO superuser token | 401 exactly as a wrong static token (bytes + headers) |
| REG-05 **v1.1** | `/dashboard` none / wrong / `?token=` / Bearer / bad month | 401 exact bytes + headers `{"detail":"invalid or missing token"}` / 401 / 200 / 200 / 422 |
| REG-06 (×24) | Reads + healthz × credentials | Snapshot status |
| REG-07 | `/healthz` shape | Same keys |
| REG-08 | Any response | No Set-Cookie |
| REG-09 | `/mcp` without token | 200 |
| REG-10 *static* **v1.1** | `git diff <merge-base main>` of existing tests | No modification (item 5: "revert … unless genuinely wrong, then explain"). **Currently FAILS**: `tests/unit/test_layering.py` modified (committed in the feature branch and modified again in the tree), no explanation yet (D-08 open) |
| REG-11 *static* | AST of `routes.py` vs snapshot | Protected still protected |
| REG-12 (×5) **v1.1** | `GET/POST /login`, `POST/GET /logout`, `GET /login?next=…` | 404 `{"detail":"Not Found"}`, no Set-Cookie |
| REG-13 (×7) **v1.1** | `static_reads` instance: each read route without / wrong / with the static token | 401 (baseline bytes + headers) / 401 / snapshot status (Q-03 resolved) |
| REG-14 **v1.1** | `static_reads`: `/healthz`, MCP | 200 / 200 (MCP stays open even with protected reads: C-12) |
| REG-15 **v1.1** | static instance log | `MCP is unauthenticated in AUTH_MODE=static` WARNING |
| REG-M1 *Manual* | Existing suite with `AUTH_MODE` unset on 3.11 and 3.13 | Green |

### 3.15 Startup and configuration (START), design B1 + v1.1 items 1, 2, 4, 6, 9

`[test_qasso_startup.py]`: throw-away processes on 8039, stopped by their process group; output captured.

| ID | Case | Expected |
|---|---|---|
| START-01 (×8) **v1.1** | sso/both missing `SSO_ISSUER`, `SSO_BASE_URL`, **`SSO_CLIENT_ID`**, **`SSO_CLIENT_SECRET`** | Exit ≠ 0 naming the variable (`SESSION_SECRET` variant removed) |
| START-02 (×4) | Invalid `AUTH_MODE` | Refuses |
| START-03 | static without any SSO setting | Serves |
| START-04 | `AUTH_MODE` unset | Serves; `/dashboard` 401 |
| START-05 (×6) **v1.1** | sso/both with `INGEST_API_TOKEN` = `changeme`, 23 chars, unset (code default) | Exit ≠ 0 naming `INGEST_API_TOKEN`, without echoing the value |
| START-05b (×2) **v1.1** | sso/both with exactly 24 chars | Serves |
| START-05c (×2) **v1.1** | static with `changeme` / 23 chars | Serves **and** logs a WARNING about `INGEST_API_TOKEN` |
| START-06 | SSO unreachable at startup | Serves |
| START-07 **v1.1** | static | Startup output has `MCP is unauthenticated in AUTH_MODE=static` |
| START-08 (×2) **v1.1** | sso/both | No such warning |
| START-09 (×2) **v1.1** | sso/both with a stray `SESSION_SECRET` set | Serves (dropped setting does not break startup) |
| START-10 (×2) **v1.1** | sso/both with `SESSION_DB_PATH` | Serves; DB at that path (xfail if created lazily at first login) |
| START-11 | Default DB path `<data dir>/sessions.sqlite3` | Skip(Q-32) |

### 3.16 Throttling (THR), design B4 + v1.1 item 4

`[test_qasso_zz_throttle.py]`, throttle phase only (SSO at 10/min IP, 5/min user, 60/min refresh). Each case uses
its own source address, so buckets never collide and no 61 s waits are needed.

| ID | Case | Expected |
|---|---|---|
| THR-01 | 6 wrong attempts for one user through llm-wiki | "Too many attempts, try again in N s" (1 ≤ N ≤ 60) |
| THR-02 | Correct password while throttled | Still throttled |
| THR-03 **v1.1** | Attacker 127.0.0.22 burns 11 attempts; victim from 127.0.0.23 logs in | **Victim signs in** (D-03 resolved by delegation) |
| THR-04 **v1.1** | Attacker's own 11th attempt | Throttled (per-IP still applies to the real end user) |
| THR-05 **v1.1** | Browser rotates `X-Forwarded-For` / `X-Real-IP` / `CF-Connecting-IP` | Still throttled on 11th: forwarded headers from an untrusted peer are ignored (Q-25) |
| THR-10 **v1.1** | SSO directly, no Basic, rotating `client_ip` | 11th → 429 (field ignored) |
| THR-11 (×4) **v1.1** | Wrong secret / inactive client / unknown client / garbage Basic + rotating `client_ip` | First 10 → 401 (login not refused for bad Basic), 11th → 429 |
| THR-12 **v1.1** | Valid ServiceClient, 13 logins with distinct `client_ip` | No 429 |
| THR-13 **v1.1** | Valid ServiceClient, same `client_ip` 11× | 11th 429; the caller's own bucket untouched |
| THR-14 (×5) **v1.1** | Valid ServiceClient, rotating invalid `client_ip` (word, 999.x, blanks, CIDR, comma list) | Only 400/401/429; 11th is 400 or 429 (never fresh buckets; Q-28) |
| THR-15 **v1.1** | SSO login log | `ip` = `client_ip` when delegated, caller address otherwise |
| THR-16 **v1.1** | Per-username limit with delegation and rotating `client_ip` | 6th → 429 (unchanged) |
| THR-17 **v1.1** | `/refresh` 61× delegated with distinct `client_ip` vs 61× plain | No 429 / 61st 429 |
| THR-M1 *Manual* | Behind cloudflared (deployment) | llm-wiki sees the real browser IP only if uvicorn runs with `--proxy-headers --forwarded-allow-ips=<cloudflared>`; otherwise every user is one IP again (C-11) |

### 3.17 Logging (LOG)

`[test_qasso_zy_logs.py]`, after everything else, all llm-wiki logs at DEBUG.

| ID | Case | Expected |
|---|---|---|
| LOG-01 | JWT pattern | Absent |
| LOG-02 | QA passwords | Absent |
| LOG-03 **v1.1** | Static tokens (incl. the JWT-shaped one) | Absent |
| LOG-04 | `llmwiki_session=<value>` | Absent |
| LOG-05 **v1.1** | `SSO_CLIENT_SECRET` and its Basic header | Absent (replaces the SESSION_SECRET case) |
| LOG-06 **v1.1** | `X-CSRF-Token: <value>` | Absent |

### 3.18 Manual checklist

| ID | Item |
|---|---|
| M-01 **v1.1** | README / `.env.example`: B1 variables plus `SSO_CLIENT_ID/SECRET`, `SESSION_DB_PATH`, `SESSION_MAX_AGE_HOURS`; **no** `SESSION_SECRET`; static-token rule (≥ 24, not `changeme`) |
| M-02 | "How to grant application.app1" and "how to add app2", followed literally |
| M-03 **v1.1** | SSO README: shared-content-type warning **and** the Q-13 rule (command-created permissions are not recreated) |
| M-04 | Local dev recipe for both services (never port 8000) |
| M-05 **v1.1** | Spec §5.1/§6.1/§8.1: `perms` always present; `client_ip` + Basic on login/refresh; `SSO_SUPERUSER_ALL_APPS` |
| M-06 | `scripts/README.md` `--sso` flow |
| M-07 | Cloudflare Access interplay |
| M-08 | Constant-time compare and `mask()` kept (also on the fallback path) |
| M-09 | Dashboard/login UX |
| M-10 **v1.1** | Docs say: multiple uvicorn workers share the SQLite file, the DB transaction is the real guard; session DB should be on local disk (not a network share) |
| M-11 **v1.1** | Registering llm-wiki's ServiceClient at the SSO (`create_service_client` or admin) is documented with the secret handling |

### 3.19 Regression round @ 6520890 (NS, RED, ROT, SID) - `test_qasso_r2_fixes.py`

| ID | Precondition | Steps | Expected |
|---|---|---|---|
| NS-01 (x2: sso, both) | real SSO, `qa_wiki_direct` | GET /login; POST wrong pw; POST ok; GET /login signed in; GET /dashboard; POST /logout; anon / bad-Bearer /dashboard; GET /logout; /login?next=//evil | every response has exactly one `Cache-Control: no-store` |
| NS-02 | sso | /healthz, /usage, /domains with a valid Bearer | no `no-store` (only the three pages) |
| NS-03 (x2: static, static_reads) | static | /dashboard ?token / Bearer / none / bad; /login; POST /logout | no `Cache-Control` header at all (Q-20 sso/both only) |
| NS-04 (x20) | sso, both, static, static_reads | GET /dashboard/, GET /login/, POST /logout/, GET /logout/, GET /dashboard/?month= | sso/both: 307 to the slash-less path (query kept) with exactly one `Cache-Control: no-store`; static: /dashboard/ 307 with no Cache-Control, /login/ and /logout/ 404 with none (ba2097f) |
| NS-05 (x2) | sso, static | GET /Dashboard | 404 |
| RED-01 (x2: static, both) | instance log file | `?token=`, `&token=`, `TOKEN=`, `access_token=`, `;token=`, repeated, on 200/401/404/405 routes | `[redacted]` present; neither the static token nor the probe secret in any new log line |
| RED-02 (x24) | static, both | `/dashboard?month=..&<name>=<static>` for 12 names: `%74oken`, `%54OKEN`, `t%6Fken`, `T%6fKeN`, `%74%6F%6B%65%6E`, `access%5Ftoken`, `Access_%54oken`, `+token`, and double-encoded `%2574oken`, `t%256Fken`, `%25%37%34oken`, `%2574%256Fken` | status unchanged (only a name decoding to exactly `token` authenticates: 200; others 401 static / 303 both); the token is in no log file |
| RED-03 (x2) | static, both | POST /compile/<malformed>?token= (server-error path) | no token in the log, traceback included |
| ROT-01 (x2: same user, other user) | sso, session DB (grey) | log in, record cookie/CSRF/refresh; log in again in the same browser | 303, new id and CSRF; old row deleted; row count unchanged; old cookie: JSON 401, HTML 303 → /login; new cookie works; SSO `/auth/refresh` with the old refresh token → 401 |
| ROT-02 | sso | wrong password while signed in | no rotation, same cookie still works (Q-10) |
| ROT-03 | stub, logout mode `server_error` then `close` | re-login | login still succeeds, old row deleted, old refresh token sent to SSO logout once (best effort) |
| ROT-04 | sso | planted unknown cookie, then log in | replaced; the planted id stays dead |
| SID-01 (x12) | static, both | GET /sources/<bad> for 6 malformed ids | 404, no traceback |
| SID-02 (x2) | static, both | GET /sources/<well-formed unknown> | 200 `state: failed, error: unknown source`, same as bca2bef (static unchanged); **decided by Philip 2026-10-11: stays 200 for static compatibility** |
| SID-03 (x6) | static, both | POST /compile/<bad> (static token) | 404, same as GET /sources |
| SID-04 (x6) | static, both | MCP `tools/call compile_update` with a bad id | `isError: true`, "not a valid source_id", no traceback |
| SID-05 | static | MCP `tools/list` | the seven tools; only `compile_update` takes a source id |
| SID-06 (x6) | CLI, offline backends | `llmwiki compile|source <bad>` (incl. `""`, `../../etc/passwd`) | exit ≠ 0, exactly one stderr line, empty stdout, no traceback |
| SID-07 (x2) / 07b (x2) / 07c / 07d | static, both, CLI | POST /compile/<well-formed unknown>; MCP compile_update; `llmwiki compile`; `llmwiki source` | 404 `{"detail":"no such source: '<id>'"}`; MCP `isError` "no such source"; CLI exit 1 with one error line (besides INFO logging), no traceback; `source` keeps the unknown-source status, exit 0 |

## 4. Case counts (v1.2)

Regression @ 6520890: +51 items in `test_qasso_r2_fixes.py` (NS 8, RED 5, ROT 5, SID 33); the suite collected 1,249 items.
Quick check @ ba2097f: `test_qasso_r2_fixes.py` has 99 items (NS 27, RED 28, ROT 5, SID 39); the suite collects **1,297** items.

| Area (file) | Test functions | pytest items |
|---|---|---|
| SSO perms claim (PERM, `sso_perms`) | 20 | 47 |
| Refresh recomputes perms (PREF) | 7 | 7 |
| App-permission command (APPCMD) | 10 | 23 |
| AUTH_MODE matrix (MATRIX) | 1 | 630 (6 instances x 5 kinds x 21 creds; n/a cells skip) |
| Token verification (JWTV) | 17 | 49 |
| Static fallback (FB) | 10 | 29 |
| 401 vs 403 (SEM) | 12 | 16 |
| Login page (LOGIN) | 19 | 28 |
| SSO failure mapping (LERR) | 9 | 12 |
| Open redirect (REDIR) | 5 | 38 |
| CSRF | 23 | 65 |
| Cookies (COOKIE) | 10 | 10 |
| Server-side sessions (SESS) | 29 | 37 |
| Logout (LOGOUT) | 9 | 11 |
| MCP / scripts / channels (MCP) | 13 | 27 |
| Static regression (REG) | 16 | 100 |
| Startup (START) | 18 | 43 |
| Logs (LOG) | 6 | 6 |
| Throttle (THR, separate phase) | 13 | 20 |
| **Total** | **247** | **1,198** |

Remaining skips by design: LOGOUT-07 (Q-15, no logout-all in llm-wiki), SESS-21 (hourly prune, manual), matrix n/a
cells, and phase-gated cases (throttle vs functional). Manual checklist unchanged (24 items).

## 5. Traceability: design requirement → tests

| Req | Design text (short) | Tests | Gap? |
|---|---|---|---|
| T-1 | Login page through SSO | LOGIN, LERR, CSRF, REDIR, COOKIE, SESS, LOGOUT | – |
| T-2 | `application.app1`, central control | PERM, PREF, APPCMD, JWTV-10/11/17, MATRIX | – |
| A1.1-1.5 | App, anchor, command, admin, migration | APPCMD-01..09, PERM-02/03, APPCMD-M1 | Admin UI manual |
| A2.1-2.7 | `perms` in token/user/`/me`/introspect; rules; refresh | PERM-01..16, PREF-01..07, SESS-32 | – |
| A3 | Shared-table warning, post-migrate | APPCMD-06/10, M-03 | – |
| B1 | Settings, fail fast | START-01..04, -06, MATRIX configs, JWTV-17 | `SSO_JWKS_URL`/`SSO_AUDIENCE` overrides (G-1) |
| B2 | RS256, claims, leeway, JWKS cache, 403 body | JWTV-*, SEM-01, MATRIX | 1 h cache manual (G-2) |
| B3.1 | JWT → SSO else static | MATRIX, JWTV-16, FB-* | – |
| B3.3 | Session cookie on API routes | MATRIX `session_ok`, CSRF-14 | – |
| B3.4 | 401 + `WWW-Authenticate` (sso/both) | SEM-02, MATRIX, MCP-04c, FB-07 | – |
| B3.5 | Never log tokens | LOG-01..06 | – |
| B4.1-4.6 | Login form, SSO call, verification, no-perm, `next`, error mapping | LOGIN, LERR, REDIR, THR-01/02 | 5xx at login (Q-09 partly; C-14) |
| B4.12-4.14 | Logout, dashboard, "Signed in as" | LOGOUT, MATRIX dashboard, SEM-04..06 | – |
| B5 | Channels, scripts | MCP-06..08, MCP-M1 | – |
| B6/B7 | Pins, developer tests | REG-M1 | Manual (G-5) |
| B8 | Existing tests unchanged | REG-10, existing suite run | D-08 resolved (9f6a15a) |
| **v1.1-1** | MCP auth wrapper (sso: SSO+perm; both: SSO or static); static open + WARNING | MATRIX mcp, MCP-01..05, -04b/c/d, -09, CSRF-21, START-07/08, REG-09/14/15 | – |
| **v1.1-2a** | Cookie = random 256-bit id only | COOKIE-03..06/08 | – |
| **v1.1-2b** | SQLite store, columns, SHA-256 id hash | SESS-01..03 | v1.2 D-16: 0600 (SESS-04, START-13/13b) |
| **v1.1-2c** | Logout / refresh failure delete the row | LOGOUT-01/03/04, SESS-07/13 | – |
| **v1.1-2d** | Absolute `SESSION_MAX_AGE_HOURS` (168), no sliding | SESS-14/15/16, COOKIE-02 | v1.2: idle 12 h (SESS-17), Max-Age remaining (COOKIE-02/02b) |
| **v1.1-2e** | Prune on start and hourly | SESS-20, SESS-21 (manual) | Hourly: manual (G-11) |
| **v1.1-2f** | Per-session lock + BEGIN IMMEDIATE, multi-worker | SESS-05/08/08b, SESS-M2 | – |
| **v1.1-3** | X-CSRF-Token on cookie non-GET; meta tag; hidden field; Bearer exempt | CSRF-11..21, LOGOUT-08, MATRIX `session_ok` | v1.2: 403 csrf_failed, unsafe methods only (CSRF-23) |
| **v1.1-4a** | SSO: Basic ServiceClient + `client_ip` → throttle + log; otherwise ignored; validated | THR-10..17 | v1.2: 400 / 401 invalid_client (THR-11/14) |
| **v1.1-4b** | llm-wiki sends `SSO_CLIENT_ID/SECRET` (required) + browser IP | LOGIN-15/15b, START-01, THR-03/04/05, LOG-05 | v1.2 C-11: START-12/14 |
| **v1.1-5** | Static byte-for-byte; no `/login`; PROTECT_READS with static token; revert test edits | REG-01..15, LOGIN-17, MATRIX static columns | – |
| **v1.1-6a** | Fallback to static compare after failed JWT verification (both) | FB-01..10 | Constant-time review (FB-M1) |
| **v1.1-6b** | Refuse `changeme` / < 24 chars in sso/both, warn in static | START-05/05b/05c | – |
| **v1.1-7a** | SSO down during refresh: continue / 503, keep session | SESS-10/11 | – |
| **v1.1-7b** | Refresh without perm → delete session, 403 page | SESS-09/09b, SESS-32 | – |
| **v1.1-7c** | HTML route: invalid explicit Bearer 401, none 303 | SEM-10/11/12, MATRIX dashboard | v1.2 Q-31 (SEM-12) |
| **v1.1-8** | `perms` always list; `SSO_SUPERUSER_ALL_APPS`; Q-13 rule | PERM-01/06/16..20, APPCMD-06/10 | – |
| **v1.1-9** | `SESSION_SECRET` dropped; revocation window accepted | START-09, SESS-31/32 | – |

**Traceability gaps:** G-1 JWKS URL / audience overrides; G-2 1 h JWKS cache (manual); G-3 `Principal.via`
(internal); G-4 constant-time compare (review, now also on the fallback path); G-5 Python 3.13 re-run; G-6
refresh-token expiry (SESS-M1); G-7 real browsers (COOKIE-M1); G-8 key rotation (JWTV-M1); G-9 admin UI; G-10
docs (M-*); **G-11 hourly prune (SESS-21 manual)**; **G-12 production client IP behind cloudflared (THR-M1)**.

## 6. Defects and design findings

### 6.1 Status of the v1.0 findings

| ID | v1.0 finding | v1.1 status |
|---|---|---|
| D-01 | `/mcp` unauthenticated in sso/both | **Resolved by design** (item 1). Verify: MCP-04/04b/04d/09, MATRIX |
| D-02 | Refresh race → random logouts | **Resolved by design** (item 2 lock + transaction). Verify: SESS-08/08b (2 workers) |
| D-03 | SSO throttle sees llm-wiki's IP | **Resolved by design** locally (item 4). Verify: THR-03..17. Production caveat: C-11 |
| D-04 | Copied cookie valid after logout; refresh token readable | **Resolved by design** (item 2). Verify: LOGOUT-03, SESS-13, COOKIE-03 |
| D-05 | Cookie > 4 KB with many groups | **Resolved by design**. Verify: COOKIE-06 |
| D-06 | CSRF on cookie-authenticated writes | **Resolved by design** (item 3). Verify: CSRF-11..21 |
| D-07 | `WWW-Authenticate` in static | **Resolved**: static stays byte-for-byte (no header). Verify: REG-02/04/05 |
| D-08 | `tests/unit/test_layering.py` edited | **Resolved** (9f6a15a revert; file identical to bca2bef; REG-10 passes) |
| D-09 | Dotted static token in both | **Resolved by design** (item 6). Verify: FB-* |
| D-10 | Revocation latency | **Accepted** (item 9) |
| D-11 | Superuser ⇒ every app | **Kept, switchable** (`SSO_SUPERUSER_ALL_APPS`, default true), pending Thomas. Verify: PERM-17..20 |
| D-12 | Inactive/no-access messages confirm the password | Info, unchanged |
| D-13 | Sliding sessions | **Resolved**: absolute 168 h. Verify: SESS-14..16 |
| D-14 | `/answer` open by default | Info, unchanged |
| D-15 | `SESSION_SECRET` ≥ 32 chars in code | **Moot** (dropped) |

### 6.2 Conflicts and risks raised on v1.1 (status after v1.2 in the last column of the results doc)

| ID | Sev | Finding | Tests |
|---|---|---|---|
| C-11 | **Medium** | **"Sends the browser's IP" is undefined behind a proxy.** llm-wiki takes `request.client.host` (the developer's `client_ip_of` relies on `uvicorn --proxy-headers`). Behind cloudflared that is the tunnel container unless uvicorn runs with `--forwarded-allow-ips=<cloudflared address>`; then every user is one IP again and D-03 returns in production. Trusting `X-Forwarded-For`/`CF-Connecting-IP` from any peer would let users pick their own throttle bucket. Needs a deployment setting and a doc line | THR-05, THR-M1 |
| D-16 | **Medium (likely)** | **Session store file mode unspecified.** The SQLite file holds live access **and refresh tokens in clear**; with the default umask it is created `0644` (and `-wal`/`-shm` likewise), readable by any local user or container sidecar sharing the volume. Recommend `0600` file / `0700` dir, or encrypt the refresh column | SESS-04 (Q-29) |
| C-12 | Low | Static mode: `SSO_PROTECT_READS=true` protects the REST read routes with the static token, but MCP stays fully open (incl. write tools) in static mode, so "protect reads" is not true for the MCP read tools (`search_wiki`, `get_page`). Accepted risk? (item 1 only adds a warning) | REG-14 |
| C-13 | Low | Item 3 says "any **non-GET** request": that includes HEAD and OPTIONS. Suggest "unsafe methods (POST/PUT/PATCH/DELETE)", and state the failure status/body (Q-30) | CSRF-11 |
| C-14 | Low | Status inconsistency: SSO unreachable at **login** → 502 (B4), during **refresh** → 503 (item 7), both "Login service unavailable". Pick one | LERR-03, SESS-11 |
| C-15 | Low | The v1.1 section is **uncommitted in both repos** (identical today). sso-server already committed code for items 4 and 8 (120b550, 6476a22, 17d06cd) while its design copy is still a working-tree change; commit both copies together to avoid drift | – |
| C-16 | Info | No idle timeout: `last_seen` is stored but unused; a stolen cookie works for up to 7 days even if the user never comes back. Intended? | SESS-17 (Q-27) |
| C-17 | Info | Superuser rule is "pending Thomas's confirmation": tests cover both settings; the default must be re-confirmed before release | PERM-17..20 |
| C-18 | Info | A valid ServiceClient can claim any `client_ip`, so a leaked `SSO_CLIENT_SECRET` disables the per-IP throttle for the attacker (per-username stays). Rotate/scope the secret; consider rate-limiting per ServiceClient as a ceiling | THR-12 |
| C-19 | Info | Invalid Basic on `/login` is silently ignored by the SSO (logged once as `service_client_auth invalid`). A mistyped `SSO_CLIENT_SECRET` in llm-wiki degrades to D-03 silently; suggest llm-wiki checks the credentials at startup or warns | THR-11 |

### 6.3 v1.2 status of C-11..C-19 and D-16

D-16 answered (0600/0700/refuse). C-11 answered (`CLIENT_IP_HEADER` + `TRUSTED_PROXY_IPS`). C-12 answered (static token
on `/mcp`). C-13 answered (unsafe methods). C-14 answered (503). C-15 closed (doc committed, identical). C-16 answered
(idle 12 h). C-17 still pending Thomas (both settings tested). C-18 accepted risk. C-19 answered (401 invalid_client →
503 "misconfigured"). Test outcomes: `SSO-LOGIN-RESULTS.md`.

## 7. Open questions

Resolved by v1.1: Q-01..07, Q-11, Q-13, Q-14 part 1, Q-16, Q-17 (pending Thomas), Q-18, Q-22. Resolved by v1.2:
Q-14 (missing kid → reject), Q-25, Q-26, Q-27, Q-28, Q-29 (= D-16), Q-30, Q-31.

Decided at the regression round @ 6520890 (Philip, 2026-10-10): Q-08, Q-10, Q-12, Q-15, Q-19, Q-20, Q-21, Q-23, Q-32
(rows below kept for traceability). Still pending: Q-33 `SSO_SUPERUSER_ALL_APPS` default (Thomas).

| Q | Question | What the tests do now |
|---|---|---|
| Q-08 | `GET /login` while signed in | **Decided (current behaviour confirmed)**: 303 to the sanitised `next`; LOGIN-16 asserts 303 → `/dashboard` for an off-site next |
| Q-10 | Failed re-login while signed in | **Decided (current behaviour confirmed)**: session kept; COOKIE-09/ROT-02 assert same cookie, still valid |
| Q-12 | Refresh token carries `perms`? | **Accepted as is**: it does not (PERM-14 passes), and a refresh token is rejected as a Bearer (FB-07 `refresh_type`) |
| Q-15 | "Log out everywhere" in llm-wiki | **Decided: out of scope** (LOGOUT-07 skipped with that reason) |
| Q-19 / Q-23 | Status codes / format of failed-login and HTML 403 pages | **Accepted as is** (401/403 HTML, no-store, no account enumeration: LOGIN-06/09, NS-01) |
| Q-20 | `Cache-Control: no-store` on `/login`, `/dashboard` | **Decided: yes in sso/both, not in static** (NS-01..04) |
| Q-21 | llm-wiki's own `/login` rate limit | **Decided: none**; relies on the SSO throttle (THR) |
| Q-32 | Session DB default path for non-local storage backends | **Decided: data-dir default, documented**; START-11 |
| Q-33 (new) | Superuser-gets-all default (`SSO_SUPERUSER_ALL_APPS`) | Pending Thomas; PERM-17..20 run both settings |

## 8. Entry and exit criteria

- **Entry:** sso-server `feat/app-permissions` with 120b550/6476a22 deployed in the QA stack; llm-wiki
  `feat/sso-login` with v1.1 implemented and committed (or at least a stable tree); ServiceClient `qa-wiki-login`
  seeded; `tests/qa_sso/run/start_all.sh` healthy (11 processes).
- **Exit:** no failing High/Medium case without an accepted waiver (currently D-08, D-16, C-11 need a decision);
  REG green; every open question answered or deferred; manual checklist done; results in
  `docs/qa/SSO-LOGIN-RESULTS.md` (to be created in the test round).
