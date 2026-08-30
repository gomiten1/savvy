"""Local MVP process: detector loop plus bounded diagnosis worker thread."""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, WINDOW_BUCKETS, load_gates
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.injections import active_controlled_filters, collapse_controlled_projections
from agent_workflow.detect.registry import IncidentRegistry
from agent_workflow.detect.scan import scan
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.reporting.publish import ReportPublisher
from agent_workflow.slack.post import poster_from_env
from agent_workflow.slack.templates import format_diagnosis
from agent_workflow.store.mock import MockStore


ACTIVE_INJECTIONS_FILE = Path("data/live/active_injections.json")


class Workflow:
    def __init__(self, store: MockStore, baselines: BaselineLookup, *, data_dir: str = "data",
                 recompute_hour: int = 0, freeze_gates: bool = False) -> None:
        self.store, self.data_dir = store, data_dir
        self.baselines, self.gates = baselines, load_gates(data_dir)
        self.recompute_hour, self.freeze_gates = recompute_hour, freeze_gates
        self._artifact_signature = self._pointer_signature()
        self._last_recompute_day = None
        self.poster = poster_from_env()
        print(f"[slack] transport={type(self.poster).__name__}")
        self.memory = IncidentRepository(f"{data_dir}/incidents.db")
        self.publisher = ReportPublisher(f"{data_dir}/reports.db")
        self.jobs: queue.Queue = queue.Queue()
        self.registry = IncidentRegistry(store, baselines, self.poster, repository=self.memory,
                                         on_open=self._enqueue, on_material_change=self._enqueue,
                                         on_resolve=self._publish_resolution)
        self.runner = DiagnosisRunner(DiagnosisTools(store, baselines, self.memory))
        self._diagnoses = {}
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="diagnosis-worker")

    def _pointer_signature(self):
        result = []
        for name in ("baselines_current", "gates_current"):
            path = Path(self.data_dir) / name
            try:
                result.append((name, path.stat().st_mtime_ns, path.read_text().strip()))
            except FileNotFoundError:
                result.append((name, None, None))
        return tuple(result)

    def reload_artifacts(self) -> bool:
        """Adopt a complete new baseline/gates pair between detector ticks."""
        signature = self._pointer_signature()
        if signature == self._artifact_signature:
            return False
        baselines = BaselineLookup.from_data_dir(self.data_dir)
        gates = load_gates(self.data_dir)
        self.baselines, self.gates = baselines, gates
        self.registry.baselines = baselines
        self.runner.tools.baselines = baselines
        self._artifact_signature = signature
        print(f"[recompute] reloaded baseline + gates artifacts")
        return True

    def maybe_schedule_recompute(self, tick: datetime) -> None:
        if not self.recompute_hour or tick.hour != self.recompute_hour or tick.date() == self._last_recompute_day:
            return
        self._last_recompute_day = tick.date()

        def run() -> None:
            from agent_workflow.recalibrate import recompute
            try:
                record = recompute(self.store, data_dir=self.data_dir, through=tick,
                                   freeze_gates=self.freeze_gates)
                print(f"[recompute] completed {record['baseline']} / {record['gates']}")
            except Exception as error:  # Keep the live detector running on a failed candidate.
                print(f"[recompute] aborted: {error}")

        threading.Thread(target=run, daemon=True, name="nightly-recompute").start()

    def start(self) -> None:
        self._worker.start()

    def stop(self) -> None:
        self.jobs.put(None)
        self._worker.join(timeout=2)

    def _enqueue(self, incident) -> None:
        self.jobs.put(incident)

    def _run_worker(self) -> None:
        while (incident := self.jobs.get()) is not None:
            # One failing incident must never kill the worker: every later incident
            # would then get a root alert and no diagnosis for the life of the process.
            try:
                self._diagnose(incident)
            except Exception as error:
                print(f"[worker] {incident.incident_id} diagnosis failed: "
                      f"{type(error).__name__}: {error}", file=sys.stderr)

    def _diagnose(self, incident) -> None:
        diagnosis = self.runner.investigate(incident)
        self._diagnoses[incident.incident_id] = diagnosis
        if incident.root_message_id != "suppressed":
            text = format_diagnosis(incident, diagnosis)
            if incident.root_message_id:
                self.poster.post_thread(incident.root_message_id, text)
            else:
                # No threadable parent (webhook/console transport, or a failed root
                # post). Still deliver the diagnosis instead of silently dropping it.
                self.poster.post_root(text)
        # Use simulated detector time so D66's cooldown is meaningful during
        # accelerated local replays.
        self.registry.mark_diagnosed(incident, incident.last_evaluated_at or incident.opened_at)
        # Reporting is explicitly after the user-facing thread reply. The
        # detector can resolve an incident while its asynchronous LLM diagnosis
        # is running; preserve the lifecycle contract by recording the open
        # diagnosis before the later resolved revision.
        if incident.status == "resolved":
            open_revision = replace(incident, status="open", resolved_at=None)
            self.publisher.publish(open_revision, diagnosis)
        self.publisher.publish(incident, diagnosis)

    def _publish_resolution(self, incident) -> None:
        diagnosis = self._diagnoses.get(incident.incident_id)
        if diagnosis:
            self.publisher.publish(incident, diagnosis)


def _floor_bucket(ts: datetime) -> datetime:
    epoch = (int(ts.timestamp()) // BUCKET_SECONDS) * BUCKET_SECONDS
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def run_csv_replay(args) -> None:
    store = MockStore.from_csv(args.input)
    start = store._attempts[0].event_ts.replace(second=0, microsecond=0)
    end = store._attempts[-1].event_ts + timedelta(seconds=BUCKET_SECONDS)
    memory = IncidentRepository(f"{args.data_dir}/incidents.db")
    artifact = build(store, start, end, excluded_intervals=memory.incident_windows(end))
    write_version(artifact, args.data_dir)
    workflow = Workflow(store, BaselineLookup.from_data_dir(args.data_dir), data_dir=args.data_dir)
    workflow.start()
    try:
        tick = start + timedelta(seconds=BUCKET_SECONDS * WINDOW_BUCKETS)
        while tick <= end:
            workflow.reload_artifacts()
            signals = scan(store, workflow.registry.baselines, tick, gates=workflow.gates)
            workflow.registry.tick(cluster(signals), tick)
            print(f"{tick.isoformat()} · {len(signals)} signals · {len(workflow.registry.open_incidents())} open incidents")
            tick += timedelta(seconds=BUCKET_SECONDS)
    finally:
        workflow.stop()


def run_live(args) -> None:
    """Drive the detector off MAX(event_ts) in live_attempts, not wall-clock (D74).

    Baselines are read from data-dir as-is: they were built and calibrated against
    clean history by T1 before the live stream started.  This never rebuilds them.
    """
    from agent_workflow.store.gold import GoldStore
    from scripts.runtime_status import update_status

    store = GoldStore()
    workflow = Workflow(store, BaselineLookup.from_data_dir(args.data_dir), data_dir=args.data_dir,
                        recompute_hour=args.recompute_hour, freeze_gates=args.freeze_gates)
    workflow.start()
    poll = max(0.2, args.poll_seconds)
    deadline = None if not args.duration else time.time() + args.duration
    tick = None
    first_successful_scan = True
    try:
        print(f"[live] waiting for live_attempts; history_end={store.history_end.isoformat()}")
        while deadline is None or time.time() < deadline:
            latest = store.latest_live_event_ts()
            if latest is None:
                time.sleep(poll)
                continue
            if tick is None:
                # First scan fires once one full detection window of sim time exists.
                tick = _floor_bucket(latest)
                print(f"[live] stream up at {latest.isoformat()}; first tick at {tick.isoformat()}")
            advanced = False
            while latest - tick >= timedelta(seconds=BUCKET_SECONDS):
                tick = tick + timedelta(seconds=BUCKET_SECONDS)
                workflow.reload_artifacts()
                workflow.maybe_schedule_recompute(tick)
                signals = scan(store, workflow.registry.baselines, tick, gates=workflow.gates)
                signals = collapse_controlled_projections(
                    signals, active_controlled_filters(ACTIVE_INJECTIONS_FILE, tick)
                )
                workflow.registry.tick(cluster(signals), tick)
                update_status(detector_last_scan=time.time())
                if first_successful_scan:
                    print(f"[detector] first successful scan at {tick.isoformat()}")
                    first_successful_scan = False
                print(f"{tick.isoformat()} · MAX(event_ts)={latest.isoformat()} · {len(signals)} signals · "
                      f"{len(workflow.registry.open_incidents())} open incidents")
                advanced = True
            if not advanced:
                time.sleep(poll)
    except KeyboardInterrupt:
        print("\n[live] stopped")
    finally:
        workflow.stop()


def _load_dotenv(path: str = ".env") -> None:
    """Populate env from a local .env so a bare `python -m agent_workflow.main`
    picks up OPENAI_API_KEY / SLACK_* without a systemd EnvironmentFile.
    Real environment variables always win (setdefault)."""
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Run the local PagoTotal monitor.")
    parser.add_argument("--live", action="store_true",
                        help="drive ticks off live_attempts MAX(event_ts) via GoldStore (D74)")
    parser.add_argument("--input", default="data/synthetic_backfill.csv", help="CSV replay source (offline mode)")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--duration", type=float, default=0.0, help="live mode: real seconds, 0 = until Ctrl+C")
    parser.add_argument("--poll-seconds", type=float, default=1.5, help="live mode: MAX(event_ts) poll interval")
    parser.add_argument("--recompute-hour", type=int, choices=range(24), default=0,
                        metavar="HOUR", help="simulated UTC hour to run nightly recompute; 0 disables it")
    parser.add_argument("--freeze-gates", action="store_true", help="rebuild the baseline but retain current gates")
    args = parser.parse_args()
    if args.live:
        run_live(args)
    else:
        run_csv_replay(args)


if __name__ == "__main__":
    main()
