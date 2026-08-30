from __future__ import annotations

import unittest

from agent_workflow.analysis.t2 import inject_conversion_drop, run
from agent_workflow.store.mock import MockStore
from tests.test_detection_pipeline import START, attempts


class T2LatencyTests(unittest.TestCase):
    def test_injection_only_converts_approvals_to_failures(self) -> None:
        clean = MockStore(attempts("P1", 20))
        injected = inject_conversion_drop(clean, {"provider": "P1"}, START, START.replace(hour=1), 0.50)
        samples = injected.get_samples(START, START.replace(hour=1), {"provider": "P1"}, 200)
        self.assertEqual(50, sum(row.status == "approved" for row in samples))
        self.assertEqual(50, sum(row.decline_code == "injected_conversion_drop" for row in samples))

    def test_t2_replay_records_latency_for_detectable_incidents(self) -> None:
        results = run(MockStore.from_csv("data/synthetic_backfill.csv"))
        self.assertEqual(6, len(results))
        self.assertTrue(any(
            row["volume"] == "high_traffic" and row["magnitude_pp"] == 60 and row["opened"]
            for row in results
        ))
        self.assertTrue(all("latency_minutes" in row for row in results))


if __name__ == "__main__":
    unittest.main()
