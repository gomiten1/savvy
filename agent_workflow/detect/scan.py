"""Enumerate non-empty 1-D/2-D cells and emit gated conversion-drop signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, MIN_ABS_DROP_PP, MIN_ATTEMPTS, SCAN_GROUPINGS, WINDOW_BUCKETS, Z_THRESHOLD
from agent_workflow.store.interface import AttemptStore


@dataclass(frozen=True)
class Signal:
    cell: dict[str, str | None]
    observed_rate: float
    baseline_rate: float
    baseline_source: str
    z_score: float
    lost_approvals: float
    attempts: int
    approved: int
    error_share: float
    decline_code_mix: dict[str, float]
    amount_usd_total: float


def scan(store: AttemptStore, baselines: BaselineLookup, end_ts: datetime, *, groupings: Sequence[Sequence[str]] = SCAN_GROUPINGS,
         characterize: bool = True) -> list[Signal]:
    """Scan the five closed aggregate buckets ending at ``end_ts``."""
    start_ts = end_ts - timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
    signals = []
    for grouping in groupings:
        totals = defaultdict(lambda: [0, 0, 0, 0, 0.0])
        for row in store.get_counts(start_ts, end_ts, BUCKET_SECONDS, grouping, {}):
            total = totals[row.dimensions]
            total[0] += row.attempts; total[1] += row.approved; total[2] += row.declined; total[3] += row.error; total[4] += row.amount_usd_total
        for values, (attempts, approved, declined, errors, amount) in totals.items():
            if attempts < MIN_ATTEMPTS:
                continue
            cell = dict(zip(grouping, values))
            baseline = baselines.get(cell, end_ts)
            if baseline is None or baseline.dispersion == 0:
                continue
            observed = approved / attempts
            drop = baseline.rate - observed
            z_score = drop / baseline.dispersion
            if drop < MIN_ABS_DROP_PP or z_score < Z_THRESHOLD:
                continue
            if characterize:
                # get_samples is ORDER BY event_ts DESC and capped, so counting codes
                # off it biases toward the window's tail.  Prefer the store's own
                # window-wide decline mix when it offers one (GoldStore does).
                if hasattr(store, "get_decline_mix"):
                    decline_code_mix = store.get_decline_mix(start_ts, end_ts, cell)
                else:
                    samples = store.get_samples(start_ts, end_ts, cell, attempts)
                    failures = Counter(row.decline_code for row in samples if row.status != "approved" and row.decline_code)
                    failure_total = sum(failures.values())
                    decline_code_mix = {code: count / failure_total for code, count in failures.items()} if failure_total else {}
            else:
                decline_code_mix = {}
            signals.append(Signal(cell, observed, baseline.rate, baseline.source, z_score, attempts * drop, attempts, approved,
                                  errors / attempts, decline_code_mix, amount))
    return sorted(signals, key=lambda signal: signal.lost_approvals, reverse=True)
