"""Gold-layer adapter: lets the detector and diagnosis agent read `pipeline/`'s
gold layer through the `AttemptStore` seam.

This is a translation layer and nothing else (D72).  It converts types, names and
formats between the two sides.  It never computes, infers, substitutes or cleans:
if the pipeline says ``unknown_bank``, this returns ``unknown_bank``.  The single
exception is a raised ``ValueError`` when ``decline_code`` is requested over the
historical window, where the pipeline silently serves a declines-only table that
reports ``approved = 0`` and any conversion rate off it is structurally wrong.

``MockStore`` stays as the permanent test double (D73); this does not replace it.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import duckdb

from pipeline.gold.access import GOLD_DIR, get_counts as _pipeline_get_counts, get_samples as _pipeline_get_samples
from pipeline.gold.schema import HISTORICAL_DB_FILENAME, LIVE_DB_FILENAME

from .interface import Attempt, CountRow, DIMENSION_COLUMNS


# Protocol bucket (int seconds / timedelta) -> the pipeline's bucket word.
_BUCKET_WORDS = {60: "minute", 3600: "hour", 86400: "day"}


def _ts(value: datetime) -> str:
    """Format an instant the way the pipeline compares timestamps: UTC, trailing 'Z'.

    ``datetime.isoformat()`` produces ``+00:00``, which sorts *before* ``Z`` in the
    lexicographic string comparison the pipeline runs in SQL, so every query would
    silently return a wrong range with no error.  A naive or non-UTC datetime would
    format to the wrong instant, so convert to UTC first.
    """
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _bucket_word(bucket) -> str:
    seconds = int(bucket.total_seconds()) if hasattr(bucket, "total_seconds") else int(bucket)
    try:
        return _BUCKET_WORDS[seconds]
    except KeyError:
        raise ValueError(
            f"bucket {seconds!r}s has no pipeline grain; expected one of "
            f"{sorted(_BUCKET_WORDS)} (minute/hour/day)"
        ) from None


def _canonical(name: str) -> str:
    return DIMENSION_COLUMNS.get(name, name)


def _read_with_retry(fn, *, attempts: int = 6, base_delay: float = 0.05):
    """Retry a read that raced the live SQLite writer.

    `pipeline/gold/access.py` opens `live.sqlite` per call with no `busy_timeout`,
    so a read that lands while Generator B is committing raises
    `OperationalError: database is locked`.  SQLite's one-writer/many-reader model
    handles this pattern; the pipeline just does not wait on the lock, so the
    adapter does — same data, one call up.
    """
    for index in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or index == attempts - 1:
                raise
            print(f"[detector] SQLite busy; retrying read ({index + 1}/{attempts})", file=sys.stderr)
            time.sleep(base_delay * (index + 1))


class GoldStore:
    """`AttemptStore` implementation over `data/gold/` (historical DuckDB + live SQLite)."""

    def __init__(self, gold_dir: str | Path | None = None) -> None:
        self._gold_dir = Path(gold_dir) if gold_dir is not None else Path(GOLD_DIR)
        meta = self._read_meta()
        self._history_start_iso = meta["history_start"]
        self._history_end_iso = meta["history_end"]

    # -- construction-time meta read; no long-lived connection is kept ----------
    def _read_meta(self) -> dict[str, str]:
        hist_path = self._gold_dir / HISTORICAL_DB_FILENAME
        if not hist_path.exists():
            raise FileNotFoundError(
                f"{hist_path} is absent; run pipeline.generator.generate_historical_aggregates first"
            )
        conn = duckdb.connect(str(hist_path), read_only=True)
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
        meta = dict(rows)
        for key in ("history_start", "history_end"):
            if key not in meta:
                raise ValueError(f"historical.duckdb meta has no {key} key")
        return meta

    @property
    def history_start(self) -> datetime:
        return _dt(self._history_start_iso)

    @property
    def history_end(self) -> datetime:
        return _dt(self._history_end_iso)

    def latest_live_event_ts(self) -> datetime | None:
        """MAX(event_ts) in live_attempts, or None before the live stream writes a row.

        The live detector clock is driven off this, not wall-clock (D74): Generator B
        runs at a speed multiplier from history_end, so sim time and wall time diverge
        immediately.  Read-only, no lock, connection not held (see gold schema note).
        """
        live_path = self._gold_dir / LIVE_DB_FILENAME
        if not live_path.exists():
            return None

        def _read():
            conn = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True, timeout=2.0)
            try:
                conn.execute("PRAGMA busy_timeout=2000")
                return conn.execute("SELECT MAX(event_ts) FROM live_attempts").fetchone()
            finally:
                conn.close()

        row = _read_with_retry(_read)
        return _dt(row[0]) if row and row[0] else None

    # -- AttemptStore ---------------------------------------------------------
    def get_counts(
        self,
        start_ts: datetime,
        end_ts: datetime,
        bucket,
        group_by: Sequence[str],
        filters: Mapping[str, str | None],
    ) -> list[CountRow]:
        word = _bucket_word(bucket)
        start_iso, end_iso = _ts(start_ts), _ts(end_ts)
        group_by = list(group_by)
        filters = dict(filters)

        if ("decline_code" in group_by or "decline_code" in filters) and start_iso < self._history_end_iso:
            raise ValueError(
                "decline_code over the historical window reads a declines-only table with no "
                f"approved counts. Use start_ts >= {self._history_end_iso} for a conversion "
                "rate, or call get_decline_mix() for the decline mix."
            )

        columns = [_canonical(name) for name in group_by]
        pipeline_filters = {_canonical(key): value for key, value in filters.items()}

        rows = _read_with_retry(lambda: _pipeline_get_counts(
            start_iso, end_iso, bucket=word, group_by=columns,
            filters=pipeline_filters, gold_dir=str(self._gold_dir),
        ))

        result = []
        for row in rows:
            granularity = row.get("bucket_granularity")
            if granularity != word:
                raise ValueError(
                    f"pipeline served {granularity!r}-grain rows for a {word!r} request "
                    f"(decline-table switch?); group_by={group_by} filters={filters}"
                )
            dimensions = tuple(row[column] for column in columns)
            result.append(CountRow(
                bucket_ts=_dt(row["bucket_ts"]),
                dimensions=dimensions,
                attempts=int(row["attempts"]),
                approved=int(row["approved"]),
                declined=int(row["declined"]),
                error=int(row["error"]),
                amount_usd_total=float(row["amount_usd_total"]),
            ))
        return result

    def get_samples(
        self,
        start_ts: datetime,
        end_ts: datetime,
        filters: Mapping[str, str | None],
        limit: int,
    ) -> list[Attempt]:
        pipeline_filters = {_canonical(key): value for key, value in dict(filters).items()}
        # scan.py passes `attempts` as the limit, tens of thousands of rows per cell
        # at pipeline volume; the query is ORDER BY event_ts DESC so a large limit
        # only drags the window's tail back. Clamp it.
        capped = max(1, min(int(limit), 500))
        rows = _read_with_retry(lambda: _pipeline_get_samples(
            _ts(start_ts), _ts(end_ts), filters=pipeline_filters,
            limit=capped, gold_dir=str(self._gold_dir),
        ))
        return [
            Attempt(
                attempt_id=row["attempt_id"],
                payment_id=row["payment_id"],
                attempt_number=row["attempt_number"],
                event_ts=_dt(row["event_ts"]),
                merchant_id=row["merchant_id"],
                provider_id=row["provider_id"],
                method=row["method"],
                country=row["country"],
                issuing_bank=row["issuing_bank"],
                status=row["status"],
                decline_code=row["decline_code"],
                amount_minor=row["amount_minor"],
                currency=row["currency"],
                amount_usd=row["amount_usd"],
            )
            for row in rows
        ]

    # -- historical comparison that the rate path cannot do -------------------
    def get_decline_mix(
        self,
        start_ts: datetime,
        end_ts: datetime,
        filters: Mapping[str, str | None],
    ) -> dict[str, float]:
        """Share of declines per code. Valid over history and live.

        The declines-only historical table is the *correct* source for a mix -- a
        mix is a share of declines and that table is declines-only. Only rate math
        breaks on it, which is why get_counts() guards it and this does not.
        """
        pipeline_filters = {_canonical(key): value for key, value in dict(filters).items()}
        rows = _read_with_retry(lambda: _pipeline_get_counts(
            _ts(start_ts), _ts(end_ts), bucket="hour", group_by=["decline_code"],
            filters=pipeline_filters, gold_dir=str(self._gold_dir),
        ))
        totals: dict[str, float] = {}
        for row in rows:
            # Successful attempts have no decline code. They are present in the
            # rate table but are not part of a decline mix; retaining their
            # ``None`` key later breaks the diagnosis prompt's sorted JSON.
            code = row["decline_code"]
            if code is not None:
                totals[code] = totals.get(code, 0) + row["attempts"]
        grand = sum(totals.values())
        if not grand:
            return {}
        return {code: count / grand for code, count in totals.items()}
