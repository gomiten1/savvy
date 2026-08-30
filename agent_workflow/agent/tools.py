"""The only four read-only functions available to the diagnosis model."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, WINDOW_SECONDS
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.store.interface import AttemptStore


TOOL_SCHEMAS = [
    {"type": "function", "name": "get_counts", "description": "Aggregate attempts and conversion counts for a time range and dimension grouping.", "strict": True,
     "parameters": {"type": "object", "properties": {"start_ts": {"type": "string"}, "end_ts": {"type": "string"}, "group_by": {"type": "array", "items": {"type": "string"}}, "filters": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}}, "required": ["start_ts", "end_ts", "group_by", "filters"], "additionalProperties": False}},
    {"type": "function", "name": "get_samples", "description": "Get raw payment attempts to characterize error and decline-code shifts.", "strict": True,
     "parameters": {"type": "object", "properties": {"start_ts": {"type": "string"}, "end_ts": {"type": "string"}, "filters": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["start_ts", "end_ts", "filters", "limit"], "additionalProperties": False}},
    {"type": "function", "name": "search_memory", "description": "Find previous resolved incidents whose identity cell is lattice-related to this cell.", "strict": True,
     "parameters": {"type": "object", "properties": {"cell": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}}, "required": ["cell"], "additionalProperties": False}},
    {"type": "function", "name": "get_baseline", "description": "Get the historical conversion baseline and its fallback source for a cell.", "strict": True,
     "parameters": {"type": "object", "properties": {"cell": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}, "at_ts": {"type": "string"}}, "required": ["cell", "at_ts"], "additionalProperties": False}},
]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class DiagnosisTools:
    def __init__(self, store: AttemptStore, baselines: BaselineLookup, memory: IncidentRepository | None = None) -> None:
        self.store, self.baselines, self.memory = store, baselines, memory

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        method = getattr(self, name, None)
        if method is None or name not in {"get_counts", "get_samples", "search_memory", "get_baseline"}:
            return {"error": f"unknown tool: {name}"}
        try:
            return method(**arguments)
        except (TypeError, ValueError, KeyError) as error:
            return {"error": str(error)}

    def get_counts(self, start_ts: str, end_ts: str, group_by: list[str], filters: dict[str, str | None]) -> dict[str, Any]:
        rows = self.store.get_counts(_timestamp(start_ts), _timestamp(end_ts), BUCKET_SECONDS, group_by, filters)
        return {"rows": [{"bucket_ts": row.bucket_ts.isoformat(), "cell": dict(zip(group_by, row.dimensions)), "attempts": row.attempts, "approved": row.approved, "declined": row.declined, "error": row.error, "amount_usd_total": row.amount_usd_total} for row in rows]}

    def get_samples(self, start_ts: str, end_ts: str, filters: dict[str, str | None], limit: int) -> dict[str, Any]:
        rows = self.store.get_samples(_timestamp(start_ts), _timestamp(end_ts), filters, min(limit, 100))
        codes = Counter(row.decline_code or "none" for row in rows if row.status != "approved")
        return {"attempts": [{"event_ts": row.event_ts.isoformat(), "status": row.status, "decline_code": row.decline_code, "payment_id": row.payment_id, "attempt_number": row.attempt_number, "amount_usd": row.amount_usd} for row in rows], "decline_code_counts": dict(codes)}

    def search_memory(self, cell: dict[str, str | None]) -> dict[str, Any]:
        return {"incidents": self.memory.search_memory(cell) if self.memory else []}

    def get_baseline(self, cell: dict[str, str | None], at_ts: str) -> dict[str, Any]:
        baseline = self.baselines.get(cell, _timestamp(at_ts))
        return {"baseline": None} if baseline is None else {"baseline": {"rate": baseline.rate, "dispersion": baseline.dispersion, "source": baseline.source}}
