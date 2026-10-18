#!/usr/bin/env bash
# Stops everything: the Python apps of THIS project, then the Docker containers.
# Stored data is kept (the Postgres volume is not removed).
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

# PIDs whose command matches $1 and whose working directory is inside this project,
# so we never touch an unrelated producer.py or streamlit from another project.
project_pids() {
  local pid cwd
  for pid in $(pgrep -f "$1" || true); do
    [ "$pid" = "$$" ] && continue
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    case "$cwd" in "$ROOT"|"$ROOT"/*) echo "$pid" ;; esac
  done
}

# stop <label> <pattern>: SIGTERM, then wait up to 15s for a clean exit
stop() {
  local label=$1 pids
  pids=$(project_pids "$2")
  if [ -z "$pids" ]; then echo "  $label: not running"; return; fi
  echo "  $label: stopping $(echo $pids)"
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 30); do
    pids=$(project_pids "$2")
    [ -z "$pids" ] && return
    sleep 0.5
  done
  echo "  $label: still running after 15s (pid $(echo $pids)); stop it with: kill -9 $(echo $pids)" >&2
  failed=1
}

failed=0
echo "Stopping Python processes..."
stop "make run supervisor" "scripts/run\.sh"   # stops its three children in order
stop "dashboard"           "streamlit run"
stop "producer"            "producer\.py"
stop "consumer"            "consumer\.py"

echo "Stopping Docker containers..."
docker compose down || failed=1

if [ "$failed" = 0 ]; then echo "Everything is stopped."; else echo "Finished with problems (see above)." >&2; exit 1; fi
