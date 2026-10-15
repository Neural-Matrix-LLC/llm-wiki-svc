#!/usr/bin/env bash
# Start the QA stack for tests/qa_sso on dedicated ports, recording every PID in run/pids/.
#   tests/qa_sso/run/start_all.sh             functional phase (SSO throttles raised)
#   tests/qa_sso/run/start_all.sh --throttle  throttle phase (SSO at spec rates 10/min IP, 5/min user)
# Stop with tests/qa_sso/run/stop_all.sh (by PID only). Writes run/qa.env for pytest.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
THROTTLE=0; [[ "${1:-}" == "--throttle" ]] && THROTTLE=1
#   tests/qa_sso/run/start_all.sh --enforce   v1.2b binding-ON phase: both SSOs SSO_IP_BINDING_MODE=ENFORCE and
#     SSO_TRUSTED_PROXIES=127.0.0.1; wiki-sso also trusts 127.0.0.1 with CLIENT_IP_HEADER=x-real-ip (use QA_PHASE=enforce)
ENFORCE=0; [[ "${1:-}" == "--enforce" ]] && ENFORCE=1
mkdir -p "$PIDS"
if compgen -G "$PIDS/*.pid" > /dev/null; then
  echo "PID files exist in $PIDS - run stop_all.sh first" >&2; exit 1
fi
# throw-away DBs: every run starts from an empty state dir (only ever this QA-owned path under run/)
case "$STATE" in "$RUN_DIR"/state-py*) rm -rf "$STATE" ;; *) echo "unexpected STATE=$STATE" >&2; exit 1 ;; esac
mkdir -p "$STATE" "$LOGS"; : > "$LOGS/.run-start"
for p in $(all_ports); do
  [[ "$p" == 8000 ]] && { echo "refusing to use port 8000" >&2; exit 1; }
  if port_busy "$p"; then echo "port $p is busy - not touching whoever owns it; pick other PORT_* values" >&2; exit 1; fi
done
if [[ ! -x "$SSO_VENV/bin/gunicorn" ]]; then
  uv venv -q --python "$QA_PY" "$SSO_VENV"
  grep -v '^mysqlclient' "$SSO_REPO/requirements.lock" > "$RUN_DIR/venvs/sso-requirements-nomysql.txt"
  VIRTUAL_ENV="$SSO_VENV" uv pip install -q -r "$RUN_DIR/venvs/sso-requirements-nomysql.txt"
fi
git -C "$SSO_REPO" rev-parse --abbrev-ref HEAD | tee "$STATE/sso_branch"

# ---- keys (3072-bit, the gen_keys.sh default: realistic token/cookie sizes) -------------------------------
if [[ ! -f "$STATE/keys/qa-wiki.private.pem" ]]; then
  bash "$SSO_REPO/scripts/gen_keys.sh" "$STATE/keys" qa-wiki 3072
fi
[[ -f "$STATIC_TOKEN_FILE" ]] || python3 -c 'import secrets;print("qa-static-"+secrets.token_urlsafe(32))' > "$STATIC_TOKEN_FILE"
STATIC_TOKEN="$(cat "$STATIC_TOKEN_FILE")"
# v1.1 item 6 (D-09): a static token that PARSES as a JWT (JSON header, 3 b64url parts) but is not a valid SSO
# token, so "verify as JWT, then fall back to the constant-time static compare" is exercised.
[[ -f "$STATE/dotted_token" ]] || python3 - > "$STATE/dotted_token" <<'PY'
import base64, json, secrets
b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
print(b({"alg": "RS256", "typ": "JWT", "kid": "qa-static"}) + "." + b({"qa": "static-token"}) + "." + secrets.token_urlsafe(32))
PY
DOTTED_TOKEN="$(cat "$STATE/dotted_token")"
LOGIN_CLIENT_ID="$(python3 -c "import sys;sys.path.insert(0,'$QA_DIR');import qasso_data as D;print(D.LOGIN_CLIENT_ID)")"
LOGIN_CLIENT_SECRET="$(python3 -c "import sys;sys.path.insert(0,'$QA_DIR');import qasso_data as D;print(D.LOGIN_CLIENT_SECRET)")"

# ---- SSO (sso-server checkout, its own venv; DB, cache, keys and logs live under tests/qa_sso/run/state) ----
if [[ $THROTTLE == 1 ]]; then RATE_LOGIN=10/min; RATE_USER=5/min; RATE_REFRESH=60/min
else RATE_LOGIN=100000/min; RATE_USER=100000/min; RATE_REFRESH=100000/min; fi
cat > "$STATE/sso.env" <<ENV
export SSO_ENV_FILE=''
export SSO_SECRET_KEY='$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')'
export SSO_DEBUG='False'
export SSO_ALLOWED_HOSTS='127.0.0.1,localhost'
export SSO_ISSUER='http://127.0.0.1:$PORT_SSO'
export SSO_AUDIENCE='myfin-services'
export SSO_DB_ENGINE='sqlite'
export SSO_SQLITE_PATH='$STATE/sso-qa_sso-test.sqlite3'
export SSO_JWT_PRIVATE_KEY_PATH='$STATE/keys/qa-wiki.private.pem'
export SSO_JWT_PUBLIC_KEY_PATHS='$STATE/keys/qa-wiki.public.pem'
export SSO_CORS_ALLOWED_ORIGINS='https://example.com'
export SSO_SSL_REDIRECT='False'
export SSO_HSTS_SECONDS='0'
export SSO_NUM_PROXIES='0'
export SSO_CACHE_URL='filecache://$STATE/sso-cache'
export SSO_ACCESS_TTL_MIN='$QA_SSO_ACCESS_TTL_MIN'
export SSO_THROTTLE_LOGIN='$RATE_LOGIN'
export SSO_THROTTLE_LOGIN_USER='$RATE_USER'
export SSO_THROTTLE_REFRESH='$RATE_REFRESH'
export PYTHONDONTWRITEBYTECODE='1'
ENV
if [[ $ENFORCE == 1 ]]; then
  printf "export SSO_IP_BINDING_MODE='ENFORCE'\nexport SSO_TRUSTED_PROXIES='127.0.0.1'\n" >> "$STATE/sso.env"
  WIKI_SSO_EXTRA=(TRUSTED_PROXY_IPS=127.0.0.1 CLIENT_IP_HEADER=x-real-ip)
else WIKI_SSO_EXTRA=(); fi
cat > "$STATE/sso-manage.sh" <<SH
#!/usr/bin/env bash
# manage.py of the QA SSO (used as SSO_MANAGE_CMD). Runs in the sso-server checkout with the QA env.
source "$STATE/sso.env"
cd "$SSO_REPO" && exec "$SSO_VENV/bin/python" manage.py "\$@"
SH
chmod +x "$STATE/sso-manage.sh"
"$STATE/sso-manage.sh" migrate --noinput > "$LOGS/sso-migrate.log" 2>&1 || { tail -30 "$LOGS/sso-migrate.log"; exit 1; }
SSO_ALLOW_SYNTHETIC_SEED=1 QA_SSO_TESTS_DIR="$QA_DIR" "$STATE/sso-manage.sh" shell -c \
  "exec(open('$QA_DIR/fixtures/sso_ops.py').read()); seed()" | tee "$LOGS/sso-seed.log"
( source "$STATE/sso.env"
  start_bg sso gunicorn "$SSO_REPO" "$LOGS/sso.log" -- "$SSO_VENV/bin/gunicorn" ssoproject.wsgi \
    --bind "127.0.0.1:$PORT_SSO" --workers 2 --access-logfile "$LOGS/sso-access.log" )
wait_http "http://127.0.0.1:$PORT_SSO/healthz" 60
# 2nd SSO process on the same DB/keys/issuer with the opt-in SSO_SUPERUSER_ALL_APPS=true (the primary SSO runs
# with the code default, false since e9b0b36 / decision 2026-10-11)
( source "$STATE/sso.env"; export SSO_SUPERUSER_ALL_APPS=true
  start_bg sso-allapps gunicorn "$SSO_REPO" "$LOGS/sso-allapps.log" -- "$SSO_VENV/bin/gunicorn" ssoproject.wsgi \
    --bind "127.0.0.1:$PORT_SSO_ALLAPPS" --workers 1 --access-logfile "$LOGS/sso-allapps-access.log" )
wait_http "http://127.0.0.1:$PORT_SSO_ALLAPPS/healthz" 60

# ---- llm-wiki venv (outside the repo's own .venv; never touches uv.lock) ----------------------------------
# sso-client (v1.2b) comes from the local sso-server checkout (SSO_CLIENT_SRC), not from the git tag in pyproject.toml
SSO_CLIENT_SRC="${SSO_CLIENT_SRC:-$SSO_REPO/clients/python}"
echo "sso-client @ file://$SSO_CLIENT_SRC" > "$RUN_DIR/venvs/sso-client-override.txt"
if [[ ! -x "$WIKI_VENV/bin/python" ]]; then
  uv venv -q --python "$QA_PY" "$WIKI_VENV"
  VIRTUAL_ENV="$WIKI_VENV" uv pip install --override "$RUN_DIR/venvs/sso-client-override.txt" \
    -e "$WIKI_REPO" pytest pytest-asyncio httpx "pyjwt[crypto]" itsdangerous
fi
# always refresh from the checkout (the SSO under test and the library must be the same commit) + llm-wiki deps
VIRTUAL_ENV="$WIKI_VENV" uv pip install -q --reinstall-package sso-client \
  --override "$RUN_DIR/venvs/sso-client-override.txt" "$SSO_CLIENT_SRC" -e "$WIKI_REPO"
"$WIKI_VENV/bin/python" -c "import sso_client,sys;print('sso-client',sso_client.__version__ if hasattr(sso_client,'__version__') else '?',sso_client.__file__)" \
  | tee "$STATE/sso_client_version"
"$WIKI_VENV/bin/python" --version | tee "$STATE/wiki_python"

# ---- stub SSO ---------------------------------------------------------------------------------------------
start_bg stub stub_sso.py "$QA_DIR" "$LOGS/stub.log" -- "$WIKI_VENV/bin/python" "$QA_DIR/stub_sso.py" \
  --port "$PORT_STUB" --upstream "http://127.0.0.1:$PORT_SSO" --issuer "http://127.0.0.1:$PORT_SSO" \
  --signing-key "$STATE/keys/qa-wiki.private.pem"
wait_http "http://127.0.0.1:$PORT_STUB/__qa/calls" 30

# ---- llm-wiki instances: cwd = an empty dir (no repo .env), offline backends, DEBUG logs -------------------
# Each instance gets run/state/wiki-NAME/start.sh (the exact command, exec'd so the PID stays the uvicorn PID)
# and run/state/wiki-NAME/port; run/restart.sh NAME stops it by PID and runs start.sh again (session-survival
# and prune-on-start cases). SESSION_DB_PATH is explicit per instance (v1.1 item 2). The default umask is kept
# on purpose: the session store's file mode is a test (SESS-04), not something the harness should fix.
start_wiki() {  # start_wiki NAME PORT [UVICORN_EXTRA_ARGS_VAR] VAR=VAL...
  local name="$1" port="$2"; shift 2
  local dir="$STATE/wiki-$name"; mkdir -p "$dir"
  local extra=""; if [[ "${1:-}" == --workers=* ]]; then extra="--workers ${1#--workers=}"; shift; fi
  {
    echo '#!/usr/bin/env bash'
    printf 'exec env -i PATH=%q HOME=%q ' "$PATH" "$HOME"
    printf '%q ' STORAGE_BACKEND=local LOCAL_STORAGE_PATH="$dir/data" VECTOR_BACKEND=memory EMBEDDING_BACKEND=fake \
      EMBEDDING_DIM=64 LLM_BACKEND=fake LEXICAL_BACKEND=memory RERANKER_BACKEND=none WEB_SEARCH_BACKEND=none \
      LOG_LEVEL=DEBUG INGEST_API_TOKEN="$STATIC_TOKEN" PYTHONDONTWRITEBYTECODE=1 \
      SSO_BASE_URL="http://127.0.0.1:$PORT_SSO" SSO_ISSUER="http://127.0.0.1:$PORT_SSO" \
      SSO_CLIENT_ID="$LOGIN_CLIENT_ID" SSO_CLIENT_SECRET="$LOGIN_CLIENT_SECRET" \
      SESSION_DB_PATH="$dir/sessions.sqlite3" "$@" \
      "$WIKI_VENV/bin/python" -m uvicorn llmwiki.api.app:app --host 127.0.0.1 --port "$port" $extra
    echo
  } > "$dir/start.sh"
  chmod +x "$dir/start.sh"; echo "$port" > "$dir/port"
  start_bg "wiki-$name" uvicorn "$dir" "$LOGS/wiki-$name.log" -- bash "$dir/start.sh"
}
start_wiki static     "$PORT_WIKI_STATIC"
start_wiki static_reads "$PORT_WIKI_STATIC_READS" SSO_PROTECT_READS=true
start_wiki dotted     "$PORT_WIKI_DOTTED"    AUTH_MODE=both INGEST_API_TOKEN="$DOTTED_TOKEN"
start_wiki both       "$PORT_WIKI_BOTH"      AUTH_MODE=both
start_wiki sso        "$PORT_WIKI_SSO"       AUTH_MODE=sso "${WIKI_SSO_EXTRA[@]}"
start_wiki sso_reads  "$PORT_WIKI_SSO_READS" AUTH_MODE=sso SSO_PROTECT_READS=true
start_wiki app2       "$PORT_WIKI_APP2"      AUTH_MODE=sso SSO_REQUIRED_PERMISSION=application.qa_app2
# 2 workers: the refresh-race case (SESS-08) must hold across processes (v1.1 item 2: the DB transaction is the guard)
start_wiki stubbed    "$PORT_WIKI_STUBBED"   --workers=2 AUTH_MODE=sso SSO_BASE_URL="http://127.0.0.1:$PORT_STUB"
for p in "$PORT_WIKI_STATIC" "$PORT_WIKI_STATIC_READS" "$PORT_WIKI_DOTTED" "$PORT_WIKI_BOTH" "$PORT_WIKI_SSO" \
         "$PORT_WIKI_SSO_READS" "$PORT_WIKI_APP2" "$PORT_WIKI_STUBBED"; do
  wait_http "http://127.0.0.1:$p/healthz" 90
done

# ---- env for pytest -------------------------------------------------------------------------------------
cat > "$RUN_DIR/qa.env" <<ENV
export SSO_BASE_URL='http://127.0.0.1:$PORT_SSO'
export SSO_ISSUER='http://127.0.0.1:$PORT_SSO'
export SSO_MANAGE_CMD='$STATE/sso-manage.sh'
export SSO_REPO_DIR='$SSO_REPO'
export SSO_TEST_PRIVATE_KEY_PATH='$STATE/keys/qa-wiki.private.pem'
export SSO_ACCESS_LOG='$LOGS/sso-access.log'
export SSO_ACCESS_TTL_SECONDS='$((QA_SSO_ACCESS_TTL_MIN * 60))'
export LLM_WIKI_BASE_URL='http://127.0.0.1:$PORT_WIKI_BOTH'
export LLM_WIKI_AUTH_MODE='both'
export LLM_WIKI_STATIC_URL='http://127.0.0.1:$PORT_WIKI_STATIC'
export LLM_WIKI_SSO_URL='http://127.0.0.1:$PORT_WIKI_SSO'
export LLM_WIKI_SSO_READS_URL='http://127.0.0.1:$PORT_WIKI_SSO_READS'
export LLM_WIKI_APP2_URL='http://127.0.0.1:$PORT_WIKI_APP2'
export LLM_WIKI_STUBBED_URL='http://127.0.0.1:$PORT_WIKI_STUBBED'
export STUB_SSO_URL='http://127.0.0.1:$PORT_STUB'
export LLM_WIKI_STATIC_TOKEN='$STATIC_TOKEN'
export LLM_WIKI_STATIC_READS_URL='http://127.0.0.1:$PORT_WIKI_STATIC_READS'
export LLM_WIKI_DOTTED_URL='http://127.0.0.1:$PORT_WIKI_DOTTED'
export LLM_WIKI_DOTTED_TOKEN='$DOTTED_TOKEN'
export LLM_WIKI_SESSION_DBS='$(for n in both sso sso_reads app2 stubbed dotted; do printf '%s=%s,' "$n" "$STATE/wiki-$n/sessions.sqlite3"; done)'
export LLM_WIKI_SESSION_MAX_AGE_HOURS='168'
export LLM_WIKI_SESSION_IDLE_HOURS='12'
export QA_PY='$QA_PY'
export LLM_WIKI_SSO_CLIENT_ID='$LOGIN_CLIENT_ID'
export LLM_WIKI_SSO_CLIENT_SECRET='$LOGIN_CLIENT_SECRET'
export QA_SSO_RESTART_CMD='$RUN_DIR/restart.sh {name}'
export SSO_ALLAPPS_URL='http://127.0.0.1:$PORT_SSO_ALLAPPS'
export SSO_SERVER_LOG='$LOGS/sso.log'
export LLM_WIKI_COOKIE_SECURE='true'
export LLM_WIKI_LOG_FILES='$(ls "$LOGS"/wiki-*.log | paste -sd, -)'
export LLM_WIKI_START_CMD='$WIKI_VENV/bin/python -m uvicorn llmwiki.api.app:app --host 127.0.0.1 --port {port}'
export QA_SSO_STARTUP_PORT='$PORT_STARTUP'
export QA_SSO_THROTTLE_PHASE='$THROTTLE'
export QA_SSO_ENFORCE_PHASE='$ENFORCE'
ENV
# ---- which interpreter does each server process really run? (/proc/PID/exe of the recorded PIDs) --------------
sleep 2
: > "$STATE/interpreters.txt"
for f in "$PIDS"/*.pid; do
  n="$(basename "$f" .pid)"; pid="$(cat "$f")"
  exe="$(readlink "/proc/$pid/exe" 2>/dev/null || echo '?')"
  ver="$("$exe" -c 'import platform,sys;print(platform.python_version(), sys.prefix)' 2>/dev/null || echo '?')"
  cmd0="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-120)"
  echo "$n pid=$pid exe=$exe python=$ver cmdline=$cmd0" | tee -a "$STATE/interpreters.txt"
done
echo "QA stack up (Python $QA_PY). PIDs in $PIDS; env in $RUN_DIR/qa.env"
