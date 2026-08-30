"""Scenario cover for the parts of diagnosis that must be deterministic.

Nothing here calls an LLM: `DiagnosisRunner` takes a fake client, so what is under
test is our own contract -- clustering, the action guardrail, and the promise that
no money figure reaches Slack unless economics.py produced it.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from agent_workflow.agent.catalogue import Catalogue
from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.schema import Diagnosis
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.detect.cluster import cluster
from agent_workflow.detect.registry import Incident, IncidentRegistry
from agent_workflow.detect.scan import Signal
from agent_workflow.economics import blast_radius, burn_rate_usd_hour
from agent_workflow.slack.templates import format_diagnosis, format_storm_summary
from agent_workflow.store.interface import Attempt
from agent_workflow.store.mock import MockStore

from tests.test_detection_pipeline import RecordingPoster, baseline_lookup


UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)


def signal(cell: dict[str, str | None], *, lost: float, attempts: int = 1000,
           error_share: float = 0.02, mix: dict[str, float] | None = None,
           amount_usd_total: float = 50_000.0) -> Signal:
    baseline, observed = 0.90, 0.90 - lost / attempts
    return Signal(
        cell=cell, observed_rate=observed, baseline_rate=baseline, baseline_source="hour_of_week",
        z_score=30.0, lost_approvals=lost, attempts=attempts, approved=int(attempts * observed),
        error_share=error_share, decline_code_mix=mix or {"05_do_not_honor": 1.0},
        amount_usd_total=amount_usd_total,
    )


def incident_for(anchor: Signal, *, opened: datetime = START, onset_minutes: int = 30) -> Incident:
    return Incident(
        incident_id="inc_test_001", identity_cell=dict(anchor.cell), current_anchor=anchor,
        onset_ts=opened - timedelta(minutes=onset_minutes), opened_at=opened,
        burn_rate_usd_hour=burn_rate_usd_hour(anchor), blast_radius=blast_radius(anchor),
        last_evaluated_at=opened,
    )


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.id, self.output = "resp-1", []
        self.output_text = json.dumps(payload)


class FakeClient:
    """Answers in one turn with no tool calls, so only our validation path runs."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.responses = self

    def create(self, **_kwargs) -> FakeResponse:
        return FakeResponse(self.payload)


class ClusterSeparationTests(unittest.TestCase):
    def test_two_simultaneous_incidents_in_one_country_stay_separate(self) -> None:
        """The scored scenario. `country=MX` reports the sum of both and must not anchor."""
        clusters = cluster([
            signal({"country": "MX"}, lost=300.0, attempts=4000),
            signal({"provider": "adyen", "country": "MX"}, lost=155.0),
            signal({"provider": "stripe", "country": "MX"}, lost=145.0),
            signal({"provider": "adyen"}, lost=155.0),
            signal({"provider": "stripe"}, lost=145.0),
        ])

        anchors = [candidate.anchor.cell for candidate in clusters]
        self.assertEqual(2, len(clusters))
        self.assertIn({"provider": "adyen", "country": "MX"}, anchors)
        self.assertIn({"provider": "stripe", "country": "MX"}, anchors)
        self.assertNotIn({"country": "MX"}, anchors)
        # The 1-D provider cells belong to their own incident, not to a third one.
        for candidate in clusters:
            self.assertIn({"provider": candidate.anchor.cell["provider"]},
                          [member.cell for member in candidate.members])

    def test_parent_dilution_reanchors_onto_the_contained_child(self) -> None:
        clusters = cluster([
            signal({"country": "MX"}, lost=150.0, attempts=4000),
            signal({"provider": "adyen", "country": "MX"}, lost=148.0),
        ])

        self.assertEqual(1, len(clusters))
        self.assertEqual({"provider": "adyen", "country": "MX"}, clusters[0].anchor.cell)
        self.assertIn({"country": "MX"}, [member.cell for member in clusters[0].members])

    def test_genuine_country_wide_story_keeps_the_parent_anchor(self) -> None:
        clusters = cluster([
            signal({"country": "MX"}, lost=300.0, attempts=4000),
            signal({"provider": "adyen", "country": "MX"}, lost=80.0),
            signal({"provider": "stripe", "country": "MX"}, lost=75.0),
        ])

        self.assertEqual(1, len(clusters))
        self.assertEqual({"country": "MX"}, clusters[0].anchor.cell)

    def test_lattice_unrelated_incidents_still_separate(self) -> None:
        clusters = cluster([
            signal({"provider": "adyen", "country": "MX"}, lost=200.0),
            signal({"provider": "stripe", "country": "BR"}, lost=90.0),
        ])
        self.assertEqual(2, len(clusters))


class RegistryRankingTests(unittest.TestCase):
    def _registry(self, poster: RecordingPoster) -> IncidentRegistry:
        return IncidentRegistry(MockStore([]), baseline_lookup(), poster,
                                debounce_ticks=1, resolve_miss_ticks=2)

    def test_a_cluster_matching_two_incidents_starves_neither(self) -> None:
        """`{provider: adyen}` relates to both adyen x MX and adyen x BR (doc item D2)."""
        poster = RecordingPoster()
        registry = self._registry(poster)
        registry.tick(cluster([signal({"provider": "adyen", "country": "MX"}, lost=200.0)]), START)
        registry.tick(cluster([signal({"provider": "adyen", "country": "BR"}, lost=180.0)]),
                      START + timedelta(minutes=1))
        self.assertEqual(2, len(registry.open_incidents()))

        registry.tick(cluster([signal({"provider": "adyen"}, lost=380.0, attempts=2000)]),
                      START + timedelta(minutes=2))

        self.assertEqual(2, len(registry.open_incidents()))
        self.assertEqual([0, 0], [incident.miss_streak for incident in registry.open_incidents()])

    def test_matches_are_ordered_nearest_in_lattice_then_by_impact(self) -> None:
        """Impact alone would hand an ambiguous cluster to whichever incident is larger."""
        registry = self._registry(RecordingPoster())
        broad = incident_for(signal({"provider": "adyen"}, lost=500.0, attempts=4000))
        narrow = incident_for(signal({"provider": "adyen", "country": "MX"}, lost=200.0))
        broad.incident_id, narrow.incident_id = "inc_broad", "inc_narrow"
        registry.incidents = {"inc_broad": broad, "inc_narrow": narrow}

        matched = registry._matches(cluster([signal({"provider": "adyen", "country": "MX"},
                                                    lost=210.0)])[0])

        self.assertEqual(["inc_narrow", "inc_broad"], [incident.incident_id for incident in matched])

    def test_equal_burn_rates_are_ordered_by_blast_radius(self) -> None:
        """D43's tiebreaker, and the storm summary must inherit the same order."""
        poster = RecordingPoster()
        registry = self._registry(poster)
        # Same lost approvals and same total amount => same burn rate; different depth.
        shallow = signal({"provider": "adyen"}, lost=100.0, attempts=2000)
        deep = signal({"provider": "stripe"}, lost=100.0, attempts=500)
        registry.tick(cluster([shallow, deep]), START)

        posted = poster.roots
        self.assertEqual(2, len(posted))
        self.assertIn("provider=stripe", posted[0])  # deeper drop, same burn rate
        self.assertIn("provider=adyen", posted[1])


class ActionGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogue = Catalogue.load()

    def _runner(self, payload: dict) -> DiagnosisRunner:
        tools = DiagnosisTools(MockStore([]), baseline_lookup())
        return DiagnosisRunner(tools, client=FakeClient(payload), catalogue=self.catalogue)

    @staticmethod
    def _payload(**overrides) -> dict:
        payload = {
            "root_cause": "Adyen card authorisations in Mexico are timing out.",
            "confidence": "high", "evidence": [{"claim": "c", "support": "s"}],
            "alternatives_ruled_out": ["issuer"], "action_id": "monitor_only",
            "action_parameters": {}, "recommended_action": "ignored, our code rebuilds this",
            "ops_explanation": "ops", "exec_one_liner": "exec",
        }
        payload.update(overrides)
        return payload

    def test_rerouting_to_a_method_the_country_does_not_offer_is_rejected(self) -> None:
        anchor = signal({"provider": "adyen", "country": "MX", "method": "pix"}, lost=200.0)
        incident = incident_for(anchor)
        allowed, reason = self.catalogue.validate_action(
            "reroute_provider", {"target_provider": "stripe"}, incident)
        self.assertFalse(allowed)
        self.assertIn("pix", reason)

    def test_rerouting_within_a_served_country_and_method_is_accepted(self) -> None:
        anchor = signal({"provider": "adyen", "country": "MX", "method": "oxxo"}, lost=200.0)
        allowed, _ = self.catalogue.validate_action(
            "reroute_provider", {"target_provider": "stripe"}, incident_for(anchor))
        self.assertTrue(allowed)

    def test_bank_stories_are_refused_where_the_provider_reports_no_issuer(self) -> None:
        anchor = signal({"provider": "adyen", "issuing_bank": "bbva"}, lost=200.0)
        allowed, reason = self.catalogue.validate_action(
            "contact_issuer", {"bank": "bbva"}, incident_for(anchor))
        self.assertFalse(allowed)
        self.assertIn("issuer data", reason)

        anchor = signal({"provider": "mercadopago", "issuing_bank": "bbva"}, lost=200.0)
        allowed, _ = self.catalogue.validate_action(
            "contact_issuer", {"bank": "bbva"}, incident_for(anchor))
        self.assertTrue(allowed)

    def test_unknown_bank_is_never_a_bank_story(self) -> None:
        anchor = signal({"provider": "mercadopago", "issuing_bank": "unknown_bank"}, lost=200.0)
        allowed, _ = self.catalogue.validate_action(
            "contact_issuer", {"bank": "unknown_bank"}, incident_for(anchor))
        self.assertFalse(allowed)

    def test_sev1_needs_a_deterministically_elevated_error_share(self) -> None:
        quiet = incident_for(signal({"provider": "adyen"}, lost=200.0, error_share=0.02))
        loud = incident_for(signal({"provider": "adyen"}, lost=200.0, error_share=0.40))
        self.assertFalse(self.catalogue.validate_action("open_provider_sev1", {"provider": "adyen"}, quiet)[0])
        self.assertTrue(self.catalogue.validate_action("open_provider_sev1", {"provider": "adyen"}, loud)[0])

    def test_a_valid_action_resolves_to_a_named_contact_and_drafted_step(self) -> None:
        anchor = signal({"provider": "adyen", "country": "MX", "method": "card"}, lost=200.0,
                        error_share=0.40, mix={"91_96_network_timeout": 0.8, "05_do_not_honor": 0.2})
        incident = incident_for(anchor)
        runner = self._runner(self._payload(action_id="open_provider_sev1",
                                            action_parameters={"provider": "adyen"}))

        diagnosis = runner.investigate(incident)

        self.assertEqual("open_provider_sev1", diagnosis.action_id)
        self.assertIn("Lucas Meyer (Adyen operations)", diagnosis.next_step)
        self.assertIn("91_96_network_timeout", diagnosis.next_step)
        self.assertIn(f"${incident.burn_rate_usd_hour:,.0f}/hr", diagnosis.next_step)

    def test_a_rejected_action_keeps_its_reason_out_of_the_channel(self) -> None:
        anchor = signal({"provider": "adyen", "country": "MX", "method": "card"}, lost=200.0,
                        error_share=0.01)
        incident = incident_for(anchor)
        runner = self._runner(self._payload(action_id="open_provider_sev1",
                                            action_parameters={"provider": "adyen"}))

        diagnosis = runner.investigate(incident)
        rendered = format_diagnosis(incident, diagnosis)

        self.assertEqual("monitor_only", diagnosis.action_id)
        self.assertIn("action_rejected", diagnosis.low_confidence_reason)
        self.assertNotIn("error share", rendered)
        self.assertNotIn("catalogue", rendered.lower())


class ExecOutputTests(unittest.TestCase):
    def test_the_thread_money_line_comes_from_the_incident_not_the_model(self) -> None:
        anchor = signal({"provider": "adyen", "country": "MX"}, lost=200.0)
        incident = incident_for(anchor)
        diagnosis = Diagnosis(
            root_cause="r", confidence="high",
            exec_one_liner="Adyen MX card volume is failing, roughly $999,999,999/hr.",
            ops_explanation="o", recommended_action="a", next_step="n",
        )

        rendered = format_diagnosis(incident, diagnosis)

        self.assertTrue(rendered.startswith(f"*{diagnosis.exec_one_liner}*"))
        self.assertIn(f"${incident.burn_rate_usd_hour:,.0f}/hr", rendered)
        self.assertIn(f"${incident.cumulative_loss_usd:,.0f} lost since", rendered)
        # The model's invented figure is quoted as its prose, never as our money line.
        self.assertNotIn("$999,999,999/hr ·", rendered)

    def test_invalid_model_output_still_renders_deterministic_money(self) -> None:
        anchor = signal({"provider": "adyen"}, lost=200.0)
        incident = incident_for(anchor)
        tools = DiagnosisTools(MockStore([]), baseline_lookup())
        runner = DiagnosisRunner(tools, client=FakeClient({"nonsense": True}))

        diagnosis = runner.investigate(incident)
        rendered = format_diagnosis(incident, diagnosis)

        self.assertEqual("insufficient_evidence", diagnosis.confidence)
        self.assertIn(f"${incident.burn_rate_usd_hour:,.0f}/hr", rendered)


class DeclineMixToolTests(unittest.TestCase):
    def _tools(self, store) -> DiagnosisTools:
        return DiagnosisTools(store, baseline_lookup())

    def test_it_falls_back_to_samples_when_the_store_offers_no_window_mix(self) -> None:
        rows = [
            Attempt(attempt_id=f"a{index}", payment_id=f"p{index}", attempt_number=1,
                    event_ts=START + timedelta(seconds=index), merchant_id="merch_acme",
                    provider_id="adyen", method="card", country="MX", issuing_bank="unknown_bank",
                    status="declined" if index else "approved",
                    decline_code=None if index == 0 else ("05_do_not_honor" if index < 3 else "51_insufficient_funds"),
                    amount_minor=1000, currency="MXN", amount_usd=50.0)
            for index in range(5)
        ]
        tools = self._tools(MockStore(rows))
        self.assertFalse(hasattr(MockStore(rows), "get_decline_mix"))

        result = tools.call("get_decline_mix", {
            "cell": {}, "start_ts": START.isoformat(),
            "end_ts": (START + timedelta(minutes=1)).isoformat()})

        self.assertAlmostEqual(1.0, sum(result["decline_mix"].values()))
        self.assertAlmostEqual(0.5, result["decline_mix"]["05_do_not_honor"])

    def test_it_prefers_the_store_window_mix_when_one_exists(self) -> None:
        class MixStore(MockStore):
            def get_decline_mix(self, start_ts, end_ts, filters):
                return {"91_96_network_timeout": 1.0}

        result = self._tools(MixStore([])).call("get_decline_mix", {
            "cell": {"provider": "adyen"}, "start_ts": START.isoformat(),
            "end_ts": (START + timedelta(minutes=1)).isoformat()})

        self.assertEqual({"91_96_network_timeout": 1.0}, result["decline_mix"])


if __name__ == "__main__":
    unittest.main()
