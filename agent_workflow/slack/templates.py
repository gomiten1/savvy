"""Deterministic Slack formatting.  Agent output never formats alert messages."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping


def format_cell(cell: Mapping[str, str | None]) -> str:
    return " · ".join(f"{key}={value if value is not None else 'null'}" for key, value in cell.items())


def format_root_alert(incident) -> str:
    anchor = incident.current_anchor
    onset = incident.onset_ts.isoformat().replace("+00:00", "Z")
    detected = incident.opened_at.isoformat().replace("+00:00", "Z")
    return "\n".join((
        f"*${incident.burn_rate_usd_hour:,.0f}/hr at risk — {format_cell(anchor.cell)} since {onset}.*",
        f"Incident `{incident.incident_id}` · detected {detected}",
        f"Conversion {anchor.baseline_rate:.1%} → {anchor.observed_rate:.1%} "
        f"({(anchor.baseline_rate - anchor.observed_rate) * 100:.1f}pp drop)",
        f"Blast radius {incident.blast_radius:.1%} · {anchor.attempts} attempts / {anchor.lost_approvals:.1f} lost approvals per window",
        f"Baseline: {anchor.baseline_source} · cost basis: {incident.cost_basis}",
    ))


def format_resolution(incident, resolved_at: datetime) -> str:
    timestamp = resolved_at.isoformat().replace("+00:00", "Z")
    return f"Incident `{incident.incident_id}` resolved at {timestamp}. Estimated cumulative loss: ${incident.cumulative_loss_usd:,.0f}."


def format_storm_summary(incidents) -> str:
    rows = [f"• {format_cell(incident.current_anchor.cell)} — ${incident.burn_rate_usd_hour:,.0f}/hr"
            for incident in incidents]
    return "\n".join((f"*{len(incidents)} further incidents open* (suppressed by alert storm cap)", *rows))


def format_diagnosis(incident, diagnosis) -> str:
    """Render only validated diagnosis fields; internal telemetry stays private."""
    evidence = diagnosis.evidence or [{"claim": "No conclusive evidence", "support": "Further investigation required."}]
    evidence_lines = [f"• {item['claim']} — {item['support']}" for item in evidence]
    alternatives = diagnosis.alternatives_ruled_out or ["No alternative was conclusively ruled out."]
    return "\n".join((
        f"*Diagnosis: {diagnosis.confidence.replace('_', ' ')}* — {diagnosis.root_cause}",
        diagnosis.ops_explanation,
        "*Evidence*", *evidence_lines,
        "*Alternatives checked*", *(f"• {item}" for item in alternatives),
        f"*Recommended action:* {diagnosis.recommended_action}",
    ))
