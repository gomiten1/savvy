"""Versioned domain context and recommendation guardrails for diagnosis."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


CATALOGUE_DIR = Path(__file__).parents[1] / "catalogue"
ELEVATED_ERROR_SHARE = 0.10


@dataclass(frozen=True)
class Catalogue:
    providers: tuple[dict[str, str], ...]
    methods: tuple[dict[str, str], ...]
    actions: tuple[dict[str, str], ...]
    ownership: tuple[dict[str, str], ...]

    @classmethod
    def load(cls, directory: Path = CATALOGUE_DIR) -> "Catalogue":
        def rows(name: str) -> tuple[dict[str, str], ...]:
            with (directory / name).open(newline="") as handle:
                return tuple(dict(row) for row in csv.DictReader(handle))

        return cls(rows("providers.csv"), rows("methods.csv"), rows("actions.csv"), rows("ownership.csv"))

    def context(self) -> str:
        """Small, explicit facts the model must use instead of inventing domain facts."""
        sections = []
        for title, records in (("providers", self.providers), ("methods", self.methods),
                               ("actions", self.actions), ("ownership", self.ownership)):
            lines = [", ".join(f"{key}={value}" for key, value in row.items()) for row in records]
            sections.append(f"{title}:\n" + "\n".join(lines))
        return "\n\n".join(sections)

    def validate_action(self, action_id: str, parameters: dict[str, str], incident) -> tuple[bool, str]:
        """Accept only catalogue actions whose deterministic preconditions are met."""
        action = next((row for row in self.actions if row["action_id"] == action_id), None)
        if action is None:
            return False, "The proposed action is not in the approved action catalogue."

        anchor = incident.current_anchor
        cell = anchor.cell
        country, method, provider = cell.get("country"), cell.get("method"), cell.get("provider")

        if action_id in {"reroute_provider", "enable_retry"}:
            if not country or not method:
                return False, "A country and method are required to verify an alternative route."
            alternatives = [row for row in self.providers if row["provider"] != provider
                            and country in row["countries"].split("|")
                            and method in row["methods"].split("|")]
            if not alternatives:
                return False, "No alternative provider in the catalogue serves this country and method."
            if action_id == "enable_retry":
                return False, "Retry-route and decline-class eligibility are not represented in the catalogue yet."
            if parameters.get("target_provider") not in {row["provider"] for row in alternatives}:
                return False, "The proposed target provider is not a compatible alternative for this country and method."
            return True, ""

        if action_id == "open_provider_sev1":
            if not provider:
                return False, "A provider-scoped cell is required to open a provider support case."
            if parameters.get("provider") != provider:
                return False, "The escalation provider does not match the affected provider cell."
            if anchor.error_share < ELEVATED_ERROR_SHARE:
                return False, "The deterministic error share is not elevated enough for an infrastructure escalation."
            return True, ""

        if action_id == "disable_method":
            return False, "Alternative checkout-path availability is not represented in the catalogue yet."

        if action_id == "monitor_only":
            return True, ""

        return False, "The action precondition is not implemented."

    def action_label(self, action_id: str) -> str:
        return next(row["action"] for row in self.actions if row["action_id"] == action_id)
