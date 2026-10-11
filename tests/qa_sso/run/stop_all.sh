#!/usr/bin/env bash
# Stop every process start_all.sh recorded - by PID only, after checking the PID still belongs to the
# process we started (marker in its command line). Never pkill by name, never kill by port.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
shopt -s nullglob
for pidfile in "$PIDS"/*.pid; do
  stop_one "$(basename "$pidfile" .pid)"
done
