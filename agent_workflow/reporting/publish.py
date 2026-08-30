"""Copy deterministic incident facts into the dashboard's revisioned SQLite sink."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


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


REPORT_FIELDS = (
    "incident_id", "revision", "published_at",
    "anchor_cell", "metric", "baseline_rate", "observed_rate", "drop_pp",
    "dominant_decline_code", "baseline_source",
    "onset_ts", "detected_at", "detection_latency_s", "resolved_at", "status",
    "affected_entities", "blast_radius", "affected_attempts",
    "burn_rate_usd_hour", "cumulative_loss_usd", "cost_basis",
    "exec_one_liner", "ops_explanation", "evidence", "confidence",
    "recommended_action", "alternatives_ruled_out",
)

JSON_FIELDS = {"anchor_cell", "affected_entities", "evidence", "alternatives_ruled_out"}


class ReportPublisher:
    """Publish contract rows and a browser-readable copy of the reporting sink."""

    def __init__(self, path: str | Path = "data/reports.db", *, feed_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.feed_path = Path(feed_path) if feed_path is not None else self.path.with_name("dashboard-reports.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.feed_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(DDL)
        # Make an existing reports.db visible to a dashboard as soon as the
        # workflow starts, not only after the next diagnosis completes.
        self.export_dashboard_feed()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _route_entity(cell: dict[str, str | None]) -> list[dict[str, str | float]]:
        """Represent an anchor cell as one affected route with full impact.

        Detection currently exposes one anchor cell, rather than an impact
        breakdown across multiple entities.  Treating that route as the one
        affected entity avoids inventing a split while keeping shares valid.
        """
        route = " × ".join(
            f"{dimension}={value if value is not None else 'unknown'}"
            for dimension, value in sorted(cell.items())
        )
        return [{"dimension": "route", "value": route, "share_of_impact": 1.0}]

    @staticmethod
    def _money_first_summary(incident, diagnosis) -> str:
        """Keep the contract's executive summary usable when diagnosis is inconclusive."""
        summary = (diagnosis.exec_one_liner or "").strip()
        if summary.startswith("$"):
            return summary
        route = " × ".join(
            f"{dimension}={value if value is not None else 'unknown'}"
            for dimension, value in sorted(incident.current_anchor.cell.items())
        )
        return (
            f"${incident.burn_rate_usd_hour:,.2f} per hour is at risk on {route} "
            f"since {incident.onset_ts:%H:%M UTC}."
        )

    def _reports(self) -> list[dict[str, object]]:
        fields = ", ".join(REPORT_FIELDS)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {fields} FROM incident_reports ORDER BY incident_id, revision"
            ).fetchall()
        reports = []
        for row in rows:
            report = dict(row)
            for field in JSON_FIELDS:
                report[field] = json.loads(report[field])
            reports.append(report)
        return reports

    def export_dashboard_feed(self) -> Path:
        """Atomically export all revisions in the JSON shape consumed by the web UI."""
        payload = {
            "schema_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reports": self._reports(),
        }
        temporary_path = self.feed_path.with_name(f".{self.feed_path.name}.tmp")
        try:
            temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary_path.replace(self.feed_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return self.feed_path

    def publish(self, incident, diagnosis, revision: int | None = None, *, published_at: datetime | None = None) -> int:
        """Idempotently publish a diagnosis revision; never re-derive core figures."""
        anchor, timestamp = incident.current_anchor, (published_at or datetime.now(timezone.utc))
        if revision is None:
            with self._connect() as connection:
                revision = connection.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM incident_reports WHERE incident_id = ?", (incident.incident_id,)).fetchone()[0]
        dominant_code = max(anchor.decline_code_mix, key=anchor.decline_code_mix.get, default=None)
        entities = self._route_entity(anchor.cell)
        latency = max(0, int((incident.opened_at - incident.onset_ts).total_seconds()))
        row = (incident.incident_id, revision, timestamp.isoformat(), json.dumps(anchor.cell, sort_keys=True),
               anchor.baseline_rate, anchor.observed_rate, (anchor.baseline_rate - anchor.observed_rate) * 100,
               dominant_code, anchor.baseline_source, incident.onset_ts.isoformat(), incident.opened_at.isoformat(), latency,
               incident.resolved_at.isoformat() if incident.resolved_at else None, incident.status, json.dumps(entities),
               incident.blast_radius, anchor.attempts, incident.burn_rate_usd_hour, incident.cumulative_loss_usd,
               incident.cost_basis, self._money_first_summary(incident, diagnosis), diagnosis.ops_explanation, json.dumps(diagnosis.evidence),
               diagnosis.confidence, diagnosis.recommended_action, json.dumps(diagnosis.alternatives_ruled_out))
        with self._connect() as connection:
            connection.execute("""INSERT OR REPLACE INTO incident_reports (
                incident_id, revision, published_at, anchor_cell, baseline_rate, observed_rate, drop_pp,
                dominant_decline_code, baseline_source, onset_ts, detected_at, detection_latency_s, resolved_at, status,
                affected_entities, blast_radius, affected_attempts, burn_rate_usd_hour, cumulative_loss_usd, cost_basis,
                exec_one_liner, ops_explanation, evidence, confidence, recommended_action, alternatives_ruled_out
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        self.export_dashboard_feed()
        return revision
