#!/usr/bin/env bash
# Starts consumer, producer, evals (every minute) and dashboard in one terminal with prefixed logs.
# Ctrl-C stops all of them. Infrastructure (Docker) is left running; use `make down`.
set -euo pipefail
cd "$(dirname "$0")/.."

# A second producer or consumer would send duplicate messages / split the work
running=$(pgrep -fl "(producer|consumer)\.py" || true)
if [ -n "$running" ]; then
  echo "A producer or consumer is already running. Stop it first (kill <pid>):" >&2
  echo "$running" >&2
  exit 1
fi

if lsof -nP -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Note: port 8501 is already in use, so Streamlit will pick the next free port." >&2
  echo "      The real dashboard URL is printed in the [dashboard] lines below." >&2
fi

pids=()

# start <name> <dir> <command...>: run in the background, prefixing every log line
start() {
  local name=$1 dir=$2
  shift 2
  ( cd "$dir" && exec "$@" ) > >(sed -u "s/^/[$name] /") 2>&1 &
  pids+=($!)
}

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Stopping..."
  # Reverse start order: dashboard, then producer (flushes Kafka), then consumer
  for ((i = ${#pids[@]} - 1; i >= 0; i--)); do
    kill -TERM "${pids[i]}" 2>/dev/null || true
    sleep 1
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM   # Ctrl-C is a normal way to stop: run cleanup via EXIT, exit code 0

start consumer  src "$PWD/.venv/bin/python" consumer.py
start producer  src "$PWD/.venv/bin/python" producer.py
start evals     src "$PWD/.venv/bin/python" evals.py --every 60
start dashboard .   "$PWD/.venv/bin/streamlit" run src/dashboard.py \
  --server.headless true --browser.gatherUsageStats false

echo "Pipeline running. Dashboard: http://localhost:8501  |  Kafka UI: http://localhost:8080"
echo "Press Ctrl-C to stop."

# Exit (and clean up) as soon as any of them dies, instead of running half a pipeline.
# (Polling because macOS ships bash 3.2, which has no `wait -n`.)
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A process exited unexpectedly (see its log above)." >&2
      exit 1
    fi
  done
  sleep 2
done
