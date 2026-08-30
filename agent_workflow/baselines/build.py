"""Build an immutable, versioned baseline artifact from historical attempts."""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from agent_workflow.config import BUCKET_SECONDS, MIN_HISTORY_OBSERVATIONS, SCAN_GROUPINGS, WINDOW_BUCKETS
from agent_workflow.store.interface import AttemptStore


def _hour_of_week(ts: datetime) -> int:
    return ts.weekday() * 24 + ts.hour


def _cell_key(grouping: Sequence[str], values: Sequence[str | None]) -> str:
    return "|".join(f"{name}={value if value is not None else '<null>'}" for name, value in zip(grouping, values))


def _rolling_windows(store: AttemptStore, grouping: Sequence[str], start: datetime, end: datetime):
    rows = store.get_counts(start, end, BUCKET_SECONDS, grouping, {})
    cells: dict[tuple[str | None, ...], dict[datetime, tuple[int, int]]] = defaultdict(dict)
    for row in rows:
        cells[row.dimensions][row.bucket_ts] = (row.attempts, row.approved)
    for values, buckets in cells.items():
        for end_bucket in sorted(buckets):
            timestamps = [end_bucket - timedelta(seconds=BUCKET_SECONDS * offset) for offset in range(WINDOW_BUCKETS - 1, -1, -1)]
            if not all(ts in buckets for ts in timestamps):
                continue
            attempts = sum(buckets[ts][0] for ts in timestamps)
            approved = sum(buckets[ts][1] for ts in timestamps)
            if attempts:
                yield values, end_bucket, attempts, approved


def build(store: AttemptStore, start: datetime, end: datetime, *, excluded_windows: Iterable[datetime] = (),
          excluded_intervals: Iterable[tuple[datetime, datetime]] = (), groupings=SCAN_GROUPINGS) -> dict:
    """Create baseline rows, excluding known incident windows supplied by the caller."""
    excluded = {ts.astimezone(timezone.utc) for ts in excluded_windows}
    intervals = [(left.astimezone(timezone.utc), right.astimezone(timezone.utc))
                 for left, right in excluded_intervals]
    observations: dict[str, list[tuple[int, float]]] = defaultdict(list)
    metadata: dict[str, tuple[tuple[str, ...], tuple[str | None, ...]]] = {}
    for grouping in groupings:
        for values, ts, attempts, approved in _rolling_windows(store, grouping, start, end):
            # Exclude a rolling observation if any part of its window overlaps a known incident.
            window_start = ts - timedelta(seconds=BUCKET_SECONDS * (WINDOW_BUCKETS - 1))
            if ts in excluded or any(window_start <= right and ts >= left for left, right in intervals):
                continue
            key = _cell_key(grouping, values)
            metadata[key] = (tuple(grouping), tuple(values))
            observations[key].append((_hour_of_week(ts), approved / attempts))

    cells = {}
    for key, values in observations.items():
        grouping, cell_values = metadata[key]
        all_time = sum(rate for _, rate in values) / len(values)
        by_hour: dict[int, list[float]] = defaultdict(list)
        for hour, rate in values:
            by_hour[hour].append(rate)
        levels = {str(hour): sum(rates) / len(rates) for hour, rates in by_hour.items()}
        residuals = [rate - levels[str(hour)] for hour, rate in values]
        mean_residual = sum(residuals) / len(residuals)
        dispersion = math.sqrt(sum((value - mean_residual) ** 2 for value in residuals) / len(residuals))
        cells[key] = {
            "dimensions": list(grouping), "values": list(cell_values), "observations": len(values),
            "all_time_rate": all_time, "hour_of_week_rates": levels,
            "hour_of_week_observations": {str(hour): len(rates) for hour, rates in by_hour.items()},
            "dispersion": dispersion,
        }
    return {"schema_version": 1, "bucket_seconds": BUCKET_SECONDS, "window_buckets": WINDOW_BUCKETS,
            "min_history_observations": MIN_HISTORY_OBSERVATIONS, "cells": cells}


def write_version(artifact: Mapping, data_dir: str | Path) -> Path:
    """Write a new immutable artifact then atomically update the current pointer."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    versions = [int(path.stem.split("v")[-1]) for path in directory.glob("baselines_v*.json") if path.stem.split("v")[-1].isdigit()]
    target = directory / f"baselines_v{max(versions, default=0) + 1}.json"
    target.write_text(json.dumps(artifact, sort_keys=True))
    pointer_tmp = directory / "baselines_current.tmp"
    pointer_tmp.write_text(target.name)
    os.replace(pointer_tmp, directory / "baselines_current")
    return target
