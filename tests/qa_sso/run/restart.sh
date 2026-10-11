#!/usr/bin/env bash
# Restart ONE llm-wiki QA instance: stop it by its recorded PID (marker-checked), start the same
# run/state/wiki-NAME/start.sh again, record the new PID, wait for /healthz. Usage: restart.sh NAME
# (NAME = static|static_reads|dotted|both|sso|sso_reads|app2|stubbed). Never kills by name or port.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
name="${1:?usage: restart.sh NAME}"
dir="$STATE/wiki-$name"
[[ -x "$dir/start.sh" && -f "$dir/port" ]] || { echo "unknown instance $name (no $dir/start.sh)" >&2; exit 1; }
port="$(cat "$dir/port")"
[[ "$port" == 8000 ]] && { echo "refusing port 8000" >&2; exit 1; }
stop_one "wiki-$name"
for _ in $(seq 1 20); do port_busy "$port" || break; sleep 0.5; done
port_busy "$port" && { echo "port $port still busy after stopping wiki-$name - not touching it" >&2; exit 1; }
start_bg "wiki-$name" uvicorn "$dir" "$LOGS/wiki-$name.log" -- bash "$dir/start.sh"
wait_http "http://127.0.0.1:$port/healthz" 90
