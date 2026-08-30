from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_workflow.agent.schema import Diagnosis
from agent_workflow.detect.registry import Incident
from agent_workflow.detect.scan import Signal
from agent_workflow.reporting.publish import ReportPublisher


CONTRACT_FIELDS = {
    "incident_id", "revision", "published_at",
    "anchor_cell", "metric", "baseline_rate", "observed_rate", "drop_pp",
    "dominant_decline_code", "baseline_source",
    "onset_ts", "detected_at", "detection_latency_s", "resolved_at", "status",
    "affected_entities", "blast_radius", "affected_attempts",
    "burn_rate_usd_hour", "cumulative_loss_usd", "cost_basis",
    "exec_one_liner", "ops_explanation", "evidence", "confidence",
    "recommended_action", "alternatives_ruled_out",
}


class ReportingExportTests(unittest.TestCase):
    def test_publish_exports_contract_ready_json_and_keeps_revision_history(self) -> None:
        onset = datetime(2026, 8, 29, 14, 3, tzinfo=timezone.utc)
        opened = onset + timedelta(minutes=3, seconds=10)
        anchor = Signal(
            cell={"provider": "P2", "country": "BR"},
            observed_rate=0.19,
            baseline_rate=0.87,
            baseline_source="hour_of_week",
            z_score=10.0,
            lost_approvals=280.0,
            attempts=412,
            approved=78,
            error_share=0.81,
            decline_code_mix={"provider_timeout": 0.81, "do_not_honor": 0.19},
            amount_usd_total=40_000.0,
        )
        incident = Incident(
            incident_id="inc_dashboard_export",
            identity_cell=dict(anchor.cell),
            current_anchor=anchor,
            onset_ts=onset,
            opened_at=opened,
            burn_rate_usd_hour=4_210.0,
            blast_radius=0.78,
            last_evaluated_at=opened + timedelta(minutes=4),
        )
        diagnosis = Diagnosis(
            root_cause="Provider P2 timeouts in Brazil",
            confidence="high",
            evidence=[{"claim": "P2 is degraded in BR", "support": "Timeouts rose to 81%"}],
            alternatives_ruled_out=["Brazil-wide problem", "Issuer-bank outage"],
            recommended_action="Route Brazilian traffic away from P2.",
            ops_explanation="Provider P2 began timing out on Brazilian traffic.",
            exec_one_liner="Brazil traffic is losing $4.2K/hour through Provider P2.",
        )

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "reports.db"
            publisher = ReportPublisher(db_path)

            self.assertEqual(1, publisher.publish(incident, diagnosis, published_at=opened))
            self.assertEqual(2, publisher.publish(
                incident,
                diagnosis,
                published_at=opened + timedelta(minutes=1),
            ))

            export_path = db_path.with_name("dashboard-reports.json")
            self.assertTrue(db_path.is_file())
            self.assertTrue(export_path.is_file())
            payload = json.loads(export_path.read_text(encoding="utf-8"))

        self.assertEqual(2, payload["schema_version"])
        self.assertIsInstance(payload["generated_at"], str)
        datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
        self.assertIsInstance(payload["reports"], list)
        self.assertEqual(2, len(payload["reports"]))
        self.assertEqual([1, 2], sorted(report["revision"] for report in payload["reports"]))

        for report in payload["reports"]:
            self.assertEqual(CONTRACT_FIELDS, set(report))
            self.assertEqual("inc_dashboard_export", report["incident_id"])
            self.assertEqual(anchor.cell, report["anchor_cell"])
            self.assertIsInstance(report["anchor_cell"], dict)
            self.assertIsInstance(report["affected_entities"], list)
            self.assertIsInstance(report["evidence"], list)
            self.assertIsInstance(report["alternatives_ruled_out"], list)
            self.assertAlmostEqual(
                1.0,
                sum(entity["share_of_impact"] for entity in report["affected_entities"]),
            )


if __name__ == "__main__":
    unittest.main()
