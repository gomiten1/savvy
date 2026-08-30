"""Bounded OpenAI Responses API loop for one incident investigation."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_workflow.agent.schema import Diagnosis, insufficient_evidence, parse_diagnosis
from agent_workflow.agent.catalogue import Catalogue
from agent_workflow.agent.tools import DiagnosisTools, TOOL_SCHEMAS
from agent_workflow.config import (AGENT_BUDGET_SECONDS, AGENT_MAX_TOOL_CALLS, AGENT_MODEL,
                                   AGENT_REASONING_EFFORT)


def _playbook() -> str:
    return (Path(__file__).with_name("playbook.md").read_text()).strip()


def _incident_prompt(incident, catalogue: Catalogue) -> str:
    anchor = incident.current_anchor
    return "\n".join((
        "Investigate this payment-conversion incident using the provided tools.",
        f"incident_id={incident.incident_id}", f"identity_cell={json.dumps(incident.identity_cell, sort_keys=True)}",
        f"anchor_cell={json.dumps(anchor.cell, sort_keys=True)}",
        f"onset_ts={incident.onset_ts.isoformat()}", f"detected_at={incident.opened_at.isoformat()}",
        f"baseline_rate={anchor.baseline_rate}", f"observed_rate={anchor.observed_rate}",
        f"baseline_source={anchor.baseline_source}", f"error_share={anchor.error_share}",
        f"decline_code_mix={json.dumps(anchor.decline_code_mix, sort_keys=True)}",
        f"attempts={anchor.attempts}", f"approved={anchor.approved}", f"lost_approvals={anchor.lost_approvals:.1f}",
        f"burn_rate_usd_hour={incident.burn_rate_usd_hour:.0f}", f"blast_radius={incident.blast_radius:.4f}",
        f"cumulative_loss_usd={incident.cumulative_loss_usd:.0f}", f"cost_basis={incident.cost_basis}",
        # Pre-formatted so "quote it verbatim" needs no arithmetic and no unit choice.
        # Left to itself a model rescales $157,920/hr into "roughly $3.9M/hr", which then
        # contradicts the deterministic money line rendered directly beneath it (D48).
        f"burn_rate_display=${incident.burn_rate_usd_hour:,.0f}/hr",
        f"cumulative_loss_display=${incident.cumulative_loss_usd:,.0f}",
        "The figures above are authoritative and already computed. If you state a money figure "
        "anywhere, copy `burn_rate_display` or `cumulative_loss_display` character for character. "
        "Never recompute, rescale, round, annualise or convert them.",
        "Return ONLY a JSON object with root_cause, confidence (high|medium|insufficient_evidence), evidence "
        "([{claim, support}]), alternatives_ruled_out, action_id (one approved catalogue action_id), action_parameters "
        "(object; reroute_provider requires target_provider, open_provider_sev1 requires provider, contact_issuer "
        "requires bank, notify_merchant requires merchant, disable_method requires method), recommended_action, "
        "ops_explanation, and exec_one_liner.",
        "Catalogue (authoritative; do not invent capabilities, contacts, or actions):",
        catalogue.context(),
    ))


class DiagnosisRunner:
    """Thin wrapper to make the OpenAI dependency optional and testable with a fake client."""

    def __init__(self, tools: DiagnosisTools, *, client: Any | None = None, model: str = AGENT_MODEL,
                 budget_seconds: int = AGENT_BUDGET_SECONDS, max_tool_calls: int = AGENT_MAX_TOOL_CALLS,
                 catalogue: Catalogue | None = None, reasoning_effort: str | None = AGENT_REASONING_EFFORT) -> None:
        self.tools, self.client, self.model = tools, client, model
        self.budget_seconds, self.max_tool_calls = budget_seconds, max_tool_calls
        self.catalogue = catalogue or Catalogue.load()
        # Only the gpt-5 family accepts `reasoning`; sending it to a 4.x model is an error.
        self._reasoning = {"reasoning": {"effort": reasoning_effort}} if reasoning_effort and model.startswith("gpt-5") else {}

    def _validated_action(self, diagnosis: Diagnosis, incident) -> Diagnosis:
        allowed, reason = self.catalogue.validate_action(diagnosis.action_id, diagnosis.action_parameters, incident)
        if allowed:
            plan = self.catalogue.resolve_action(diagnosis.action_id, diagnosis.action_parameters, incident)
            return replace(diagnosis, recommended_action=plan.label, next_step=plan.next_step)
        # The rejection reason is why *our* catalogue refused, not something operations can
        # act on -- same reasoning as D64.  It stays internal; the channel gets the fallback.
        fallback = self.catalogue.resolve_action("monitor_only", {}, incident)
        return replace(diagnosis, action_id="monitor_only", action_parameters={},
                       recommended_action=fallback.label, next_step=fallback.next_step,
                       low_confidence_reason=diagnosis.low_confidence_reason or f"action_rejected: {reason}")

    def investigate(self, incident) -> Diagnosis:
        if self.client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                return insufficient_evidence("llm_unavailable", detail="Diagnosis worker is not configured; deterministic incident facts remain available.")
            try:
                from openai import OpenAI
                self.client = OpenAI()
            except ImportError:
                return insufficient_evidence("llm_unavailable", detail="OpenAI SDK is not installed; deterministic incident facts remain available.")

        started, calls = time.monotonic(), 0
        # The transcript is carried client-side rather than with `previous_response_id`:
        # that parameter resolves a *stored* response, and we send `store=False`, so
        # chaining on it 400s on the second turn and every tool-using investigation dies.
        # Reasoning items are echoed back with the rest of `output` so the gpt-5 models
        # keep their chain of thought across turns.
        conversation: list[Any] = [{"role": "user", "content": _incident_prompt(incident, self.catalogue)}]
        try:
            while True:
                response = self.client.responses.create(
                    model=self.model, instructions=_playbook(), input=conversation, tools=TOOL_SCHEMAS,
                    parallel_tool_calls=False, store=False, **self._reasoning,
                )
                function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
                if not function_calls:
                    return self._validated_action(parse_diagnosis(response.output_text), incident)
                conversation += [item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
                                 for item in response.output]
                for call in function_calls:
                    if calls >= self.max_tool_calls or time.monotonic() - started >= self.budget_seconds:
                        return insufficient_evidence("budget_exhausted", detail="Investigation is inconclusive; the available checks did not isolate one specific cause.")
                    arguments = json.loads(call.arguments)
                    result = self.tools.call(call.name, arguments)
                    conversation.append({"type": "function_call_output", "call_id": call.call_id,
                                         "output": json.dumps(result, default=str)})
                    calls += 1
        except Exception as error:
            # Do not let a provider/network failure affect lifecycle or root alerts -- but say
            # so on the console, because swallowing it silently hid exactly this bug.
            print(f"[agent] investigation failed: {type(error).__name__}: {error}", file=sys.stderr)
            return insufficient_evidence(f"llm_unavailable: {type(error).__name__}", detail="Investigation could not complete yet; deterministic incident facts remain available.")
