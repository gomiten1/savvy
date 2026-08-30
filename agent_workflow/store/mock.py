"""CSV-backed local store used until the gold-layer store is available.

It intentionally depends only on the Python standard library so T0/T1 can run on a
fresh machine.  It implements exactly the production store seam, nothing more.
"""

from __future__ import annotations

import csv
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .interface import Attempt, CountRow, DIMENSION_COLUMNS


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _canonical_key(key: str) -> str:
    return DIMENSION_COLUMNS.get(key, key)


class MockStore:
    def __init__(self, attempts: Sequence[Attempt]) -> None:
        self._attempts = tuple(sorted(attempts, key=lambda row: row.event_ts))
        self._timestamps = tuple(row.event_ts for row in self._attempts)

    def _between(self, start_ts: datetime, end_ts: datetime) -> Sequence[Attempt]:
        """Use the timestamp index so T1 replays don't rescan the full CSV."""
        start = bisect_left(self._timestamps, start_ts)
        end = bisect_left(self._timestamps, end_ts)
        return self._attempts[start:end]

    @classmethod
    def from_csv(cls, path: str | Path) -> "MockStore":
        with Path(path).open(newline="") as handle:
            rows = []
            for raw in csv.DictReader(handle):
                rows.append(
                    Attempt(
                        attempt_id=raw["attempt_id"], payment_id=raw["payment_id"],
                        attempt_number=int(raw["attempt_number"]), event_ts=_parse_ts(raw["event_ts"]),
                        merchant_id=raw["merchant_id"], provider_id=raw["provider_id"],
                        method=raw["method"], country=raw["country"],
                        issuing_bank=raw["issuing_bank"] or None, status=raw["status"],
                        decline_code=raw["decline_code"] or None, amount_minor=int(raw["amount_minor"]),
                        currency=raw["currency"], amount_usd=float(raw["amount_usd"]),
                    )
                )
        return cls(rows)

    def get_counts(self, start_ts: datetime, end_ts: datetime, bucket: int | timedelta,
                   group_by: Sequence[str], filters: Mapping[str, str | None]) -> list[CountRow]:
        seconds = int(bucket.total_seconds()) if isinstance(bucket, timedelta) else bucket
        if seconds <= 0:
            raise ValueError("bucket must be positive seconds")
        columns = tuple(_canonical_key(name) for name in group_by)
        normalized_filters = {_canonical_key(key): value for key, value in filters.items()}
        origin = int(start_ts.timestamp())
        aggregates: dict[tuple[datetime, tuple[str | None, ...]], list[float]] = defaultdict(lambda: [0, 0, 0, 0, 0.0])
        for attempt in self._between(start_ts, end_ts):
            if any(getattr(attempt, key) != value for key, value in normalized_filters.items()):
                continue
            bucket_ts = datetime.fromtimestamp(origin + ((int(attempt.event_ts.timestamp()) - origin) // seconds) * seconds, tz=timezone.utc)
            dimensions = tuple(getattr(attempt, column) for column in columns)
            values = aggregates[(bucket_ts, dimensions)]
            values[0] += 1
            values[1] += attempt.status == "approved"
            values[2] += attempt.status == "declined"
            values[3] += attempt.status == "error"
            values[4] += attempt.amount_usd
        ordered = sorted(aggregates.items(), key=lambda item: (item[0][0], tuple(value or "" for value in item[0][1])))
        return [CountRow(bucket_ts, dimensions, int(values[0]), int(values[1]), int(values[2]), int(values[3]), values[4])
                for (bucket_ts, dimensions), values in ordered]

    def get_samples(self, start_ts: datetime, end_ts: datetime, filters: Mapping[str, str | None], limit: int) -> list[Attempt]:
        normalized_filters = {_canonical_key(key): value for key, value in filters.items()}
        return [attempt for attempt in self._between(start_ts, end_ts)
                if all(getattr(attempt, key) == value for key, value in normalized_filters.items())][:limit]
