from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.analysis.t2 import run as run_t2
from agent_workflow.baselines.build import build
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.registry import Incident, IncidentRegistry
from agent_workflow.economics import burn_rate_usd_hour
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.store.mock import MockStore
from tests.test_detection_pipeline import RecordingPoster, START, attempts, baseline_lookup, signal


class FakeResponses:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses, self.calls = responses, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = FakeResponses(responses)


def function_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id="call-1")


def final_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(id="response-2", output=[], output_text=json.dumps(payload))


def incident_for(cell: dict[str, str | None] | None = None) -> Incident:
    anchor = signal(cell or {"provider": "P1"})
    return Incident("inc_test", dict(anchor.cell), anchor, START, START + timedelta(minutes=10), 1_920, 0.20)


class ShipReadinessTests(unittest.TestCase):
    def test_t3_opened_replays_anchor_to_true_cell_or_parent(self) -> None:
        """T3: an injected cell must not open under an unrelated identity."""
        results = run_t2(MockStore.from_csv("data/synthetic_backfill.csv"))
        for result in (row for row in results if row["opened"]):
            identity, true_cell = result["identity_cell"], result["cell"]
            self.assertTrue(
                set(identity.items()).issubset(true_cell.items()),
                f"identity {identity} is not the injected cell or its parent {true_cell}",
            )

    def test_t5_agent_honors_tool_call_budget_and_hides_internal_reason(self) -> None:
        """T5: an exhausted worker returns an operator-safe inconclusive diagnosis."""
        first = SimpleNamespace(id="response-1", output=[function_call("get_baseline", {
            "cell": {"provider": "P1"}, "at_ts": START.isoformat(),
        })], output_text="")
        client = FakeClient([first])
        runner = DiagnosisRunner(
            DiagnosisTools(MockStore(attempts("P1", 14)), baseline_lookup(({"provider": "P1"}, .9, .01))),
            client=client, max_tool_calls=0,
        )
        diagnosis = runner.investigate(incident_for())
        self.assertEqual("insufficient_evidence", diagnosis.confidence)
        self.assertEqual("budget_exhausted", diagnosis.low_confidence_reason)
        self.assertNotIn("budget", diagnosis.exec_one_liner.lower())

    def test_t6_no_baseline_is_explicitly_inconclusive(self) -> None:
        """T6: a cold-start cell never receives a fabricated baseline or diagnosis."""
        tools = DiagnosisTools(MockStore(attempts("P1", 14)), BaselineLookup({"cells": {}}))
        self.assertEqual({"baseline": None}, tools.get_baseline({"provider": "new_provider"}, START.isoformat()))

    def test_t7_memory_returns_only_related_resolved_incidents(self) -> None:
        """T7: repeat lookup is deterministic lattice search, not fuzzy matching."""
        with tempfile.TemporaryDirectory() as directory:
            repository = IncidentRepository(Path(directory) / "incidents.db")
            related = incident_for({"provider": "P1", "country": "BR"})
            related.resolved_at = START + timedelta(hours=1)
            repository.save(related)
            unrelated = incident_for({"provider": "P2"})
            unrelated.incident_id = "inc_unrelated"
            unrelated.resolved_at = START + timedelta(hours=2)
            repository.save(unrelated)
            matches = DiagnosisTools(MockStore(attempts("P1", 14)), baseline_lookup(({"provider": "P1"}, .9, .01)), repository).search_memory({"provider": "P1"})
            self.assertEqual(["inc_test"], [row["incident_id"] for row in matches["incidents"]])

    def test_t8_agent_can_query_a_three_dimension_drilldown(self) -> None:
        """T8: the diagnosis seam accepts 3-D drill-downs even though detection scans <=2-D."""
        store = MockStore(attempts("P1", 14))
        rows = DiagnosisTools(store, baseline_lookup(({"provider": "P1"}, .9, .01))).get_counts(
            START.isoformat(), (START + timedelta(minutes=25)).isoformat(),
            ["merchant", "provider", "country"], {"provider": "P1"},
        )["rows"]
        self.assertTrue(rows)
        self.assertEqual({"merchant": "M1", "provider": "P1", "country": "BR"}, rows[0]["cell"])

    def test_t9_long_incident_posts_exactly_one_root(self) -> None:
        """T9: a persistent signal does not spam root alerts."""
        parent, poster = signal({"provider": "P1"}), RecordingPoster()
        registry = IncidentRegistry(MockStore(attempts("P1", 14)), baseline_lookup(({"provider": "P1"}, .9, .01)), poster)
        for minute in range(25, 70, 5):
            registry.tick(cluster([parent]), START + timedelta(minutes=minute))
        self.assertEqual(1, len(registry.open_incidents()))
        self.assertEqual(1, len(poster.roots))

    def test_t10_burn_rate_matches_hand_calculation(self) -> None:
        """T10: 20 lost approvals/25 min * $40 each -> $1,920/hour."""
        self.assertAlmostEqual(1_920.0, burn_rate_usd_hour(signal({"provider": "P1"})))


class FreeQueryTests(unittest.TestCase):
    def _tools(self, *, gold_dir=None):
        store = MockStore(attempts("P1", 14))
        if gold_dir is not None:
            store._gold_dir = gold_dir
        return DiagnosisTools(store, baseline_lookup(({"provider": "P1"}, .9, .01)))

    def test_run_sql_unavailable_without_gold_store(self) -> None:
        result = self._tools().run_sql("history", "SELECT 1")
        self.assertIn("unavailable", result["error"])

    def test_run_sql_rejects_non_read_statements(self) -> None:
        tools = self._tools(gold_dir="/nonexistent")
        self.assertIn("single read-only", tools.run_sql("live", "DELETE FROM live_attempts")["error"])
        self.assertIn("single read-only", tools.run_sql("live", "SELECT 1; DROP TABLE live_attempts")["error"])
        self.assertIn("non-read keyword", tools.run_sql("live", "SELECT * FROM sqlite_master")["error"])
        self.assertEqual("database must be 'history' or 'live'", tools.run_sql("prod", "SELECT 1")["error"])

    def test_runner_caps_free_queries(self) -> None:
        calls = [SimpleNamespace(id=f"r{i}", output=[function_call("run_sql", {"database": "live", "sql": "SELECT 1"})],
                                 output_text="") for i in range(3)]
        calls.append(final_response({
            "root_cause": "x", "confidence": "insufficient_evidence", "evidence": [],
            "ops_explanation": "x", "exec_one_liner": "x",
        }))
        client = FakeClient(calls)
        runner = DiagnosisRunner(self._tools(), client=client, free_query_limit=2)
        runner.investigate(incident_for())
        outputs = [item["output"] for kwargs in client.responses.calls
                   for item in kwargs["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]
        self.assertTrue(any("free-query limit (2) reached" in text for text in outputs))


if __name__ == "__main__":
    unittest.main()
