"""Replay a clean backfill through the detector; T1 passes only with zero signals."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, Gates, SCAN_GROUPINGS, WINDOW_BUCKETS


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def count_signals(store, lookup: BaselineLookup, start: datetime, end: datetime, gates: Gates) -> tuple[int, int]:
    """Replay a range against an already-built candidate artifact without swapping it."""
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
                if attempts < gates.min_attempts:
                    continue
                baseline = lookup.get(dict(zip(grouping, values)), tick)
                if baseline is None or baseline.dispersion == 0:
                    continue
                drop = baseline.rate - approved / attempts
                if drop >= gates.min_abs_drop_pp and drop / baseline.dispersion >= gates.z_threshold:
                    signals += 1
        tick += timedelta(seconds=BUCKET_SECONDS)
    return scans, signals


def run(store, start: datetime, end: datetime, data_dir: str, gates: Gates | None = None) -> tuple[int, int]:
    artifact = build(store, start, end)
    write_version(artifact, data_dir)
    return count_signals(store, BaselineLookup(artifact), start, end, gates or Gates())


def _resolve_range(args, store) -> tuple[datetime, datetime]:
    if args.start and args.end:
        return _parse_ts(args.start), _parse_ts(args.end)
    if args.store == "gold":
        # Leave a margin so the first rolling windows are complete.
        start = _parse_ts(args.start) if args.start else store.history_start + timedelta(hours=1)
        end = _parse_ts(args.end) if args.end else store.history_end
        return start, end
    # mock: derive from the CSV span, the pre-refactor behaviour.
    attempts = store._attempts
    start = _parse_ts(args.start) if args.start else attempts[0].event_ts.replace(second=0, microsecond=0)
    end = _parse_ts(args.end) if args.end else attempts[-1].event_ts.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return start, end


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", choices=("mock", "gold"), default="gold")
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--start", help="ISO YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--end", help="ISO YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--build-only", action="store_true",
                        help="Write the baseline artifact without the T1 replay validation.")
    args = parser.parse_args()

    if args.store == "gold":
        from agent_workflow.store.gold import GoldStore

        store = GoldStore()
    else:
        from agent_workflow.store.mock import MockStore

        store = MockStore.from_csv(args.input)

    start, end = _resolve_range(args, store)
    if args.build_only:
        artifact = build(store, start, end)
        write_version(artifact, args.data_dir)
        print("Baselines built")
        raise SystemExit(0)
    scans, signals = run(store, start, end, args.data_dir)
    print(f"T1: {scans} scans, {signals} clean-history signals")
