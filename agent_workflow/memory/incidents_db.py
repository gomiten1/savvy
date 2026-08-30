"""Small write-through SQLite store for incident lifecycle state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent_workflow.detect.cluster import lattice_related


class IncidentRepository:
    def __init__(self, path: str | Path = "data/incidents.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    identity_cell TEXT NOT NULL,
                    current_anchor TEXT NOT NULL,
                    onset_ts TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    resolved_at TEXT,
                    burn_rate_usd_hour REAL NOT NULL,
                    blast_radius REAL NOT NULL,
                    cost_basis TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, incident) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO incidents (
                    incident_id, identity_cell, current_anchor, onset_ts, opened_at,
                    resolved_at, burn_rate_usd_hour, blast_radius, cost_basis
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    current_anchor=excluded.current_anchor,
                    resolved_at=excluded.resolved_at,
                    burn_rate_usd_hour=excluded.burn_rate_usd_hour,
                    blast_radius=excluded.blast_radius
            """, (
                incident.incident_id, json.dumps(incident.identity_cell, sort_keys=True),
                json.dumps(incident.current_anchor.cell, sort_keys=True),
                incident.onset_ts.isoformat(), incident.opened_at.isoformat(),
                incident.resolved_at.isoformat() if incident.resolved_at else None,
                incident.burn_rate_usd_hour, incident.blast_radius, incident.cost_basis,
            ))

    def search_memory(self, cell: dict[str, str | None], limit: int = 5) -> list[dict]:
        """Return only lattice-related resolved incidents, newest first.

        SQLite cannot express lattice containment over JSON portably, so keep SQL
        deterministic for retrieval and apply the small, explicit lattice predicate
        over the returned operational history.
        """
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT * FROM incidents WHERE resolved_at IS NOT NULL
                   ORDER BY resolved_at DESC"""
            ).fetchall()
        matches = []
        for row in rows:
            identity = json.loads(row["identity_cell"])
            if lattice_related(identity, cell):
                matches.append({key: row[key] for key in row.keys()} | {"identity_cell": identity})
            if len(matches) >= limit:
                break
        return matches

    def incident_windows(self, through: datetime) -> list[tuple[datetime, datetime]]:
        """Known incident intervals to keep out of a rebuilt baseline artifact."""
        with self._connect() as connection:
            rows = connection.execute("SELECT onset_ts, resolved_at FROM incidents").fetchall()
        return [
            (datetime.fromisoformat(onset), datetime.fromisoformat(resolved) if resolved else through)
            for onset, resolved in rows
        ]
