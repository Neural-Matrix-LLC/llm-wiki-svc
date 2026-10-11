# tests/qa_sso: black-box QA for llm-wiki-svc SSO login and `application.app1`

Test plan: [`docs/qa/SSO-LOGIN-TEST-PLAN.md`](../../docs/qa/SSO-LOGIN-TEST-PLAN.md). Design:
[`docs/sso-integration-design.md`](../../docs/sso-integration-design.md) v1.0.

The suite only talks HTTP to running servers, plus the SSO's `manage.py` for seeding and grants. It never imports
`llmwiki` (the repo's root `tests/conftest.py` does, so pytest has to run in an env where llmwiki is installed;
`run/pytest.sh` takes care of that). **It is opt-in.** Unless `QA_SSO=1` is set, a plain `pytest` of the repo
collects nothing from this directory. Stale `SSO_*` variables left in a shell don't enable it either, and
any URL on port 8000 aborts the run.

## Ports (dedicated; never 8000)

| port | process | config |
|---|---|---|
| 8030 | SSO (sso-server checkout, gunicorn, SQLite under `run/state-pyX.Y/`) | branch `feat/app-permissions` |
| 8031 | llm-wiki `both` | primary `LLM_WIKI_BASE_URL` |
| 8032 | llm-wiki `sso` with `SSO_BASE_URL` set to the stub | failure-mode cases |
| 8033 | `stub_sso.py` | proxies to 8030 or mints/fails on request |
| 8034 | llm-wiki `sso` | login flows, cookies, CSRF, redirects |
| 8035 | llm-wiki with `AUTH_MODE` unset (`static` is the default) | regression |
| 8036 | llm-wiki `sso` + `SSO_PROTECT_READS=true` | matrix |
| 8037 | llm-wiki `sso` + `SSO_REQUIRED_PERMISSION=application.qa_app2` | JWTV-17 |
| 8038 | nothing listens here | SSO unreachable |
| 8039 | throw-away processes from `test_qasso_startup.py` | fail-fast cases |
| 8040 | 2nd SSO process, same DB/keys/issuer, opt-in `SSO_SUPERUSER_ALL_APPS=true` (the primary SSO runs the default, false) | PERM-18..20, APPCMD-05b, SU-04/05 |
| 8041 | llm-wiki `static` + `SSO_PROTECT_READS=true` | REG-13/14 (v1.1 item 5) |
| 8042 | llm-wiki `both` with a JWT-shaped `INGEST_API_TOKEN` | FB-* (v1.1 item 6, D-09) |

The `stubbed` instance (8032) runs with `--workers 2`, so the refresh-race case SESS-08 exercises the
cross-process guard (v1.1 item 2: the SQLite transaction). Every llm-wiki instance gets its own
`SESSION_DB_PATH=run/state-pyX.Y/wiki-NAME/sessions.sqlite3` and `SSO_CLIENT_ID/SECRET` of the seeded ServiceClient
`qa-wiki-login`; `SESSION_SECRET` is no longer set (v1.1 item 9). The default umask is kept on purpose: the
session store's file mode is itself a test (SESS-04).

Restarting one instance (SESS-19/20) goes through `run/restart.sh NAME`: stop by the recorded PID (marker-checked),
re-run the recorded `run/state-pyX.Y/wiki-NAME/start.sh`, record the new PID, wait for `/healthz`.

You can override any port with `PORT_*` (see `run/common.sh`). `start_all.sh` refuses to start if a port is
busy. It never kills whatever already holds the port.

## Run

```bash
cd /workspace/projects/llm-wiki-svc
# sso-server must be on feat/app-permissions with the developer's work (application app + perms claim)
git -C /workspace/projects/sso-server branch --show-current

tests/qa_sso/run/start_all.sh            # SSO 8030 + stub 8033 + six llm-wiki instances; PIDs -> run/pids/
tests/qa_sso/run/pytest.sh -m "not slow"  # quick pass (about 2-3 min)
tests/qa_sso/run/pytest.sh                # everything, including slow refresh/expiry cases (+ ~6 min)
tests/qa_sso/run/stop_all.sh             # stops exactly the recorded PIDs

# throttle phase (SSO at spec rates: 10/min per IP, 5/min per username), run separately
QA_PHASE=throttle tests/qa_sso/run/start_all.sh --throttle && QA_PHASE=throttle tests/qa_sso/run/pytest.sh
tests/qa_sso/run/stop_all.sh

# Python version: QA_PY=3.11 (default) or QA_PY=3.13 selects the interpreter of EVERY process (SSO gunicorn,
# stub, llm-wiki uvicorn, pytest). Venvs: run/venvs/{sso,wiki}-pyX.Y (built on first use; the SSO venv from
# sso-server requirements.lock minus mysqlclient). State run/state-pyX.Y is wiped at each start (throw-away DBs);
# logs in run/logs/pyX.Y[-throttle]/; start_all.sh records each server's /proc/PID/exe in interpreters.txt.
QA_PY=3.13 tests/qa_sso/run/start_all.sh && QA_PY=3.13 tests/qa_sso/run/pytest.sh \
  --junitxml=tests/qa_sso/run/logs/junit/py3.13-functional.xml
# summarise a junit file per area:  python tests/qa_sso/tools/junit_summary.py <xml> --groups
```

- `start_all.sh` creates `run/state-pyX.Y/` (a 3072-bit QA key pair from `scripts/gen_keys.sh`, the SSO SQLite DB
  `sso-qa_sso-test.sqlite3`, a file cache, a random static token, and per-instance data dirs and session
  secrets), `run/logs/` and `run/pids/`, plus `run/qa.env` with every variable listed below. All of these
  are git-ignored (`tests/qa_sso/.gitignore`).
- llm-wiki runs from a QA venv at `run/venvs/wiki-pyX.Y` (`uv venv` + `uv pip install -e .`). It never
  touches `uv.lock` or the repo's `.venv`. Set `WIKI_PYTHON_VERSION=3.13` for the Python 3.13 pass (design B6).
- Each llm-wiki instance runs with `env -i` from an empty directory, so the repo's real `.env` is never
  loaded. Backends are offline (`local`/`memory`/`fake`), so there is no LLM cost. Logs are at `LOG_LEVEL=DEBUG`
  so the no-secrets-in-logs checks have something to look at.
- `stop_all.sh` kills a PID only if its command line still contains the marker recorded at start
  (`gunicorn`, `uvicorn`, `stub_sso.py`), so a reused PID is never hit.
- SSO access TTL is 2 min by default (`QA_SSO_ACCESS_TTL_MIN`), so the real-SSO refresh cases finish in
  minutes. Fast refresh cases use the stub in `mint` mode instead.

### Collect only (no servers)

```bash
QA_SSO=1 <venv>/bin/python -m pytest tests/qa_sso --collect-only -q -p no:cacheprovider \
    --confcutdir=tests/qa_sso -o addopts=""
```

`--confcutdir` skips the repo's root conftest (handy with a venv that has no llmwiki in it).

## Seeding and the test-DB guard

`fixtures/sso_ops.py` runs inside the SSO's `manage.py shell`. Every operation (seed, grant, revoke,
group changes, activate/deactivate) execs it first. It applies the **same R27 guard as
sso-server `qa/tests/fixtures/seed_test_data.py`**: it requires `SSO_ALLOW_SYNTHETIC_SEED=1` **and** a
local/throw-away DB (SQLite under the temp dir, under a `qa`/`qa_sso` directory, or with `test`/`qa` in its
name; MySQL only on localhost/socket/`db`/`mysql`/`SSO_SEED_LOCAL_HOSTS`). It only touches `qa_*` users
and groups and the service client `qa-wiki-introspect`. If `application.qa_app2` is missing, it creates
it with the design's `create_app_permission` command. Users, groups and grants are listed in `qasso_data.py`.

## Environment (written to `run/qa.env` by start_all.sh)

| variable | meaning |
|---|---|
| `QA_SSO=1` | enables the suite (pytest.sh sets it) |
| `SSO_BASE_URL`, `SSO_ISSUER`, `SSO_AUDIENCE` | the QA SSO |
| `SSO_MANAGE_CMD`, `SSO_REPO_DIR` | `run/state-pyX.Y/sso-manage.sh` (manage.py with the QA env) |
| `SSO_TEST_PRIVATE_KEY_PATH` | QA signing key, used to forge tokens with valid signatures |
| `SSO_ACCESS_LOG` | SSO access log (counts JWKS fetches and `/logout` calls) |
| `SSO_ACCESS_TTL_SECONDS` | access TTL of the QA SSO |
| `LLM_WIKI_BASE_URL`, `LLM_WIKI_AUTH_MODE`, `LLM_WIKI_PROTECT_READS` | primary instance and its config |
| `LLM_WIKI_STATIC_URL`, `_SSO_URL`, `_BOTH_URL`, `_SSO_READS_URL`, `_BOTH_READS_URL` | matrix instances (unset ones are skipped) |
| `LLM_WIKI_APP2_URL` | instance with `SSO_REQUIRED_PERMISSION=application.qa_app2` |
| `LLM_WIKI_STUBBED_URL`, `STUB_SSO_URL` | stubbed instance + stub control |
| `LLM_WIKI_STATIC_TOKEN` | `INGEST_API_TOKEN` of all instances |
| `LLM_WIKI_STATIC_READS_URL` | static + `SSO_PROTECT_READS=true` (v1.1 item 5) |
| `LLM_WIKI_DOTTED_URL`, `LLM_WIKI_DOTTED_TOKEN` | `both` instance with a JWT-shaped static token (FB-*) |
| `LLM_WIKI_SESSION_DBS` | `name=path,...` session DB per instance (grey-box SESS-*, LOGOUT-*) |
| `LLM_WIKI_SESSION_MAX_AGE_HOURS` | `SESSION_MAX_AGE_HOURS` of the instances (168) |
| `LLM_WIKI_SSO_CLIENT_ID`, `_SECRET` | the ServiceClient llm-wiki uses for /login and /refresh delegation |
| `QA_SSO_RESTART_CMD` | `run/restart.sh {name}` (by PID) |
| `SSO_ALLAPPS_URL` | 2nd SSO with the opt-in `SSO_SUPERUSER_ALL_APPS=true` (default false) |
| `SSO_SERVER_LOG` | SSO stderr log (JSON login events with `ip`, THR-15) |
| `LLM_WIKI_COOKIE_SECURE` | expected `Secure` flag (instances run with the default `true`) |
| `LLM_WIKI_LOG_FILES` | llm-wiki logs scanned by `test_qasso_zy_logs.py` |
| `LLM_WIKI_START_CMD` | command template (`{port}`) for the fail-fast startup cases |
| `QA_SSO_THROTTLE_PHASE=1` | throttle phase only |

## Files

| file | purpose |
|---|---|
| `conftest.py` | config from env, opt-in and port-8000 guard, fixtures (`tokens`, `forge`, `sso_admin`, `logged_in`, `stub`) |
| `qasso_data.py` | synthetic users, groups and grants, plus `expected_perms()` (design A2 rule) |
| `fixtures/sso_ops.py` | guarded SSO-side operations (run in the SSO's Django shell) |
| `sso_admin.py` | runs those operations through `SSO_MANAGE_CMD` |
| `jwt_forge.py` | verbatim copy of sso-server `qa/tests/jwt_forge.py` |
| `wiki_client.py` | httpx "browser" (manual cookie jar, optional loopback source address 127.0.0.x, form + csrf-meta parsing, `logout()`, `write()` with X-CSRF-Token), browser-like URL resolution |
| `session_store.py` | grey-box reader of an instance's SQLite session store; can age `created_at`/`last_seen` (QA DBs under `run/state-pyX.Y/` only) |
| `creds.py`, `matrix.py` | credentials and expected outcomes for the AUTH_MODE matrix |
| `stub_sso.py` | controllable SSO stand-in (timeouts, 5xx, garbage, forged or minted tokens, refresh failures) |
| `snapshots/static_routes_bca2bef.json`, `tools/snapshot_routes.py` | AST snapshot of today's routes and auth |
| `run/` | `common.sh`, `start_all.sh`, `stop_all.sh`, `restart.sh`, `pytest.sh` |

## Markers

`needs_wiki`, `needs_sso`, `needs_manage`, `needs_stub`, `needs_key`, `slow`, `throttle`, `static_check`,
`grey` (reads/ages the QA instance's own session DB), `restart` (restarts the `sso` instance by PID; run these
last or alone if other runs share the stack: `-m restart` / `-m "not restart"`).
Throttle cases bind distinct loopback source addresses (127.0.0.21-62), so no 61 s waits between them; Linux
routes all of 127.0.0.0/8 to `lo`, nothing to configure.
A test whose prerequisite is missing skips and says which variable is absent. `pytest.mark.skip` is used
only where the design is genuinely undecided, and the reason names the open question (Q-nn in the plan).
