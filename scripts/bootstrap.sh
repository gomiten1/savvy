#!/usr/bin/env bash
# Prepare a clean checkout for the local demo.  Run from the repository root.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# Ordering is intentional: generating history removes live.sqlite, so calibrate
# only after generation and before either live service starts.
.venv/bin/python -m pipeline.generator.generate_historical_aggregates --seed 42
.venv/bin/python -m agent_workflow.analysis.t1 --store gold

echo "Bootstrap complete. Start the generator, then the detector (see docs/DEPLOY.md)."
