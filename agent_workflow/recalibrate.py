"""Nightly, safe baseline and detector-gate recomputation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_workflow.analysis.calibrate import collect_scores, solve
from agent_workflow.analysis.t1 import count_signals
from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import Gates, load_gates, write_gates_version
from agent_workflow.memory.incidents_db import IncidentRepository


def _artifact_version(path: Path) -> str:
    return path.name


def _current_artifact(data_dir: str, pointer: str) -> Path | None:
    path = Path(data_dir) / pointer
    return Path(data_dir) / path.read_text().strip() if path.exists() else None


def _changed_enough(current: Gates, candidate: Gates, band: float) -> bool:
    return any(abs(new - old) / max(abs(old), 1e-9) > band for old, new in (
        (current.min_abs_drop_pp, candidate.min_abs_drop_pp),
        (current.z_threshold, candidate.z_threshold),
    ))


def recompute(store, *, data_dir: str = "data", through: datetime | None = None,
              history_start: datetime | None = None, window_days: int = 28,
              freeze_gates: bool = False, hysteresis: float = 0.15) -> dict:
    """Validate candidates before advancing either artifact pointer.

    The final artifact is fitted over the whole clean trailing window.  Gates are
    selected against its last 20% held out from the calibration fit, preventing a
    nightly run from merely fitting its own noise.
    """
    history_start = history_start or store.history_start
    latest = getattr(store, "latest_live_event_ts", lambda: None)()
    through = through or max(store.history_end, latest or store.history_end)
    start = max(history_start, through - timedelta(days=window_days))
    if through - start < timedelta(days=2):
        raise ValueError("need at least two days of clean traffic to recompute")

    memory = IncidentRepository(Path(data_dir) / "incidents.db")
    excluded = memory.incident_windows(through)
    # Reserve a meaningful clean holdout while preserving enough training history.
    holdout = max(timedelta(days=1), (through - start) / 5)
    fit_end = through - holdout
    training_artifact = build(store, start, fit_end, excluded_intervals=excluded)
    scans, scores = collect_scores(store, BaselineLookup(training_artifact), fit_end, through,
                                   excluded_intervals=excluded)
    candidate = solve(scores)

    current = load_gates(data_dir)
    existing_gates = _current_artifact(data_dir, "gates_current")
    active = (candidate if existing_gates is None else current if freeze_gates or
              not _changed_enough(current, candidate, hysteresis) else candidate)
    artifact = build(store, start, through, excluded_intervals=excluded)
    check_scans, signals = count_signals(store, BaselineLookup(artifact), fit_end, through, active)
    if signals:
        raise RuntimeError(f"recompute aborted: T1 found {signals} clean-history signals")

    # The two pointers are each atomically replaced only after both candidates pass.
    baseline_path = write_version(artifact, data_dir)
    gates_path = existing_gates if active == current and existing_gates else write_gates_version(active, data_dir)
    # A rising clean z is a cheap stale-baseline warning, not another gate solver.
    drift_start = max(fit_end, through - timedelta(days=1))
    _, drift_scores = collect_scores(store, BaselineLookup(artifact), drift_start, through,
                                     excluded_intervals=excluded)
    record = {
        "at": datetime.now(timezone.utc).isoformat(), "baseline": _artifact_version(baseline_path),
        "gates": _artifact_version(gates_path), "start": start.isoformat(), "end": through.isoformat(),
        "excluded_intervals": len(excluded), "calibration_scans": scans, "t1_scans": check_scans,
        "t1_signals": signals, "candidate_gates": candidate.__dict__, "active_gates": active.__dict__,
        "freeze_gates": freeze_gates, "max_clean_z_last_day": max((score.z_score for score in drift_scores), default=0.0),
    }
    log_path = Path(data_dir) / "recompute.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as log:
        log.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely rebuild trailing baseline and detector gates.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--window-days", type=int, default=28)
    parser.add_argument("--freeze-gates", action="store_true")
    args = parser.parse_args()
    from agent_workflow.store.gold import GoldStore
    record = recompute(GoldStore(), data_dir=args.data_dir, window_days=args.window_days,
                       freeze_gates=args.freeze_gates)
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
