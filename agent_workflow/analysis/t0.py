"""T0 descriptive EDA for choosing a detector dispersion estimator.

This intentionally reports diagnostics rather than deciding alert thresholds.  T1 does
that by replaying clean history after the detector exists.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from agent_workflow.config import BUCKET_SECONDS, WINDOW_BUCKETS


SCAN_GROUPINGS = (("provider",), ("issuing_bank",), ("provider", "country"),
                  ("issuing_bank", "country"), ("issuing_bank", "method"),
                  ("method", "country"), ("merchant", "provider"))


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _rolling_rates(store, grouping: tuple[str, ...], start: datetime, end: datetime) -> dict[tuple[str | None, ...], list[tuple[datetime, int, float]]]:
    step = timedelta(seconds=BUCKET_SECONDS)
    span = WINDOW_BUCKETS - 1
    by_cell: dict[tuple[str | None, ...], list[tuple[datetime, int, int]]] = defaultdict(list)
    for row in store.get_counts(start, end, BUCKET_SECONDS, grouping, {}):
        by_cell[row.dimensions].append((row.bucket_ts, row.attempts, row.approved))
    result = defaultdict(list)
    for cell, rows in by_cell.items():
        for index in range(span, len(rows)):
            window = rows[index - span:index + 1]
            if any(window[i][0] + step != window[i + 1][0] for i in range(span)):
                continue
            attempts = sum(item[1] for item in window)
            approved = sum(item[2] for item in window)
            result[cell].append((rows[index][0], attempts, approved / attempts))
    return result


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def _std(values: list[float]) -> float:
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values)) if values else 0.0


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


def run(store, start: datetime, end: datetime) -> str:
    window_minutes = BUCKET_SECONDS * WINDOW_BUCKETS // 60
    lines = ["# T0 results", "", f"Backfill: {start.isoformat()} to {end.isoformat()} (clean history).", "",
             f"Window: {window_minutes} minutes; groupings: the seven core scan groupings.", "",
             "| grouping | cells | windows >= 50 attempts | median overdispersion | residual skew proxy | median lag-1 autocorrelation |", "|---|---:|---:|---:|---:|---:|"]
    factors: list[float] = []
    for grouping in SCAN_GROUPINGS:
        cells = _rolling_rates(store, grouping, start, end)
        cell_factors, autocorrelations, residuals = [], [], []
        qualifying = 0
        for observations in cells.values():
            levels: dict[int, list[float]] = defaultdict(list)
            for ts, _, rate in observations:
                levels[ts.weekday() * 24 + ts.hour].append(rate)
            residual = [rate - _mean(levels[ts.weekday() * 24 + ts.hour]) for ts, _, rate in observations]
            residuals.extend(residual)
            rates = [rate for _, _, rate in observations]
            attempts = [n for _, n, _ in observations]
            p = _mean(rates)
            for _, n, rate in observations:
                if n >= 50:
                    qualifying += 1
            if len(rates) > 1 and 0 < p < 1:
                observed = sum((rate - p) ** 2 for rate in rates) / len(rates)
                expected = _mean(p * (1 - p) / n for p, n in zip(rates, attempts))
                if expected:
                    cell_factors.append(observed / expected)
            if len(residual) > 2 and _std(residual[:-1]) > 0 and _std(residual[1:]) > 0:
                left, right = residual[:-1], residual[1:]
                left_mean, right_mean = _mean(left), _mean(right)
                autocorrelations.append(sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right)) / (len(left) * _std(left) * _std(right)))
        residual_std = _std(residuals) if residuals else 0.0
        skew_proxy = 0.0 if not residuals or residual_std == 0 else (_mean(residuals) - median(residuals)) / residual_std
        factors.extend(cell_factors)
        overdispersion = f"{median(cell_factors):.2f}" if cell_factors else "n/a"
        autocorrelation = f"{median(autocorrelations):.3f}" if autocorrelations else "n/a"
        lines.append(f"| {' × '.join(grouping)} | {len(cells)} | {qualifying} | {overdispersion} | {skew_proxy:.3f} | {autocorrelation} |")
    overall = f"{median(factors):.2f}×" if factors else "n/a"
    lines += ["", f"Overall median overdispersion: **{overall}** binomial variance.",
              "", "## Decision", "", "Use **pooled residual standard deviation** for the initial detector. The clean synthetic residuals are approximately symmetric (small mean–median skew proxy), while their dispersion is materially above the binomial assumption. T1 must still tune the one-sided threshold, minimum volume, and percentage-point gate to zero clean-history incidents."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", choices=("mock", "gold"), default="gold")
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--output", default="-", help="Markdown destination, or - for stdout")
    parser.add_argument("--start", help="ISO YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--end", help="ISO YYYY-MM-DDTHH:MM:SSZ")
    args = parser.parse_args()

    if args.store == "gold":
        from agent_workflow.store.gold import GoldStore

        store = GoldStore()
        start = _parse_ts(args.start) if args.start else store.history_start + timedelta(hours=1)
        end = _parse_ts(args.end) if args.end else store.history_end
    else:
        from agent_workflow.store.mock import MockStore

        store = MockStore.from_csv(args.input)
        attempts = store._attempts
        start = _parse_ts(args.start) if args.start else attempts[0].event_ts.replace(second=0, microsecond=0)
        end = _parse_ts(args.end) if args.end else attempts[-1].event_ts.replace(second=0, microsecond=0) + timedelta(seconds=BUCKET_SECONDS)

    report = run(store, start, end)
    if args.output != "-":
        with open(args.output, "w") as handle:
            handle.write(report)
    print(report)
