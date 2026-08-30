"""The only four read-only functions available to the diagnosis model."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, WINDOW_SECONDS
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.store.interface import AttemptStore


# The detector's five scan dimensions (config.SCAN_DIMENSIONS).  They are enumerated
# rather than left as an open map because strict function schemas reject
# `additionalProperties: {...}` -- and because naming them stops the model asking for a
# dimension this data does not have.  A null value means "do not filter on this".
DIMENSIONS = ("merchant", "provider", "method", "country", "issuing_bank")

_CELL = {
    "type": "object",
    "properties": {name: {"type": ["string", "null"]} for name in DIMENSIONS},
    "required": list(DIMENSIONS),
    "additionalProperties": False,
}


def _tool(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}


TOOL_SCHEMAS = [
    _tool("get_counts",
          "Aggregate attempts and conversion counts for a time range and dimension grouping.",
          {"start_ts": {"type": "string"}, "end_ts": {"type": "string"},
           "group_by": {"type": "array", "items": {"type": "string", "enum": list(DIMENSIONS)}},
           "filters": _CELL}),
    _tool("get_samples",
          "Get raw payment attempts to characterize error and decline-code shifts.",
          {"start_ts": {"type": "string"}, "end_ts": {"type": "string"}, "filters": _CELL,
           "limit": {"type": "integer"}}),
    _tool("search_memory",
          "Find previous resolved incidents whose identity cell is lattice-related to this cell.",
          {"cell": _CELL}),
    _tool("get_baseline",
          "Get the historical conversion baseline and its fallback source for a cell.",
          {"cell": _CELL, "at_ts": {"type": "string"}}),
    _tool("get_decline_mix",
          "Share of declines per decline code for a cell over a time range. Valid over history "
          "as well as live traffic, so a cell's normal mix can be compared against its current one.",
          {"cell": _CELL, "start_ts": {"type": "string"}, "end_ts": {"type": "string"}}),
    _tool("run_sql",
          "Run ONE read-only SELECT against the raw data when the structured tools cannot express "
          "what you need (conversion as a time series, an arbitrary group-by, retry chains across "
          "payment_id, one decline code by hour). Prefer the structured tools first; only six of "
          "these are allowed per investigation. `database` is 'history' (minute/hour aggregates for "
          "the 14 days before the live stream) or 'live' (raw attempts from the live stream onward). "
          "Table schemas are in your instructions. Returns at most 200 rows.",
          {"database": {"type": "string", "enum": ["history", "live"]}, "sql": {"type": "string"}}),
]

TOOL_NAMES = {schema["name"] for schema in TOOL_SCHEMAS}


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _cell(value: Mapping[str, Any] | None) -> dict[str, str]:
    """Drop the nulls a strict schema forces the model to send for unused dimensions.

    Both stores read a filter entry as an equality match, so a null left in place would
    mean "where provider IS NULL" rather than "any provider".
    """
    return {key: item for key, item in (value or {}).items() if item is not None}


class DiagnosisTools:
    def __init__(self, store: AttemptStore, baselines: BaselineLookup, memory: IncidentRepository | None = None) -> None:
        self.store, self.baselines, self.memory = store, baselines, memory

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        method = getattr(self, name, None)
        if method is None or name not in TOOL_NAMES:
            return {"error": f"unknown tool: {name}"}
        try:
            return method(**arguments)
        except (TypeError, ValueError, KeyError) as error:
            return {"error": str(error)}

    def get_counts(self, start_ts: str, end_ts: str, group_by: list[str], filters: dict[str, str | None]) -> dict[str, Any]:
        rows = self.store.get_counts(_timestamp(start_ts), _timestamp(end_ts), BUCKET_SECONDS, group_by, _cell(filters))
        return {"rows": [{"bucket_ts": row.bucket_ts.isoformat(), "cell": dict(zip(group_by, row.dimensions)), "attempts": row.attempts, "approved": row.approved, "declined": row.declined, "error": row.error, "amount_usd_total": row.amount_usd_total} for row in rows]}

    def get_samples(self, start_ts: str, end_ts: str, filters: dict[str, str | None], limit: int) -> dict[str, Any]:
        rows = self.store.get_samples(_timestamp(start_ts), _timestamp(end_ts), _cell(filters), min(limit, 100))
        codes = Counter(row.decline_code or "none" for row in rows if row.status != "approved")
        return {"attempts": [{"event_ts": row.event_ts.isoformat(), "status": row.status, "decline_code": row.decline_code, "payment_id": row.payment_id, "attempt_number": row.attempt_number, "amount_usd": row.amount_usd} for row in rows], "decline_code_counts": dict(codes)}

    def search_memory(self, cell: dict[str, str | None]) -> dict[str, Any]:
        return {"incidents": self.memory.search_memory(_cell(cell)) if self.memory else []}

    def get_baseline(self, cell: dict[str, str | None], at_ts: str) -> dict[str, Any]:
        baseline = self.baselines.get(_cell(cell), _timestamp(at_ts))
        return {"baseline": None} if baseline is None else {"baseline": {"rate": baseline.rate, "dispersion": baseline.dispersion, "source": baseline.source}}

    def get_decline_mix(self, cell: dict[str, str | None], start_ts: str, end_ts: str) -> dict[str, Any]:
        """Decline shares for a cell.  The historical declines table is the correct
        source for a *mix* even though get_counts refuses it for a *rate* (D72)."""
        start, end, filters = _timestamp(start_ts), _timestamp(end_ts), _cell(cell)
        if hasattr(self.store, "get_decline_mix"):
            return {"decline_mix": self.store.get_decline_mix(start, end, filters)}
        # Same fallback the detector uses when the store offers no window-wide mix.
        rows = self.store.get_samples(start, end, filters, 100)
        failures = Counter(row.decline_code for row in rows if row.status != "approved" and row.decline_code)
        total = sum(failures.values())
        return {"decline_mix": {code: count / total for code, count in failures.items()} if total else {}}

    def run_sql(self, database: str, sql: str) -> dict[str, Any]:
        """One read-only SELECT against the gold layer.  Read-only is enforced by the
        connection mode (`read_only=True` / `mode=ro`); the parse check is defence in
        depth.  Only available when the store is backed by the gold files."""
        gold_dir = getattr(self.store, "_gold_dir", None)
        if gold_dir is None:
            return {"error": "run_sql is unavailable in this run; use the structured tools."}
        if database not in ("history", "live"):
            return {"error": "database must be 'history' or 'live'"}
        stripped = sql.strip().rstrip(";").strip()
        if not re.match(r"(?is)^(select|with)\b", stripped) or ";" in stripped:
            return {"error": "only a single read-only SELECT or WITH statement is allowed"}
        if re.search(r"(?i)\b(attach|copy|install|load_extension|pragma|export|sqlite_master)\b", stripped):
            return {"error": "statement contains a non-read keyword"}
        try:
            from pipeline.gold.schema import HISTORICAL_DB_FILENAME, LIVE_DB_FILENAME
            if database == "history":
                import duckdb
                conn = duckdb.connect(str(Path(gold_dir) / HISTORICAL_DB_FILENAME), read_only=True)
                try:
                    try:
                        conn.execute("SET statement_timeout='10s'")
                    except Exception:
                        pass
                    cursor = conn.execute(stripped)
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(201)
                finally:
                    conn.close()
            else:
                import sqlite3
                conn = sqlite3.connect(f"file:{Path(gold_dir) / LIVE_DB_FILENAME}?mode=ro", uri=True, timeout=2.0)
                try:
                    conn.execute("PRAGMA busy_timeout=2000")
                    cursor = conn.execute(stripped)
                    columns = [d[0] for d in cursor.description]
                    rows = cursor.fetchmany(201)
                finally:
                    conn.close()
        except Exception as error:
            return {"error": f"{type(error).__name__}: {error}"}
        return {"columns": columns, "rows": [dict(zip(columns, row)) for row in rows[:200]],
                "truncated": len(rows) > 200}
