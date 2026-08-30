"""Local MVP process: detector loop plus bounded diagnosis worker thread."""

from __future__ import annotations

import argparse
import queue
import threading
from datetime import timedelta

from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.baselines.build import build, write_version
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import BUCKET_SECONDS
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.registry import IncidentRegistry
from agent_workflow.detect.scan import scan
from agent_workflow.memory.incidents_db import IncidentRepository
from agent_workflow.reporting.publish import ReportPublisher
from agent_workflow.slack.post import ConsolePoster
from agent_workflow.slack.templates import format_diagnosis
from agent_workflow.store.mock import MockStore


class Workflow:
    def __init__(self, store: MockStore, baselines: BaselineLookup, *, data_dir: str = "data") -> None:
        self.poster = ConsolePoster()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local PagoTotal monitor over a CSV backfill.")
    parser.add_argument("--input", default="data/synthetic_backfill.csv")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    store = MockStore.from_csv(args.input)
    start = store._attempts[0].event_ts.replace(second=0, microsecond=0)
    end = store._attempts[-1].event_ts + timedelta(seconds=BUCKET_SECONDS)
    memory = IncidentRepository(f"{args.data_dir}/incidents.db")
    artifact = build(store, start, end, excluded_intervals=memory.incident_windows(end))
    write_version(artifact, args.data_dir)
    workflow = Workflow(store, BaselineLookup.from_data_dir(args.data_dir), data_dir=args.data_dir)
    workflow.start()
    try:
        tick = start + timedelta(seconds=BUCKET_SECONDS * 5)
        while tick <= end:
            signals = scan(store, workflow.registry.baselines, tick)
            changes = workflow.registry.tick(cluster(signals), tick)
            print(f"{tick.isoformat()} · {len(signals)} signals · {len(workflow.registry.open_incidents())} open incidents")
            tick += timedelta(seconds=BUCKET_SECONDS)
    finally:
        workflow.stop()


if __name__ == "__main__":
    main()
