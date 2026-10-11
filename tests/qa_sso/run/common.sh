# Shared settings for the llm-wiki SSO-login QA run. Sourced by start_all.sh / stop_all.sh / pytest.sh.
# Dedicated ports only - never 8000 (other agents and the developer use it). Processes are tracked by the
# PID files in run/pids/ and stopped by PID only (never pkill by name, never "kill whatever is on the port").
QA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$QA_DIR/run"
WIKI_REPO="${LLM_WIKI_REPO_DIR:-$(cd "$QA_DIR/../.." && pwd)}"
SSO_REPO="${SSO_REPO_DIR:-/workspace/projects/sso-server}"
# QA_PY selects the interpreter for EVERY process of the run (SSO gunicorn, stub, llm-wiki uvicorn, pytest).
# Each version has its own venvs (run/venvs/, created on first use by start_all.sh, never the repos' .venv) and
# its own throw-away state/logs dirs (run/state-pyX.Y wiped by start_all.sh, run/logs/pyX.Y).
QA_PY="${QA_PY:-${WIKI_PYTHON_VERSION:-3.11}}"
SSO_VENV="${SSO_VENV:-$RUN_DIR/venvs/sso-py$QA_PY}"            # sso-server requirements.lock minus mysqlclient
WIKI_VENV="${WIKI_VENV:-$RUN_DIR/venvs/wiki-py$QA_PY}"         # uv venv + pip -e llm-wiki-svc + test deps
WIKI_PYTHON_VERSION="$QA_PY"
STATE="$RUN_DIR/state-py$QA_PY"; PIDS="$RUN_DIR/pids"
LOGS="$RUN_DIR/logs/py$QA_PY${QA_PHASE:+-$QA_PHASE}"   # QA_PHASE=throttle keeps the throttle-phase logs apart

PORT_SSO="${PORT_SSO:-8030}"
PORT_WIKI_BOTH="${PORT_WIKI_BOTH:-8031}"         # AUTH_MODE=both  (primary LLM_WIKI_BASE_URL)
PORT_WIKI_STUBBED="${PORT_WIKI_STUBBED:-8032}"   # AUTH_MODE=sso, SSO_BASE_URL -> stub
PORT_STUB="${PORT_STUB:-8033}"                   # stub_sso.py
PORT_WIKI_SSO="${PORT_WIKI_SSO:-8034}"           # AUTH_MODE=sso
PORT_WIKI_STATIC="${PORT_WIKI_STATIC:-8035}"     # AUTH_MODE unset (= static default)
PORT_WIKI_SSO_READS="${PORT_WIKI_SSO_READS:-8036}"  # AUTH_MODE=sso, SSO_PROTECT_READS=true
PORT_WIKI_APP2="${PORT_WIKI_APP2:-8037}"         # AUTH_MODE=sso, SSO_REQUIRED_PERMISSION=application.qa_app2
PORT_UNREACHABLE=8038                            # nothing listens here (SSO-down cases)
PORT_STARTUP=8039                                # test_qasso_startup.py throw-away processes
PORT_SSO_NOSUPER="${PORT_SSO_NOSUPER:-8040}"     # 2nd SSO, same DB, SSO_SUPERUSER_ALL_APPS=false (v1.1 item 8)
PORT_WIKI_STATIC_READS="${PORT_WIKI_STATIC_READS:-8041}"  # AUTH_MODE=static, SSO_PROTECT_READS=true (v1.1 item 5)
PORT_WIKI_DOTTED="${PORT_WIKI_DOTTED:-8042}"     # AUTH_MODE=both, JWT-shaped INGEST_API_TOKEN (v1.1 item 6, D-09)

QA_SSO_ACCESS_TTL_MIN="${QA_SSO_ACCESS_TTL_MIN:-2}"   # short, so refresh cases run in minutes
STATIC_TOKEN_FILE="$STATE/static_token"

all_ports() { echo "$PORT_SSO $PORT_WIKI_BOTH $PORT_WIKI_STUBBED $PORT_STUB $PORT_WIKI_SSO $PORT_WIKI_STATIC $PORT_WIKI_SSO_READS $PORT_WIKI_APP2 $PORT_SSO_NOSUPER $PORT_WIKI_STATIC_READS $PORT_WIKI_DOTTED"; }

# stop_one NAME -> stop the process recorded in $PIDS/NAME.pid, by PID, only if its cmdline has the marker
stop_one() {
  local name="$1" pidfile="$PIDS/$1.pid" pid marker
  [[ -f "$pidfile" ]] || { echo "$name: no PID file"; return 0; }
  pid="$(cat "$pidfile")"; marker="$(cat "$PIDS/$name.marker" 2>/dev/null || echo)"
  if [[ -n "$marker" && -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q -- "$marker"; then
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && { echo "$name pid=$pid did not stop, SIGKILL"; kill -KILL "$pid"; }
    echo "stopped $name pid=$pid"
  else
    echo "$name pid=$pid not running (or PID reused by another process) - left alone"
  fi
  rm -f "$pidfile" "$PIDS/$name.marker"
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }   # something accepts connections

# start_bg NAME MARKER CWD LOGFILE -- CMD...   -> runs CMD detached, records its PID in $PIDS/NAME.pid
start_bg() {
  local name="$1" marker="$2" cwd="$3" log="$4"; shift 5
  ( cd "$cwd" && exec setsid "$@" >>"$log" 2>&1 < /dev/null ) &
  local pid=$!
  echo "$pid" > "$PIDS/$name.pid"
  echo "$marker" > "$PIDS/$name.marker"
  echo "started $name pid=$pid ($*)"
}

wait_http() {  # wait_http URL SECONDS
  local url="$1" t="${2:-60}"
  for _ in $(seq 1 "$((t * 2))"); do
    curl -fsS -o /dev/null "$url" 2>/dev/null && return 0
    sleep 0.5
  done
  echo "TIMEOUT waiting for $url" >&2; return 1
}
