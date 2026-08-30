"""Empirically derive detector gates from a clean, *held-out* replay.

The solver deliberately does not fit a probability distribution.  The detector scans
many correlated cells, so the calibration contract is simpler and stronger for the
MVP: choose the least restrictive pair of gates that produces no clean signals.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from agent_workflow.baselines.build import build
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, Gates, MIN_ATTEMPTS, SCAN_GROUPINGS, WINDOW_BUCKETS


@dataclass(frozen=True)
class CleanScore:
    """A positive clean-history deviation after the volume/baseline gates."""

    drop: float
    z_score: float


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def validate_intervals(train_start: datetime, train_end: datetime,
                       eval_start: datetime, eval_end: datetime) -> None:
    """Reject overlap: calibration must score data the baseline did not fit."""
    if train_start >= train_end or eval_start >= eval_end:
        raise ValueError("train and evaluation intervals must have positive duration")
    if eval_start < train_end:
        raise ValueError("evaluation must start at or after the end of the training interval")


def collect_scores(store, lookup: BaselineLookup, start: datetime, end: datetime, *,
                   groupings: Sequence[Sequence[str]] = SCAN_GROUPINGS,
                   min_attempts: int = MIN_ATTEMPTS,
                   excluded_intervals: Iterable[tuple[datetime, datetime]] = ()) -> tuple[int, list[CleanScore]]:
    """Replay a clean interval once and retain only its one-sided positive scores."""
    aggregates = {}
    for grouping in groupings:
        by_bucket = defaultdict(dict)
        for row in store.get_counts(start, end, BUCKET_SECONDS, grouping, {}):
            by_bucket[row.bucket_ts][row.dimensions] = (row.attempts, row.approved)
        aggregates[grouping] = by_bucket

    scans, scores = 0, []
    intervals = tuple(excluded_intervals)
    tick = start + timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
    while tick <= end:
        window_start = tick - timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
        if any(window_start <= right and tick >= left for left, right in intervals):
            tick += timedelta(seconds=BUCKET_SECONDS)
            continue
        scans += 1
        bucket_times = [tick - timedelta(seconds=BUCKET_SECONDS * offset)
                        for offset in range(WINDOW_BUCKETS, 0, -1)]
        for grouping, by_bucket in aggregates.items():
            totals = defaultdict(lambda: [0, 0])
            for bucket_ts in bucket_times:
                for values, (attempts, approved) in by_bucket[bucket_ts].items():
                    totals[values][0] += attempts
                    totals[values][1] += approved
            for values, (attempts, approved) in totals.items():
                if attempts < min_attempts:
                    continue
                baseline = lookup.get(dict(zip(grouping, values)), tick)
                if baseline is None or baseline.dispersion <= 0:
                    continue
                drop = baseline.rate - approved / attempts
                if drop > 0:
                    scores.append(CleanScore(drop, drop / baseline.dispersion))
        tick += timedelta(seconds=BUCKET_SECONDS)
    return scans, scores


def frontier(scores: Iterable[CleanScore], *, drop_step_pp: float = 1.0) -> list[tuple[float, float]]:
    """Return ``(drop_pp, worst_z)`` rows; ``0`` means no clean score remains."""
    scores = tuple(scores)
    rows = []
    for step in range(int(100 / drop_step_pp) + 1):
        drop_pp = step * drop_step_pp
        surviving = [score.z_score for score in scores if score.drop * 100 >= drop_pp]
        rows.append((drop_pp, max(surviving, default=0.0)))
    return rows


def solve(scores: Iterable[CleanScore], *, min_attempts: int = MIN_ATTEMPTS,
          max_z: float = 20.0, drop_step_pp: float = 1.0, z_step: float = 1.0) -> Gates:
    """Find the first drop floor whose zero-signal z gate is within ``max_z``.

    The z gate must be strictly greater than the largest clean z because the
    detector fires on ``>=``.
    """
    rows = frontier(scores, drop_step_pp=drop_step_pp)
    for drop_pp, worst_z in rows:
        # An empty tail is not a useful calibration: it would silently solve the
        # problem by setting the pp gate above every observed clean deviation.
        if worst_z == 0:
            continue
        threshold = (math.floor(worst_z / z_step) + 1) * z_step
        if threshold <= max_z:
            return Gates(drop_pp / 100, threshold, min_attempts)
    raise ValueError(f"no zero-signal gate pair at z <= {max_z:g}")


def format_report(scans: int, scores: Iterable[CleanScore], gates: Gates, *, drop_step_pp: float = 1.0) -> str:
    rows = frontier(scores, drop_step_pp=drop_step_pp)
    table = ["# Calibration frontier", "", f"Clean holdout scans: {scans}.", "",
             "| drop floor | worst clean z |", "|---:|---:|"]
    table.extend(f"| {drop_pp:.0f} pp | {worst_z:.2f} |" for drop_pp, worst_z in rows if worst_z)
    table += ["", "## Selected gates", "",
              f"- `MIN_ABS_DROP_PP = {gates.min_abs_drop_pp:.2f}`",
              f"- `Z_THRESHOLD = {gates.z_threshold:.1f}`",
              f"- `MIN_ATTEMPTS = {gates.min_attempts}`", ""]
    return "\n".join(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibrate detector gates on a held-out clean interval.")
    parser.add_argument("--store", choices=("mock", "gold"), default="gold")
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--train-start", required=True, help="ISO UTC; baseline fit starts here")
    parser.add_argument("--train-end", required=True, help="ISO UTC; baseline fit ends here")
    parser.add_argument("--eval-start", required=True, help="ISO UTC; clean holdout starts here")
    parser.add_argument("--eval-end", required=True, help="ISO UTC; clean holdout ends here")
    parser.add_argument("--max-z", type=float, default=20.0)
    args = parser.parse_args()

    if args.store == "gold":
        from agent_workflow.store.gold import GoldStore
        store = GoldStore()
    else:
        from agent_workflow.store.mock import MockStore
        store = MockStore.from_csv(args.input)
    train_start, train_end = _parse_ts(args.train_start), _parse_ts(args.train_end)
    eval_start, eval_end = _parse_ts(args.eval_start), _parse_ts(args.eval_end)
    validate_intervals(train_start, train_end, eval_start, eval_end)
    lookup = BaselineLookup(build(store, train_start, train_end))
    scans, scores = collect_scores(store, lookup, eval_start, eval_end)
    print(format_report(scans, scores, solve(scores, max_z=args.max_z)))
