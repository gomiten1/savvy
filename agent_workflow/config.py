"""Detector configuration.  T1 calibrates the three gate values below."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path

SCAN_DIMENSIONS = ("merchant", "provider", "method", "country", "issuing_bank")
SCAN_GROUPINGS = tuple((dimension,) for dimension in SCAN_DIMENSIONS) + tuple(
    pair for pair in combinations(SCAN_DIMENSIONS, 2)
)

# The pipeline gold layer serves only minute/hour/day grain (D71).  Match its minute
# grain 1:1 so the adapter never re-aggregates in Python; same 25-minute window.
BUCKET_SECONDS = 60
WINDOW_BUCKETS = 25
MIN_HISTORY_OBSERVATIONS = 2

# Calibrated by analysis.t1 replaying the pipeline's clean 14-day gold history
# (20,076 scans, ~5M cell-window evaluations, zero signals).  See docs/T1-RESULTS.md.
# First zero on the (drop, z) frontier: at drop >= 15pp the worst clean z is 16.84.
Z_THRESHOLD = 17.0
MIN_ABS_DROP_PP = 0.15
# Non-binding at pipeline volume (~2000 txn/min); the z/pp gates above already reach
# zero.  Retained as the low-volume floor and flagged for revisit in docs/T1-RESULTS.md.
MIN_ATTEMPTS = 50


@dataclass(frozen=True)
class Gates:
    """Runtime detector gates, with the checked-in values as a safe fallback."""

    min_abs_drop_pp: float = MIN_ABS_DROP_PP
    z_threshold: float = Z_THRESHOLD
    min_attempts: int = MIN_ATTEMPTS


def load_gates(data_dir: str | Path = "data") -> Gates:
    """Read the current gates artifact; a fresh checkout intentionally uses defaults."""
    directory = Path(data_dir)
    pointer = directory / "gates_current"
    if not pointer.exists():
        return Gates()
    try:
        filename = pointer.read_text().strip()
        payload = json.loads((directory / filename).read_text())
        return Gates(float(payload["min_abs_drop_pp"]), float(payload["z_threshold"]), int(payload["min_attempts"]))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid gates artifact in {directory}") from error


def write_gates_version(gates: Gates, data_dir: str | Path = "data") -> Path:
    """Write an immutable gates artifact and atomically advance its pointer."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    versions = [int(path.stem.split("v")[-1]) for path in directory.glob("gates_v*.json")
                if path.stem.split("v")[-1].isdigit()]
    target = directory / f"gates_v{max(versions, default=0) + 1}.json"
    target.write_text(json.dumps(asdict(gates), sort_keys=True))
    pointer_tmp = directory / "gates_current.tmp"
    pointer_tmp.write_text(target.name)
    os.replace(pointer_tmp, directory / "gates_current")
    return target

# Clustering shape.  `lost_approvals = attempts x drop` is conserved going up the
# lattice, so a diluted parent cell reports the same loss as the child that caused
# it and wins the anchor on a bare sort.  These two shares decide when the parent
# is standing in for one child (re-anchor) and when it is hiding two (split).
CLUSTER_CONTAINMENT_SHARE = 0.80
CLUSTER_SPLIT_SHARE = 0.30

# Deterministic incident lifecycle.  These are intentionally independent of the
# calibrated detector gates above.
DEBOUNCE_TICKS = 2
RESOLVE_MISS_TICKS = 3
MAX_CONCURRENT_ALERTS = 3
WINDOW_SECONDS = BUCKET_SECONDS * WINDOW_BUCKETS
RETRY_RECOVERY_RATE = 0.0

# Diagnosis is deliberately downstream of detection.  These bounds protect the worker
# from an investigation that keeps exploring while alerts continue to arrive.
AGENT_BUDGET_SECONDS = 120
AGENT_MAX_TOOL_CALLS = 15
# The playbook is a guide, not a procedure (D4), so diagnosis quality tracks judgement.
# Mini tier keeps the spend flat; effort is pinned low because AGENT_MAX_TOOL_CALLS
# sequential turns have to finish inside AGENT_BUDGET_SECONDS (D80).
AGENT_MODEL = "gpt-5.4-mini"
AGENT_REASONING_EFFORT = "low"
MAX_AGENT_RUNS_PER_INCIDENT = 3
AGENT_RERUN_COOLDOWN_MINUTES = 5
