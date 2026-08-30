"""Local MVP process: detector loop plus bounded diagnosis worker thread."""

from __future__ import annotations

import argparse
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS, WINDOW_BUCKETS
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.registry import IncidentRegistry
from agent_workflow.detect.scan import scan
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.reporting.publish import ReportPublisher
from agent_workflow.slack.post import poster_from_env
from agent_workflow.slack.templates import format_diagnosis
from agent_workflow.store.mock import MockStore


class Workflow:
    def __init__(self, store: MockStore, baselines: BaselineLookup, *, data_dir: str = "data") -> None:
        self.poster = poster_from_env()
        self.memory = IncidentRepository(f"{data_dir}/incidents.db")
        self.publisher = ReportPublisher(f"{data_dir}/reports.db")
        self.jobs: queue.Queue = queue.Queue()
        self.registry = IncidentRegistry(store, baselines, self.poster, repository=self.memory,
                                         on_open=self._enqueue, on_material_change=self._enqueue,
                                         on_resolve=self._publish_resolution)
        self.runner = DiagnosisRunner(DiagnosisTools(store, baselines, self.memory))
        self._diagnoses = {}
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="diagnosis-worker")

    def start(self) -> None:
        self._worker.start()

    def stop(self) -> None:
        self.jobs.put(None)
        self._worker.join(timeout=2)

    def _enqueue(self, incident) -> None:
        self.jobs.put(incident)

    def _run_worker(self) -> None:
        while (incident := self.jobs.get()) is not None:
            diagnosis = self.runner.investigate(incident)
            self._diagnoses[incident.incident_id] = diagnosis
            if incident.root_message_id not in (None, "suppressed"):
                self.poster.post_thread(incident.root_message_id, format_diagnosis(incident, diagnosis))
            # Use simulated detector time so D66's cooldown is meaningful during
            # accelerated local replays.
            self.registry.mark_diagnosed(incident, incident.last_evaluated_at or incident.opened_at)
            # Reporting is explicitly after the user-facing thread reply.
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
            signals = scan(store, workflow.registry.baselines, tick)
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

    store = GoldStore()
    workflow = Workflow(store, BaselineLookup.from_data_dir(args.data_dir), data_dir=args.data_dir)
    workflow.start()
    poll = max(0.2, args.poll_seconds)
    deadline = None if not args.duration else time.time() + args.duration
    tick = None
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
                signals = scan(store, workflow.registry.baselines, tick)
                workflow.registry.tick(cluster(signals), tick)
                print(f"{tick.isoformat()} · MAX(event_ts)={latest.isoformat()} · {len(signals)} signals · "
                      f"{len(workflow.registry.open_incidents())} open incidents")
                advanced = True
            if not advanced:
                time.sleep(poll)
    except KeyboardInterrupt:
        print("\n[live] stopped")
    finally:
        workflow.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local PagoTotal monitor.")
    parser.add_argument("--live", action="store_true",
                        help="drive ticks off live_attempts MAX(event_ts) via GoldStore (D74)")
    parser.add_argument("--input", default="data/synthetic_backfill.csv", help="CSV replay source (offline mode)")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--duration", type=float, default=0.0, help="live mode: real seconds, 0 = until Ctrl+C")
    parser.add_argument("--poll-seconds", type=float, default=1.5, help="live mode: MAX(event_ts) poll interval")
    args = parser.parse_args()
    if args.live:
        run_live(args)
    else:
        run_csv_replay(args)


if __name__ == "__main__":
    main()
