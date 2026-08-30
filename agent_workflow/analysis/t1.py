"""Replay a clean backfill through the detector; T1 passes only with zero signals."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta

from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, MIN_ABS_DROP_PP, MIN_ATTEMPTS, SCAN_GROUPINGS, WINDOW_BUCKETS, Z_THRESHOLD
from agent_workflow.store.mock import MockStore


def run(store: MockStore, data_dir: str) -> tuple[int, int]:
    attempts = store._attempts
    start = attempts[0].event_ts.replace(second=0, microsecond=0)
    end = attempts[-1].event_ts.replace(second=0, microsecond=0) + timedelta(minutes=1)
    artifact = build(store, start, end)
    write_version(artifact, data_dir)
    lookup = BaselineLookup.from_data_dir(data_dir)
    # Materialize each GROUP BY once.  The production path still polls `scan`; this
    # replay cache keeps calibration fast enough to run repeatedly during a demo.
    aggregates = {}
    for grouping in SCAN_GROUPINGS:
        by_bucket = defaultdict(dict)
        for row in store.get_counts(start, end, BUCKET_SECONDS, grouping, {}):
            by_bucket[row.bucket_ts][row.dimensions] = (row.attempts, row.approved)
        aggregates[grouping] = by_bucket

    scans, signals = 0, 0
    tick = start + timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
    while tick <= end:
        scans += 1
        bucket_times = [tick - timedelta(seconds=BUCKET_SECONDS * offset) for offset in range(WINDOW_BUCKETS, 0, -1)]
        for grouping, by_bucket in aggregates.items():
            totals = defaultdict(lambda: [0, 0])
            for bucket_ts in bucket_times:
                for values, (attempts, approved) in by_bucket[bucket_ts].items():
                    totals[values][0] += attempts
                    totals[values][1] += approved
            for values, (attempts, approved) in totals.items():
                if attempts < MIN_ATTEMPTS:
                    continue
                baseline = lookup.get(dict(zip(grouping, values)), tick)
                if baseline is None or baseline.dispersion == 0:
                    continue
                drop = baseline.rate - approved / attempts
                if drop >= MIN_ABS_DROP_PP and drop / baseline.dispersion >= Z_THRESHOLD:
                    signals += 1
        tick += timedelta(seconds=BUCKET_SECONDS)
    return scans, signals


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    scans, signals = run(MockStore.from_csv(args.input), args.data_dir)
    print(f"T1: {scans} scans, {signals} clean-history signals")
