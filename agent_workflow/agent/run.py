"""Bounded OpenAI Responses API loop for one incident investigation."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_workflow.agent.schema import Diagnosis, insufficient_evidence, parse_diagnosis
from agent_workflow.agent.catalogue import Catalogue
from agent_workflow.agent.tools import DiagnosisTools, TOOL_SCHEMAS
from agent_workflow.config import AGENT_BUDGET_SECONDS, AGENT_MAX_TOOL_CALLS, AGENT_MODEL


def _playbook() -> str:
    return (Path(__file__).with_name("playbook.md").read_text()).strip()


def _incident_prompt(incident, catalogue: Catalogue) -> str:
    anchor = incident.current_anchor
    return "\n".join((
        "Investigate this payment-conversion incident using the four provided tools.",
        f"incident_id={incident.incident_id}", f"identity_cell={json.dumps(incident.identity_cell, sort_keys=True)}",
        f"anchor_cell={json.dumps(anchor.cell, sort_keys=True)}",
        f"onset_ts={incident.onset_ts.isoformat()}", f"detected_at={incident.opened_at.isoformat()}",
        f"baseline_rate={anchor.baseline_rate}", f"observed_rate={anchor.observed_rate}",
        f"baseline_source={anchor.baseline_source}", f"error_share={anchor.error_share}",
        "Return ONLY a JSON object with root_cause, confidence (high|medium|insufficient_evidence), evidence "
        "([{claim, support}]), alternatives_ruled_out, action_id (one approved catalogue action_id), action_parameters "
        "(object; reroute_provider requires target_provider and open_provider_sev1 requires provider), recommended_action, "
        "ops_explanation, and exec_one_liner.",
        "Catalogue (authoritative; do not invent capabilities, contacts, or actions):",
        catalogue.context(),
    ))


class DiagnosisRunner:
    """Thin wrapper to make the OpenAI dependency optional and testable with a fake client."""

    def __init__(self, tools: DiagnosisTools, *, client: Any | None = None, model: str = AGENT_MODEL,
                 budget_seconds: int = AGENT_BUDGET_SECONDS, max_tool_calls: int = AGENT_MAX_TOOL_CALLS,
                 catalogue: Catalogue | None = None) -> None:
        self.tools, self.client, self.model = tools, client, model
        self.budget_seconds, self.max_tool_calls = budget_seconds, max_tool_calls
        self.catalogue = catalogue or Catalogue.load()

    def _validated_action(self, diagnosis: Diagnosis, incident) -> Diagnosis:
        allowed, reason = self.catalogue.validate_action(diagnosis.action_id, diagnosis.action_parameters, incident)
        if allowed:
            suffix = ", ".join(f"{key}={value}" for key, value in sorted(diagnosis.action_parameters.items()))
            recommendation = self.catalogue.action_label(diagnosis.action_id)
            return replace(diagnosis, recommended_action=f"{recommendation}{f' ({suffix})' if suffix else ''}")
        return replace(diagnosis, action_id="monitor_only",
                       action_parameters={},
                       recommended_action=f"No safe action — monitor only. {reason}")

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
        try:
            response = self.client.responses.create(
                model=self.model, instructions=_playbook(), input=_incident_prompt(incident, self.catalogue), tools=TOOL_SCHEMAS,
                parallel_tool_calls=False, store=False,
            )
            while True:
                function_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
                if not function_calls:
                    return self._validated_action(parse_diagnosis(response.output_text), incident)
                outputs = []
                for call in function_calls:
                    if calls >= self.max_tool_calls or time.monotonic() - started >= self.budget_seconds:
                        return insufficient_evidence("budget_exhausted", detail="Investigation is inconclusive; the available checks did not isolate one specific cause.")
                    arguments = json.loads(call.arguments)
                    result = self.tools.call(call.name, arguments)
                    outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, default=str)})
                    calls += 1
                response = self.client.responses.create(
                    model=self.model, previous_response_id=response.id, input=outputs, tools=TOOL_SCHEMAS,
                    parallel_tool_calls=False, store=False,
                )
        except Exception:
            # Do not let a provider/network failure affect lifecycle or root alerts.
            return insufficient_evidence("llm_unavailable", detail="Investigation could not complete yet; deterministic incident facts remain available.")
