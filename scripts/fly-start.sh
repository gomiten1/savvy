#!/usr/bin/env bash
set -euo pipefail

cd /app
# Start serving before demo data is prepared. Fly needs this endpoint to remain
# available even if a non-essential generator or detector process exits.
python scripts/health_server.py &
health_pid=$!

# Application code uses ./data. On Fly, make it the persistent volume.
if [[ ! -L data ]]; then
  ln -s /data data
fi

(
  # A fresh volume needs the historical artifact before the live writer starts.
  # Subsequent restarts preserve it and return to the demo immediately.
  if [[ ! -f /data/gold/historical.duckdb || ! -f /data/baselines_current ]]; then
    python -m pipeline.generator.generate_historical_aggregates --seed 42
    python -m agent_workflow.analysis.t1 --store gold
  fi

  python -m pipeline.generator.generate_live_stream --duration 0 &
  python -m agent_workflow.main --live &
  wait
) &
workers_pid=$!

trap 'kill "$health_pid" "$workers_pid" 2>/dev/null || true; wait "$health_pid" "$workers_pid" 2>/dev/null || true' EXIT INT TERM
wait "$health_pid"
