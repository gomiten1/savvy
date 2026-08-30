"""Detector configuration.  T1 calibrates the three gate values below."""

from __future__ import annotations

from itertools import combinations

SCAN_DIMENSIONS = ("merchant", "provider", "method", "country", "issuing_bank")
SCAN_GROUPINGS = tuple((dimension,) for dimension in SCAN_DIMENSIONS) + tuple(
    pair for pair in combinations(SCAN_DIMENSIONS, 2)
)

# The existing synthetic generator emits a traffic batch every five minutes.  Keep this
# aligned with T0 until the live generator delivers one-minute gold aggregates.
BUCKET_SECONDS = 300
WINDOW_BUCKETS = 5
MIN_HISTORY_OBSERVATIONS = 2

# Calibrated against the clean synthetic history by analysis.t1.
Z_THRESHOLD = 4.0
MIN_ABS_DROP_PP = 0.08
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
