from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_workflow.analysis.calibrate import CleanScore
from agent_workflow.config import Gates, load_gates, write_gates_version
from agent_workflow.recalibrate import recompute


UTC = timezone.utc


class FakeStore:
    history_start = datetime(2026, 8, 1, tzinfo=UTC)
    history_end = datetime(2026, 8, 15, tzinfo=UTC)

    def latest_live_event_ts(self):
        return None


class RecomputeTests(unittest.TestCase):
    def test_gate_artifacts_are_versioned_and_have_a_safe_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(Gates(), load_gates(directory))
            first = write_gates_version(Gates(.12, 14, 70), directory)
            second = write_gates_version(Gates(.13, 15, 80), directory)
            self.assertEqual("gates_v1.json", first.name)
            self.assertEqual("gates_v2.json", second.name)
            self.assertEqual(Gates(.13, 15, 80), load_gates(directory))

    def test_recompute_validates_then_writes_both_versioned_artifacts_and_log(self) -> None:
        artifact = {"min_history_observations": 2, "cells": {}}
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent_workflow.recalibrate.build", return_value=artifact) as build_mock, \
                patch("agent_workflow.recalibrate.collect_scores", return_value=(4, [CleanScore(.15, 10)])), \
                patch("agent_workflow.recalibrate.count_signals", return_value=(3, 0)):
            record = recompute(FakeStore(), data_dir=directory)
            path = Path(directory)
            self.assertEqual("baselines_v1.json", record["baseline"])
            self.assertEqual("gates_v1.json", record["gates"])
            self.assertEqual("baselines_v1.json", (path / "baselines_current").read_text())
            self.assertEqual("gates_v1.json", (path / "gates_current").read_text())
            self.assertEqual(2, build_mock.call_count)
            self.assertEqual(0, record["t1_signals"])
            self.assertEqual(record, json.loads((path / "recompute.log").read_text()))

    def test_a_failed_t1_never_advances_either_pointer(self) -> None:
        artifact = {"min_history_observations": 2, "cells": {}}
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent_workflow.recalibrate.build", return_value=artifact), \
                patch("agent_workflow.recalibrate.collect_scores", return_value=(4, [CleanScore(.15, 10)])), \
                patch("agent_workflow.recalibrate.count_signals", return_value=(3, 1)):
            with self.assertRaisesRegex(RuntimeError, "T1 found 1"):
                recompute(FakeStore(), data_dir=directory)
            self.assertFalse((Path(directory) / "baselines_current").exists())
            self.assertFalse((Path(directory) / "gates_current").exists())


if __name__ == "__main__":
    unittest.main()
