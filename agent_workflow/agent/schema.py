"""Strict, small contract between the diagnosis model and our presentation code."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


CONFIDENCES = {"high", "medium", "insufficient_evidence"}


@dataclass(frozen=True)
class Diagnosis:
    root_cause: str
    confidence: str
    evidence: list[dict[str, str]] = field(default_factory=list)
    alternatives_ruled_out: list[str] = field(default_factory=list)
    action_id: str = "monitor_only"
    action_parameters: dict[str, str] = field(default_factory=dict)
    recommended_action: str = "no_safe_action"
    # Built by our code from the catalogue + deterministic figures, never by the model.
    next_step: str = ""
    ops_explanation: str = "Investigation is inconclusive."
    exec_one_liner: str = "Investigation is inconclusive; continue monitoring."
    low_confidence_reason: str | None = None  # internal only; never render it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def insufficient_evidence(reason: str, *, detail: str = "") -> Diagnosis:
    message = detail or "The available data does not isolate one specific cause."
    return Diagnosis(
        root_cause="Insufficient evidence to isolate a specific root cause.",
        confidence="insufficient_evidence", ops_explanation=message,
        exec_one_liner="Investigation remains inconclusive; monitor the affected route and gather more evidence.",
        low_confidence_reason=reason,
    )


def parse_diagnosis(value: str | dict[str, Any]) -> Diagnosis:
    """Validate untrusted model JSON before it reaches Slack or reports."""
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        confidence = raw["confidence"]
        if confidence not in CONFIDENCES:
            raise ValueError("invalid confidence")
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise ValueError("invalid evidence")
        return Diagnosis(
            root_cause=str(raw["root_cause"]), confidence=confidence,
            evidence=[{"claim": str(item.get("claim", "")), "support": str(item.get("support", ""))} for item in evidence],
            alternatives_ruled_out=[str(item) for item in raw.get("alternatives_ruled_out", [])],
            action_id=str(raw.get("action_id", "monitor_only")),
            action_parameters={str(key): str(item) for key, item in raw.get("action_parameters", {}).items()},
            recommended_action=str(raw.get("recommended_action", "no_safe_action")),
            ops_explanation=str(raw["ops_explanation"]), exec_one_liner=str(raw["exec_one_liner"]),
            low_confidence_reason=str(raw["low_confidence_reason"]) if raw.get("low_confidence_reason") else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return insufficient_evidence("invalid_model_output", detail=f"Investigation could not produce a validated conclusion: {error}.")
