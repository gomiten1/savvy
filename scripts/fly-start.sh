#!/usr/bin/env bash
set -euo pipefail

cd /app
# Application code uses ./data. On Fly, make it the persistent volume.
if [[ ! -L data ]]; then
  ln -s /data data
fi

# A fresh volume needs the historical artifact before the live writer starts.
# Subsequent restarts preserve it and return to the demo immediately.
if [[ ! -f /data/gold/historical.duckdb || ! -f /data/baselines_current ]]; then
  python -m pipeline.generator.generate_historical_aggregates --seed 42
  python -m agent_workflow.analysis.t1 --store gold
fi

python scripts/health_server.py &
health_pid=$!
python -m pipeline.generator.generate_live_stream --duration 0 &
generator_pid=$!
python -m agent_workflow.main --live &
detector_pid=$!
trap 'kill "$health_pid" "$generator_pid" "$detector_pid" 2>/dev/null || true; wait "$health_pid" "$generator_pid" "$detector_pid" 2>/dev/null || true' EXIT INT TERM

set +e
wait -n "$health_pid" "$generator_pid" "$detector_pid"
status=$?
set -e
exit "$status"
