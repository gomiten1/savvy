"""Debounced incident lifecycle with pinned identities and deterministic alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, TYPE_CHECKING

from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.config import (AGENT_RERUN_COOLDOWN_MINUTES, BUCKET_SECONDS, DEBOUNCE_TICKS,
                                   MAX_AGENT_RUNS_PER_INCIDENT, MAX_CONCURRENT_ALERTS, RESOLVE_MISS_TICKS)
from agent_workflow.detect.cluster import Cluster, canonical_cell, lattice_related
from agent_workflow.detect.scan import Signal
from agent_workflow.economics import COST_BASIS, blast_radius, burn_rate_usd_hour
from agent_workflow.slack.post import AlertPoster
from agent_workflow.slack.templates import format_resolution, format_root_alert, format_storm_summary
from agent_workflow.store.interface import AttemptStore

if TYPE_CHECKING:
    from agent_workflow.memory.incidents_db import IncidentRepository


@dataclass
class Incident:
    incident_id: str
    identity_cell: dict[str, str | None]
    current_anchor: Signal
    onset_ts: datetime
    opened_at: datetime
    burn_rate_usd_hour: float
    blast_radius: float
    cost_basis: str = COST_BASIS
    root_message_id: str | None = None
    miss_streak: int = 0
    resolved_at: datetime | None = None
    agent_runs: int = 0
    last_agent_run_at: datetime | None = None
    last_diagnosis_burn_rate: float | None = None
    last_diagnosis_anchor_cell: dict[str, str | None] | None = None
    # Detector time, rather than host wall time.  The MVP replays historical data at
    # arbitrary speed, so wall time would make its loss figure nonsensical.
    last_evaluated_at: datetime | None = None

    @property
    def status(self) -> str:
        return "resolved" if self.resolved_at else "open"

    @property
    def cumulative_loss_usd(self) -> float:
        end = self.resolved_at or self.last_evaluated_at or self.opened_at
        return max(0.0, self.burn_rate_usd_hour * (end - self.onset_ts).total_seconds() / 3600)


class IncidentRegistry:
    def __init__(self, store: AttemptStore, baselines: BaselineLookup, poster: AlertPoster | None = None,
                 *, debounce_ticks: int = DEBOUNCE_TICKS, resolve_miss_ticks: int = RESOLVE_MISS_TICKS,
                 on_open: Callable[[Incident], None] | None = None,
                 on_material_change: Callable[[Incident], None] | None = None,
                 on_resolve: Callable[[Incident], None] | None = None,
                 repository: "IncidentRepository | None" = None) -> None:
        self.store, self.baselines, self.poster = store, baselines, poster
        self.debounce_ticks, self.resolve_miss_ticks = debounce_ticks, resolve_miss_ticks
        self.on_open, self.on_material_change, self.on_resolve = on_open, on_material_change, on_resolve
        self.repository = repository
        self.incidents: dict[str, Incident] = {}
        self._pending: dict[tuple[tuple[str, str | None], ...], int] = {}
        self._sequence = 0
        self._storm_summary_posted = False

    def open_incidents(self) -> list[Incident]:
        return [incident for incident in self.incidents.values() if incident.status == "open"]

    def tick(self, clusters: Iterable[Cluster], now: datetime) -> list[Incident]:
        """Apply one detector tick and return incidents opened or resolved this tick."""
        now = now.astimezone(timezone.utc)
        changed: list[Incident] = []
        matched: set[str] = set()
        for incident in self.open_incidents():
            incident.last_evaluated_at = now
        for candidate in clusters:
            related = self._matches(candidate)
            if related:
                # Every lattice-related open incident counts as seen this tick, not just
                # the one that absorbs the update.  Otherwise a cluster related to two
                # open incidents starves the loser into a false resolve.
                matched.update(incident.incident_id for incident in related)
                incident = related[0]
                incident.current_anchor = candidate.anchor
                incident.burn_rate_usd_hour = burn_rate_usd_hour(candidate.anchor)
                incident.blast_radius = blast_radius(candidate.anchor)
                incident.miss_streak = 0
                if self._is_material_change(incident, now) and self.on_material_change:
                    self.on_material_change(incident)
                continue
            signature = canonical_cell(candidate.anchor.cell)
            self._pending[signature] = self._pending.get(signature, 0) + 1
            if self._pending[signature] < self.debounce_ticks:
                continue
            incident = self._open(candidate, now)
            matched.add(incident.incident_id)
            changed.append(incident)
            self._pending.pop(signature, None)
        active_signatures = {canonical_cell(candidate.anchor.cell) for candidate in clusters}
        for signature in list(self._pending):
            if signature not in active_signatures:
                self._pending.pop(signature, None)
        self._post_new_alerts(changed)
        for incident in self.open_incidents():
            if incident.incident_id in matched:
                continue
            incident.miss_streak += 1
            if incident.miss_streak >= self.resolve_miss_ticks:
                incident.resolved_at = now
                if self.repository:
                    self.repository.save(incident)
                if self.poster and incident.root_message_id not in (None, "suppressed"):
                    self.poster.post_thread(incident.root_message_id, format_resolution(incident, now))
                if self.on_resolve:
                    self.on_resolve(incident)
                changed.append(incident)
        return changed

    def mark_diagnosed(self, incident: Incident, at: datetime) -> None:
        """Record a completed diagnosis so reruns are bounded by D66."""
        incident.agent_runs += 1
        incident.last_agent_run_at = at.astimezone(timezone.utc)
        incident.last_diagnosis_burn_rate = incident.burn_rate_usd_hour
        incident.last_diagnosis_anchor_cell = dict(incident.current_anchor.cell)

    def _is_material_change(self, incident: Incident, now: datetime) -> bool:
        if incident.agent_runs >= MAX_AGENT_RUNS_PER_INCIDENT or incident.last_agent_run_at is None:
            return False
        if now - incident.last_agent_run_at < timedelta(minutes=AGENT_RERUN_COOLDOWN_MINUTES):
            return False
        prior = incident.last_diagnosis_burn_rate or 0.0
        burn_rate_doubled = prior > 0 and incident.burn_rate_usd_hour >= prior * 2
        prior_anchor = incident.last_diagnosis_anchor_cell
        changed_lattice_branch = prior_anchor is not None and not lattice_related(
            prior_anchor, incident.current_anchor.cell
        )
        return burn_rate_doubled or changed_lattice_branch

    def _matches(self, candidate: Cluster) -> list[Incident]:
        """Open incidents this cluster belongs to, nearest in the lattice first.

        A cluster anchored on `{provider: adyen}` is related to both `adyen x MX` and
        `adyen x BR`.  The most specific identity wins the update -- impact alone would
        hand every ambiguous cluster to whichever incident happens to be larger.
        """
        matches = [incident for incident in self.open_incidents()
                   if lattice_related(incident.identity_cell, candidate.anchor.cell)]
        return sorted(matches, key=lambda incident: (len(incident.identity_cell),
                                                     incident.current_anchor.lost_approvals),
                      reverse=True)

    def _open(self, candidate: Cluster, now: datetime) -> Incident:
        self._sequence += 1
        incident = Incident(
            incident_id=f"inc_{now:%Y%m%d_%H%M%S}_{self._sequence:03d}",
            identity_cell=dict(candidate.anchor.cell), current_anchor=candidate.anchor,
            onset_ts=self._backdate_onset(candidate.anchor, now), opened_at=now,
            burn_rate_usd_hour=burn_rate_usd_hour(candidate.anchor), blast_radius=blast_radius(candidate.anchor),
            last_evaluated_at=now,
        )
        self.incidents[incident.incident_id] = incident
        if self.repository:
            self.repository.save(incident)
        return incident

    def _post_new_alerts(self, newly_opened: Iterable[Incident]) -> None:
        """Post only the top three roots when one detector tick opens a storm."""
        candidates = [incident for incident in newly_opened if incident.status == "open"]
        # D43: burn rate primary, blast radius then lost approvals as deterministic
        # tiebreakers, so two similar-burn incidents have a stable order.
        candidates.sort(key=lambda incident: (incident.burn_rate_usd_hour, incident.blast_radius,
                                              incident.current_anchor.lost_approvals), reverse=True)
        already_announced = sum(1 for incident in self.open_incidents()
                                if incident.root_message_id not in (None, "suppressed"))
        available = max(0, MAX_CONCURRENT_ALERTS - already_announced)
        individual = candidates[:available]
        suppressed = candidates[available:]
        for incident in individual:
            if self.poster:
                incident.root_message_id = self.poster.post_root(format_root_alert(incident))
            if self.on_open:
                self.on_open(incident)
        if not suppressed:
            # Latching this forever would silence the summary for every later storm.
            self._storm_summary_posted = False
            return
        for incident in suppressed:
            incident.root_message_id = "suppressed"
        if self.poster and not self._storm_summary_posted:
            self.poster.post_root(format_storm_summary(suppressed))
            self._storm_summary_posted = True

    def _backdate_onset(self, anchor: Signal, now: datetime) -> datetime:
        """Find the first contiguous bucket below its own baseline before detection."""
        cursor = now - timedelta(seconds=BUCKET_SECONDS)
        onset = cursor
        # A bounded scan avoids turning a pathological mock into an unbounded hot path.
        for _ in range(24 * 60 * 60 // BUCKET_SECONDS):
            rows = self.store.get_counts(cursor, cursor + timedelta(seconds=BUCKET_SECONDS), BUCKET_SECONDS,
                                         tuple(anchor.cell), anchor.cell)
            if not rows:
                break
            attempts = sum(row.attempts for row in rows)
            approved = sum(row.approved for row in rows)
            baseline = self.baselines.get(anchor.cell, cursor + timedelta(seconds=BUCKET_SECONDS))
            if not attempts or baseline is None or approved / attempts >= baseline.rate:
                break
            onset = cursor
            cursor -= timedelta(seconds=BUCKET_SECONDS)
        return onset
