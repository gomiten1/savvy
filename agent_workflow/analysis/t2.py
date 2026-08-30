"""T2 detection-latency replay with deterministic conversion-drop injections.

Baselines always come from the unmodified backfill.  Each scenario then replays a
fresh injected copy through the same scan, cluster, and lifecycle path as the app.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from agent_workflow.baselines.build import build
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, MIN_ATTEMPTS, WINDOW_BUCKETS
from agent_workflow.detect.cluster import cluster, lattice_related
from agent_workflow.detect.registry import IncidentRegistry
from agent_workflow.detect.scan import scan
from agent_workflow.store.interface import Attempt
from agent_workflow.store.mock import MockStore


def _matches(attempt: Attempt, cell: Mapping[str, str | None]) -> bool:
    columns = {
        "merchant": "merchant_id",
        "provider": "provider_id",
        "method": "method",
        "country": "country",
        "issuing_bank": "issuing_bank",
    }
    return all(getattr(attempt, columns[name]) == value for name, value in cell.items())


def inject_conversion_drop(store: MockStore, cell: Mapping[str, str | None], start: datetime,
                           end: datetime, target_rate: float) -> MockStore:
    """Return a copy with approved attempts flipped to declines per bucket.

    We only introduce failures; this keeps the injection causal and deterministic.
    """
    target_rate = max(0.0, min(1.0, target_rate))
    affected: dict[datetime, list[Attempt]] = {}
    for attempt in store._attempts:
        if start <= attempt.event_ts < end and _matches(attempt, cell):
            bucket = attempt.event_ts.replace(second=0, microsecond=0)
            bucket -= timedelta(minutes=bucket.minute % (BUCKET_SECONDS // 60))
            affected.setdefault(bucket, []).append(attempt)

    replacements: dict[str, Attempt] = {}
    for rows in affected.values():
        approvals = sorted((row for row in rows if row.status == "approved"), key=lambda row: row.attempt_id)
        desired_approvals = int(len(rows) * target_rate)
        for row in approvals[max(0, desired_approvals):]:
            replacements[row.attempt_id] = replace(row, status="declined", decline_code="injected_conversion_drop")
    return MockStore([replacements.get(row.attempt_id, row) for row in store._attempts])


def _candidate_cells(store: MockStore, grouping: Sequence[str], start: datetime, end: datetime):
    for tick in _ticks(start, end):
        window_start = tick - timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
        counts = store.get_counts(window_start, tick, BUCKET_SECONDS, grouping, {})
        totals: dict[tuple[str | None, ...], int] = {}
        for row in counts:
            totals[row.dimensions] = totals.get(row.dimensions, 0) + row.attempts
        for values, attempts in totals.items():
            if attempts >= MIN_ATTEMPTS:
                yield attempts, tick - timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS), dict(zip(grouping, values))


def _ticks(start: datetime, end: datetime):
    tick = start + timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
    while tick <= end:
        yield tick
        tick += timedelta(seconds=BUCKET_SECONDS)


def _select_cells(store: MockStore, start: datetime, end: datetime):
    """Pick reproducible high- and near-threshold cells from the actual backfill."""
    candidates = list(_candidate_cells(store, ("provider",), start, end))
    high = max(candidates, key=lambda item: item[0])
    pair_candidates = list(_candidate_cells(store, ("merchant", "provider"), start, end))
    near = min(pair_candidates, key=lambda item: item[0])
    return {"high_traffic": high, "near_threshold": near}


def replay_scenario(clean_store: MockStore, baseline_lookup: BaselineLookup, *, grouping: Sequence[str],
                    cell: Mapping[str, str | None], onset: datetime, magnitude_pp: int) -> dict:
    target_rate = max(0.01, baseline_lookup.get(cell, onset).rate - magnitude_pp / 100)
    injected = inject_conversion_drop(clean_store, cell, onset, onset + timedelta(hours=1), target_rate)
    registry = IncidentRegistry(injected, baseline_lookup)
    end = onset + timedelta(hours=1)
    for tick in _ticks(onset - timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS), end):
        signals = scan(injected, baseline_lookup, tick, groupings=(grouping,), characterize=False)
        registry.tick(cluster(signals), tick)
        matches = [incident for incident in registry.open_incidents()
                   if lattice_related(incident.identity_cell, cell)]
        if matches:
            incident = matches[0]
            return {
                "opened": True,
                "onset": onset,
                "detected_at": incident.opened_at,
                "latency_minutes": (incident.opened_at - onset).total_seconds() / 60,
                "identity_cell": incident.identity_cell,
            }
    return {"opened": False, "onset": onset, "detected_at": None, "latency_minutes": None, "identity_cell": None}


def run(store: MockStore) -> list[dict]:
    start = store._attempts[0].event_ts.replace(second=0, microsecond=0)
    end = store._attempts[-1].event_ts.replace(second=0, microsecond=0)
    selected = _select_cells(store, start, end - timedelta(hours=2))
    results = []
    for volume, (_, onset, cell) in selected.items():
        grouping = tuple(cell)
        artifact = build(store, start, end, groupings=(grouping,))
        lookup = BaselineLookup(artifact)
        for magnitude in (10, 25, 60):
            result = replay_scenario(store, lookup, grouping=grouping, cell=cell, onset=onset, magnitude_pp=magnitude)
            result.update({"volume": volume, "magnitude_pp": magnitude, "cell": cell})
            results.append(result)
    return results


def format_report(results: list[dict]) -> str:
    lines = ["# T2 detection-latency results", "", "| cell volume | injected drop | cell | opened | latency |", "|---|---:|---|---|---:|"]
    for row in results:
        latency = f"{row['latency_minutes']:.0f} min" if row["latency_minutes"] is not None else "—"
        cell = ", ".join(f"{key}={value}" for key, value in row["cell"].items())
        lines.append(f"| {row['volume']} | -{row['magnitude_pp']}pp | {cell} | {'yes' if row['opened'] else 'no'} | {latency} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--output", default="docs/T2-RESULTS.md")
    args = parser.parse_args()
    report = format_report(run(MockStore.from_csv(args.input)))
    with open(args.output, "w") as handle:
        handle.write(report)
    print(report)
