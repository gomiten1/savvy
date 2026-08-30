from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.registry import IncidentRegistry
from agent_workflow.detect.scan import Signal, scan
from agent_workflow.economics import burn_rate_usd_hour
from agent_workflow.store.interface import Attempt
from agent_workflow.store.mock import MockStore


UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)


def baseline_lookup(*cells: tuple[dict[str, str | None], float, float]) -> BaselineLookup:
    records = {}
    for cell, rate, dispersion in cells:
        key = "|".join(f"{name}={value if value is not None else '<null>'}" for name, value in cell.items())
        records[key] = {
            "hour_of_week_rates": {"96": rate},
            "hour_of_week_observations": {"96": 2},
            "all_time_rate": rate,
            "dispersion": dispersion,
        }
    return BaselineLookup({"min_history_observations": 2, "cells": records})


def signal(cell: dict[str, str | None], *, lost: float = 20.0) -> Signal:
    return Signal(
        cell=cell,
        observed_rate=0.70,
        baseline_rate=0.90,
        baseline_source="all_time",
        z_score=10.0,
        lost_approvals=lost,
        attempts=100,
        approved=70,
        error_share=0.05,
        decline_code_mix={"provider_timeout": 1.0},
        amount_usd_total=4_000.0,
    )


def attempts(provider: str, approved_per_bucket: int) -> list[Attempt]:
    rows = []
    for bucket in range(5):
        for index in range(20):
            approved = index < approved_per_bucket
            number = bucket * 20 + index
            rows.append(Attempt(
                attempt_id=f"{provider}-{number}", payment_id=f"payment-{provider}-{number}",
                attempt_number=1, event_ts=START + timedelta(minutes=5 * bucket, seconds=index),
                merchant_id="M1", provider_id=provider, method="card", country="BR",
                issuing_bank="BR_B1", status="approved" if approved else "declined",
                decline_code=None if approved else "do_not_honor", amount_minor=4_000,
                currency="BRL", amount_usd=40.0,
            ))
    return rows


class RecordingPoster:
    def __init__(self) -> None:
        self.roots: list[str] = []
        self.threads: list[tuple[str, str]] = []

    def post_root(self, text: str) -> str:
        self.roots.append(text)
        return f"root-{len(self.roots)}"

    def post_thread(self, root_id: str, text: str) -> str:
        self.threads.append((root_id, text))
        return f"thread-{len(self.threads)}"


class DetectionPipelineTests(unittest.TestCase):
    def test_scan_requires_all_three_gates_and_ignores_improvements(self) -> None:
        lookup = baseline_lookup(({"provider": "P1"}, 0.90, 0.01))
        end = START + timedelta(minutes=25)

        degraded = MockStore(attempts("P1", 14))
        fired = scan(degraded, lookup, end, groupings=(("provider",),))
        self.assertEqual(1, len(fired))
        self.assertEqual({"provider": "P1"}, fired[0].cell)
        self.assertAlmostEqual(20.0, fired[0].lost_approvals)

        improved = MockStore(attempts("P1", 19))
        self.assertEqual([], scan(improved, lookup, end, groupings=(("provider",),)))

    def test_cluster_keeps_lattice_related_signals_together(self) -> None:
        parent = signal({"provider": "P1"}, lost=25)
        child = signal({"provider": "P1", "country": "BR"}, lost=15)
        unrelated = signal({"issuing_bank": "BR_B1"}, lost=10)

        clusters = cluster([child, unrelated, parent])
        self.assertEqual(2, len(clusters))
        self.assertEqual(parent, clusters[0].anchor)
        self.assertEqual((parent, child), clusters[0].members)
        self.assertEqual(unrelated, clusters[1].anchor)

    def test_registry_debounces_pins_identity_and_posts_one_root(self) -> None:
        parent = signal({"provider": "P1"}, lost=25)
        child = signal({"provider": "P1", "country": "BR"}, lost=40)
        store = MockStore(attempts("P1", 14))
        lookup = baseline_lookup(
            ({"provider": "P1"}, 0.90, 0.01),
            ({"provider": "P1", "country": "BR"}, 0.90, 0.01),
        )
        poster = RecordingPoster()
        registry = IncidentRegistry(store, lookup, poster, debounce_ticks=2, resolve_miss_ticks=2)

        registry.tick(cluster([parent]), START + timedelta(minutes=25))
        self.assertEqual([], registry.open_incidents())
        registry.tick(cluster([parent]), START + timedelta(minutes=30))
        incident = registry.open_incidents()[0]
        self.assertEqual({"provider": "P1"}, incident.identity_cell)
        self.assertEqual(1, len(poster.roots))

        registry.tick(cluster([child]), START + timedelta(minutes=35))
        self.assertEqual(1, len(registry.open_incidents()))
        self.assertEqual({"provider": "P1"}, incident.identity_cell)
        self.assertEqual(child, incident.current_anchor)
        self.assertEqual(1, len(poster.roots))

    def test_registry_resolves_after_configured_miss_streak(self) -> None:
        parent = signal({"provider": "P1"})
        store = MockStore(attempts("P1", 14))
        lookup = baseline_lookup(({"provider": "P1"}, 0.90, 0.01))
        registry = IncidentRegistry(store, lookup, debounce_ticks=1, resolve_miss_ticks=2)
        registry.tick(cluster([parent]), START + timedelta(minutes=25))
        incident = registry.open_incidents()[0]
        registry.tick([], START + timedelta(minutes=30))
        self.assertEqual("open", incident.status)
        registry.tick([], START + timedelta(minutes=35))
        self.assertEqual("resolved", incident.status)

    def test_two_unrelated_incidents_open_as_two_roots(self) -> None:
        """T4: simultaneous, disjoint causes must not merge into one incident."""
        p1 = signal({"provider": "P1"}, lost=30)
        p2 = signal({"provider": "P2"}, lost=20)
        store = MockStore(attempts("P1", 14) + attempts("P2", 14))
        lookup = baseline_lookup(
            ({"provider": "P1"}, 0.90, 0.01),
            ({"provider": "P2"}, 0.90, 0.01),
        )
        poster = RecordingPoster()
        registry = IncidentRegistry(store, lookup, poster, debounce_ticks=2)

        registry.tick(cluster([p1, p2]), START + timedelta(minutes=25))
        registry.tick(cluster([p1, p2]), START + timedelta(minutes=30))

        incidents = registry.open_incidents()
        self.assertEqual(2, len(incidents))
        self.assertEqual({"P1", "P2"}, {item.identity_cell["provider"] for item in incidents})
        self.assertEqual(2, len(poster.roots))

    def test_burn_rate_uses_windowed_lost_approvals(self) -> None:
        self.assertAlmostEqual(1_920.0, burn_rate_usd_hour(signal({"provider": "P1"})))


if __name__ == "__main__":
    unittest.main()
