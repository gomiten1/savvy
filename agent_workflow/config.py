"""Detector configuration.  T1 calibrates the three gate values below."""

from __future__ import annotations

from itertools import combinations

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
AGENT_MODEL = "gpt-4.1-mini"
MAX_AGENT_RUNS_PER_INCIDENT = 3
AGENT_RERUN_COOLDOWN_MINUTES = 5
