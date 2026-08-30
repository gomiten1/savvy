#!/usr/bin/env bash
set -euo pipefail

cd /app

# Local credentials stay out of the image. Fly injects production credentials
# as secrets; this only makes `./scripts/fly-start.sh` usable locally.
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

# Application code uses ./data. On Fly, make it the persistent volume.
if [[ ! -L data ]]; then
  ln -s /data data
fi

status_file=/data/runtime-status.json
export RUNTIME_STATUS_FILE="$status_file"
python -c 'from scripts.runtime_status import update_status; update_status(state="booting", generator_last_write=None, detector_last_scan=None)'

web_pid=""
generator_pid=""
detector_pid=""
cleanup() {
  for pid in "$web_pid" "$generator_pid" "$detector_pid"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python scripts/web_server.py --status-file "$status_file" &
web_pid=$!

# A fresh volume needs the historical artifact and a baseline before the live
# writer starts. Keep these checks separate: a missing baseline must not force
# an expensive regeneration of history that is already persisted on the volume.
if [[ ! -f /data/gold/historical.duckdb ]]; then
  echo "[boot] building history"
  python -m pipeline.generator.generate_historical_aggregates --seed 42
fi
if [[ ! -f /data/baselines_current ]]; then
  echo "[boot] building baselines"
  # T1 also runs a full historical detection replay. That is valuable in CI,
  # but it delays production startup by many minutes and can be interrupted
  # before the baseline pointer is written. Fly only needs the artifact here.
  python -m agent_workflow.analysis.t1 --store gold --build-only
else
  echo "[boot] using existing history and baselines"
fi

# The detector needs to evaluate each simulated minute before the generator
# advances past it. Fly therefore runs at real-time pace; local demos retain
# the generator's 10x default unless they opt in to this variable.
python -m pipeline.generator.generate_live_stream --duration 0 --speed-multiplier "${NEXTWAVE_LIVE_SPEED_MULTIPLIER:-1}" &
generator_pid=$!

live_ready=false
for _ in $(seq 1 30); do
  if python scripts/check_live_db.py /data/gold/live.sqlite; then
    live_ready=true
    break
  fi
  if ! kill -0 "$generator_pid" 2>/dev/null; then
    echo "[boot] generator exited before live database became ready" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$live_ready" != true ]]; then
  echo "[boot] timed out waiting for a readable live database" >&2
  exit 1
fi

python -m agent_workflow.main --live &
detector_pid=$!
python -c 'from scripts.runtime_status import update_status; update_status(state="ready")'

# Any worker exit, even a clean one, makes Fly fail health and restart the Machine.
wait -n "$generator_pid" "$detector_pid" || true
echo "[boot] worker exited; restarting Machine" >&2
exit 1
