"""Deterministic Slack formatting.  Agent output never formats alert messages."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Mapping
from urllib.parse import quote


DEFAULT_DASHBOARD_URL = "https://savvy.fly.dev"


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
        # `blast_radius` is the same number as the pp drop above (economics.blast_radius),
        # so printing it here labelled differently read as two independent findings.
        f"{anchor.attempts} attempts / {anchor.lost_approvals:.1f} lost approvals per window",
        f"Baseline: {anchor.baseline_source} · cost basis: {incident.cost_basis}",
    ))


def format_resolution(incident, resolved_at: datetime) -> str:
    timestamp = resolved_at.isoformat().replace("+00:00", "Z")
    return f"Incident `{incident.incident_id}` resolved at {timestamp}. Estimated cumulative loss: ${incident.cumulative_loss_usd:,.0f}."


def format_storm_summary(incidents) -> str:
    rows = [f"• {format_cell(incident.current_anchor.cell)} — ${incident.burn_rate_usd_hour:,.0f}/hr"
            for incident in incidents]
    return "\n".join((f"*{len(incidents)} further incidents open* (suppressed by alert storm cap)", *rows))


def dashboard_incident_url(incident_id: str) -> str:
    """Link an executive Slack update to the incident's dashboard report."""
    base_url = os.environ.get("DASHBOARD_URL", DEFAULT_DASHBOARD_URL).rstrip("/")
    return f"{base_url}/incident-detail.html?id={quote(incident_id, safe='')}"


def format_diagnosis(incident, diagnosis) -> str:
    """Render only validated diagnosis fields; internal telemetry stays private.

    The exec one-liner leads the thread because the root message cannot carry it -- the
    root posts on open, before the agent has run (D29).  Its money line is read off the
    incident, never off the model, so it can never disagree with the root message (D48).
    """
    onset = incident.onset_ts.isoformat().replace("+00:00", "Z")
    evidence = diagnosis.evidence or [{"claim": "No conclusive evidence", "support": "Further investigation required."}]
    evidence_lines = [f"• {item['claim']} — {item['support']}" for item in evidence]
    alternatives = diagnosis.alternatives_ruled_out or ["No alternative was conclusively ruled out."]
    lines = [
        f"*{diagnosis.exec_one_liner}*",
        f"${incident.burn_rate_usd_hour:,.0f}/hr · ${incident.cumulative_loss_usd:,.0f} lost since "
        f"{onset} · cost basis {incident.cost_basis}",
        f"*Diagnosis: {diagnosis.confidence.replace('_', ' ')}* — {diagnosis.root_cause}",
        diagnosis.ops_explanation,
        "*Evidence*", *evidence_lines,
        "*Alternatives checked*", *(f"• {item}" for item in alternatives),
        f"*Recommended action:* {diagnosis.recommended_action}",
    ]
    if diagnosis.next_step:
        lines.append(f"*Next step:* {diagnosis.next_step}")
    lines.append(f"*Dashboard:* <{dashboard_incident_url(incident.incident_id)}|Open incident report>")
    return "\n".join(lines)
