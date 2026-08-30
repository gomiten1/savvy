"""
Tests de recovery tracking: RecoveryTracker (grain evento individual, para
el stream en vivo) y las funciones puras de deviation/confidence (usadas
también por el post-procesamiento del Generador A).
"""
import unittest

from pipeline.silver.recovery import (
    RecoveryTracker,
    recovery_deviation,
    recovery_confidence,
    expected_recovery_rate,
)
from pipeline.generator.weights import RECOVERY_RATE_BY_CANONICAL_CODE, MIN_SAMPLE_SIZE_PER_CELL


class RecoveryDeviationTest(unittest.TestCase):
    def test_matches_expected_exactly_when_observed_equals_expected(self):
        code = "51_insufficient_funds"
        expected = RECOVERY_RATE_BY_CANONICAL_CODE[code]
        self.assertEqual(recovery_deviation(expected, code), 0.0)

    def test_negative_when_observed_below_expected(self):
        code = "05_do_not_honor"
        expected = RECOVERY_RATE_BY_CANONICAL_CODE[code]
        dev = recovery_deviation(expected - 0.12, code)
        self.assertAlmostEqual(dev, -0.12, places=4)

    def test_none_when_code_unknown(self):
        self.assertIsNone(recovery_deviation(0.5, "not_a_real_code"))

    def test_none_when_observed_none(self):
        self.assertIsNone(recovery_deviation(None, "51_insufficient_funds"))


class RecoveryConfidenceTest(unittest.TestCase):
    def test_below_threshold_is_insufficient_sample(self):
        self.assertEqual(recovery_confidence(MIN_SAMPLE_SIZE_PER_CELL - 1), "insufficient_sample")

    def test_at_or_above_threshold_is_reliable(self):
        self.assertEqual(recovery_confidence(MIN_SAMPLE_SIZE_PER_CELL), "reliable")
        self.assertEqual(recovery_confidence(MIN_SAMPLE_SIZE_PER_CELL + 1000), "reliable")


class RecoveryTrackerTest(unittest.TestCase):
    def setUp(self):
        self.tracker = RecoveryTracker()
        self.cell = ("stripe", "MX", "card", "unknown_bank")
        self.code = "51_insufficient_funds"

    def test_no_retry_no_recovery(self):
        self.tracker.record(
            linked_order_id="ord_1", attempt_number=1, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["attempts"], 1)
        self.assertEqual(snap["approvals"], 0)
        self.assertEqual(snap["recovery_rate"], 0.0)

    def test_retry_then_approved_counts_as_recovered(self):
        self.tracker.record(
            linked_order_id="ord_1", attempt_number=1, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        self.tracker.record(
            linked_order_id="ord_1", attempt_number=2, status="approved",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["attempts"], 1)
        self.assertEqual(snap["approvals"], 1)
        self.assertEqual(snap["recovery_rate"], 1.0)
        self.assertAlmostEqual(snap["recovery_rate_deviation"], 1.0 - expected_recovery_rate(self.code), places=4)

    def test_exhausted_retries_not_recovered(self):
        self.tracker.record(
            linked_order_id="ord_2", attempt_number=1, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        self.tracker.record(
            linked_order_id="ord_2", attempt_number=2, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        self.tracker.record(
            linked_order_id="ord_2", attempt_number=3, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["attempts"], 1)  # una sola "oportunidad" (una orden)
        self.assertEqual(snap["approvals"], 0)

    def test_unrelated_order_ids_are_independent(self):
        for i in range(5):
            self.tracker.record(
                linked_order_id=f"ord_{i}", attempt_number=1, status="declined",
                cell_key=self.cell, canonical_decline_code=self.code,
            )
        for i in range(3):
            self.tracker.record(
                linked_order_id=f"ord_{i}", attempt_number=2, status="approved",
                cell_key=self.cell, canonical_decline_code=self.code,
            )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["attempts"], 5)
        self.assertEqual(snap["approvals"], 3)
        self.assertAlmostEqual(snap["recovery_rate"], 0.6, places=4)

    def test_snapshot_confidence_reflects_sample_size(self):
        self.tracker.record(
            linked_order_id="ord_1", attempt_number=1, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["confidence"], "insufficient_sample")

    def test_no_linked_order_id_is_ignored_not_crash(self):
        self.tracker.record(
            linked_order_id=None, attempt_number=1, status="declined",
            cell_key=self.cell, canonical_decline_code=self.code,
        )
        snap = self.tracker.snapshot(self.cell, self.code)
        self.assertEqual(snap["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
