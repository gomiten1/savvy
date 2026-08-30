import unittest

from datetime import datetime, timedelta, timezone

from agent_workflow.analysis.calibrate import CleanScore, frontier, solve, validate_intervals


class CalibrationTests(unittest.TestCase):
    def test_solver_uses_first_drop_floor_with_a_zero_signal_gate_under_cap(self) -> None:
        scores = [CleanScore(.14, 28.77), CleanScore(.15, 16.84), CleanScore(.25, 11.62)]
        gates = solve(scores, max_z=20)
        self.assertEqual(.15, gates.min_abs_drop_pp)
        self.assertEqual(17.0, gates.z_threshold)

    def test_frontier_drops_scores_below_the_floor(self) -> None:
        rows = dict(frontier([CleanScore(.08, 28.77), CleanScore(.15, 16.84)], drop_step_pp=1))
        self.assertEqual(28.77, rows[8])
        self.assertEqual(16.84, rows[15])
        self.assertEqual(0.0, rows[16])

    def test_integer_clean_z_is_rounded_strictly_above_the_detector_boundary(self) -> None:
        gates = solve([CleanScore(.15, 17.0)], max_z=20)
        self.assertEqual(18.0, gates.z_threshold)

    def test_selected_gates_leave_no_clean_score_that_the_detector_would_fire(self) -> None:
        scores = [CleanScore(.14, 28.77), CleanScore(.15, 16.84), CleanScore(.25, 11.62)]
        gates = solve(scores, max_z=20)
        fired = [score for score in scores if score.drop >= gates.min_abs_drop_pp and score.z_score >= gates.z_threshold]
        self.assertEqual([], fired)

    def test_calibration_rejects_overlapping_fit_and_holdout_periods(self) -> None:
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "evaluation"):
            validate_intervals(start, start + timedelta(days=2), start + timedelta(days=1), start + timedelta(days=3))


if __name__ == "__main__":
    unittest.main()
