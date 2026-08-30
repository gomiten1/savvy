"""Copy deterministic incident facts into the dashboard's revisioned SQLite sink."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS incident_reports (
  incident_id TEXT NOT NULL, revision INTEGER NOT NULL, published_at TEXT NOT NULL,
  anchor_cell TEXT NOT NULL, metric TEXT NOT NULL DEFAULT 'attempt_conversion_rate',
  baseline_rate REAL NOT NULL, observed_rate REAL NOT NULL, drop_pp REAL NOT NULL,
  dominant_decline_code TEXT, baseline_source TEXT NOT NULL,
  onset_ts TEXT NOT NULL, detected_at TEXT NOT NULL, detection_latency_s INTEGER NOT NULL,
  resolved_at TEXT, status TEXT NOT NULL, affected_entities TEXT NOT NULL,
  blast_radius REAL NOT NULL, affected_attempts INTEGER NOT NULL,
  burn_rate_usd_hour REAL NOT NULL, cumulative_loss_usd REAL NOT NULL, cost_basis TEXT NOT NULL,
  exec_one_liner TEXT NOT NULL, ops_explanation TEXT NOT NULL, evidence TEXT NOT NULL,
  confidence TEXT NOT NULL, recommended_action TEXT, alternatives_ruled_out TEXT,
  PRIMARY KEY (incident_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON incident_reports(status, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_incident ON incident_reports(incident_id, revision DESC);
"""


class ReportPublisher:
    def __init__(self, path: str | Path = "data/reports.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(DDL)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def publish(self, incident, diagnosis, revision: int | None = None, *, published_at: datetime | None = None) -> int:
        """Idempotently publish a diagnosis revision; never re-derive core figures."""
        anchor, timestamp = incident.current_anchor, (published_at or datetime.now(timezone.utc))
        if revision is None:
            with self._connect() as connection:
                revision = connection.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM incident_reports WHERE incident_id = ?", (incident.incident_id,)).fetchone()[0]
        dominant_code = max(anchor.decline_code_mix, key=anchor.decline_code_mix.get, default=None)
        entities = [{"dimension": key, "value": value, "share_of_impact": 1.0} for key, value in anchor.cell.items()]
        latency = max(0, int((incident.opened_at - incident.onset_ts).total_seconds()))
        row = (incident.incident_id, revision, timestamp.isoformat(), json.dumps(anchor.cell, sort_keys=True),
               anchor.baseline_rate, anchor.observed_rate, (anchor.baseline_rate - anchor.observed_rate) * 100,
               dominant_code, anchor.baseline_source, incident.onset_ts.isoformat(), incident.opened_at.isoformat(), latency,
               incident.resolved_at.isoformat() if incident.resolved_at else None, incident.status, json.dumps(entities),
               incident.blast_radius, anchor.attempts, incident.burn_rate_usd_hour, incident.cumulative_loss_usd,
               incident.cost_basis, diagnosis.exec_one_liner, diagnosis.ops_explanation, json.dumps(diagnosis.evidence),
               diagnosis.confidence, diagnosis.recommended_action, json.dumps(diagnosis.alternatives_ruled_out))
        with self._connect() as connection:
            connection.execute("""INSERT OR REPLACE INTO incident_reports (
                incident_id, revision, published_at, anchor_cell, baseline_rate, observed_rate, drop_pp,
                dominant_decline_code, baseline_source, onset_ts, detected_at, detection_latency_s, resolved_at, status,
                affected_entities, blast_radius, affected_attempts, burn_rate_usd_hour, cumulative_loss_usd, cost_basis,
                exec_one_liner, ops_explanation, evidence, confidence, recommended_action, alternatives_ruled_out
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        return revision
