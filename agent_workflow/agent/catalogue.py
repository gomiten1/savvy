"""Versioned domain context and recommendation guardrails for diagnosis."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


CATALOGUE_DIR = Path(__file__).parents[1] / "catalogue"
ELEVATED_ERROR_SHARE = 0.10
UNKNOWN_BANK = "unknown_bank"


@dataclass(frozen=True)
class ActionPlan:
    """A validated recommendation, already addressed to a named owner (D59)."""
    label: str
    contact: str | None
    next_step: str


def _cell_text(cell) -> str:
    return " · ".join(f"{key}={value}" for key, value in sorted(cell.items()) if value is not None)


@dataclass(frozen=True)
class Catalogue:
    providers: tuple[dict[str, str], ...]
    methods: tuple[dict[str, str], ...]
    countries: tuple[dict[str, str], ...]
    actions: tuple[dict[str, str], ...]
    ownership: tuple[dict[str, str], ...]

    @classmethod
    def load(cls, directory: Path = CATALOGUE_DIR) -> "Catalogue":
        def rows(name: str) -> tuple[dict[str, str], ...]:
            with (directory / name).open(newline="") as handle:
                return tuple(dict(row) for row in csv.DictReader(handle))

        return cls(rows("providers.csv"), rows("methods.csv"), rows("countries.csv"),
                   rows("actions.csv"), rows("ownership.csv"))

    def context(self) -> str:
        """Small, explicit facts the model must use instead of inventing domain facts."""
        sections = []
        for title, records in (("providers", self.providers), ("methods", self.methods),
                               ("countries", self.countries), ("actions", self.actions),
                               ("ownership", self.ownership)):
            lines = [", ".join(f"{key}={value}" for key, value in row.items()) for row in records]
            sections.append(f"{title}:\n" + "\n".join(lines))
        return "\n\n".join(sections)

    # -- lookups --------------------------------------------------------------
    def _action(self, action_id: str) -> dict[str, str] | None:
        return next((row for row in self.actions if row["action_id"] == action_id), None)

    def _provider(self, provider: str | None) -> dict[str, str] | None:
        return next((row for row in self.providers if row["provider"] == provider), None)

    def methods_in(self, country: str | None) -> list[str]:
        row = next((row for row in self.countries if row["country"] == country), None)
        return row["methods"].split("|") if row else []

    def serves(self, provider: str | None, country: str | None) -> bool:
        row = self._provider(provider)
        return bool(row) and country in row["countries"].split("|")

    def bank_is_meaningful(self, method: str | None) -> bool:
        """`issuing_bank` is only real for card methods (D54)."""
        row = next((row for row in self.methods if row["method"] == method), None)
        return bool(row) and row["issuing_bank_meaningful"] == "true"

    def contact_for(self, entity_type: str, entity: str | None) -> str | None:
        row = next((row for row in self.ownership
                    if row["entity_type"] == entity_type and row["entity"] == entity), None)
        return row["contact"] if row else None

    # -- guardrail ------------------------------------------------------------
    def validate_action(self, action_id: str, parameters: dict[str, str], incident) -> tuple[bool, str]:
        """Accept only catalogue actions whose deterministic preconditions are met."""
        action = self._action(action_id)
        if action is None:
            return False, "The proposed action is not in the approved action catalogue."

        anchor = incident.current_anchor
        cell = anchor.cell
        country, method = cell.get("country"), cell.get("method")
        provider, merchant, bank = cell.get("provider"), cell.get("merchant"), cell.get("issuing_bank")

        if action_id == "monitor_only":
            return True, ""

        if action_id == "reroute_provider":
            target = parameters.get("target_provider")
            if not provider:
                return False, "The affected cell does not name a provider to route away from."
            if not target or self._provider(target) is None:
                return False, "The proposed target provider is not in the catalogue."
            if target == provider:
                return False, "The proposed target provider is the one already affected."
            if country and not self.serves(target, country):
                return False, f"{target} does not serve {country}."
            if method:
                # Method availability is country-scoped in this market, not provider-scoped.
                reachable = self.methods_in(country) if country else [
                    offered for row in self.countries
                    if row["country"] in self._provider(target)["countries"].split("|")
                    for offered in row["methods"].split("|")
                ]
                if method not in reachable:
                    where = country or f"any country {target} serves"
                    return False, f"{method} is not offered in {where}."
            return True, ""

        if action_id == "open_provider_sev1":
            if not provider:
                return False, "A provider-scoped cell is required to open a provider support case."
            if parameters.get("provider") != provider:
                return False, "The escalation provider does not match the affected provider cell."
            if anchor.error_share < ELEVATED_ERROR_SHARE:
                return False, "The deterministic error share is not elevated enough for an infrastructure escalation."
            return True, ""

        if action_id == "contact_issuer":
            if not bank or bank == UNKNOWN_BANK:
                return False, "The affected cell does not name a real issuing bank."
            if parameters.get("bank") != bank:
                return False, "The proposed bank does not match the affected cell."
            if method and not self.bank_is_meaningful(method):
                return False, f"Issuing bank is not a meaningful dimension for {method}."
            row = self._provider(provider) if provider else None
            if row is not None and row["exposes_issuer"] != "true":
                return False, f"{provider} does not report issuer data, so its bank attribution is not usable."
            if self.contact_for("bank", bank) is None:
                return False, "No relationship owner is on file for that bank."
            return True, ""

        if action_id == "notify_merchant":
            if not merchant:
                return False, "A merchant-scoped cell is required to notify an account manager."
            if parameters.get("merchant") != merchant:
                return False, "The proposed merchant does not match the affected cell."
            if self.contact_for("merchant", merchant) is None:
                return False, "No account manager is on file for that merchant."
            return True, ""

        if action_id == "disable_method":
            if not method:
                return False, "The affected cell does not name a method to disable."
            if not country:
                return False, "A country is required to confirm an alternative checkout path exists."
            if len([offered for offered in self.methods_in(country) if offered != method]) == 0:
                return False, f"{country} offers no alternative checkout path to {method}."
            return True, ""

        return False, "The action precondition is not implemented."

    # -- addressable next step (D59/D60) ---------------------------------------
    def resolve_action(self, action_id: str, parameters: dict[str, str], incident) -> ActionPlan:
        """Build the owner-addressed next step.  Every figure here is deterministic."""
        action = self._action(action_id)
        anchor = incident.current_anchor
        suffix = ", ".join(f"{key}={value}" for key, value in sorted(parameters.items()))
        label = f"{action['action']}{f' ({suffix})' if suffix else ''}"

        entity_type, owner_parameter = action["owner_entity_type"], action["owner_parameter"]
        # ownership.csv keys banks as `bank`; the detector dimension is `issuing_bank`.
        dimension = {"provider": "provider", "merchant": "merchant", "bank": "issuing_bank"}.get(entity_type)
        entity = parameters.get(owner_parameter) or (anchor.cell.get(dimension) if dimension else None)
        contact = self.contact_for(entity_type, entity) if entity_type else None

        fields = {
            "provider": anchor.cell.get("provider") or "the affected provider",
            "target_provider": parameters.get("target_provider") or "the target provider",
            "country": anchor.cell.get("country") or "the affected country",
            "method": anchor.cell.get("method") or "the affected method",
            "bank": parameters.get("bank") or anchor.cell.get("issuing_bank") or "the issuing bank",
            "merchant": anchor.cell.get("merchant") or "the affected merchant",
            "alternatives": ", ".join(offered for offered in self.methods_in(anchor.cell.get("country"))
                                      if offered != anchor.cell.get("method")) or "none on file",
        }
        request = action["request"].format(**fields)
        addressee = contact or action["owner"].replace("_", " ")
        next_step = f"Contact {addressee} — {request}.\n{self._facts(incident)}"
        return ActionPlan(label=label, contact=contact, next_step=next_step)

    def _facts(self, incident) -> str:
        """Deterministic figures for a drafted message.  Never a model-produced number (D48)."""
        anchor = incident.current_anchor
        onset = incident.onset_ts.isoformat().replace("+00:00", "Z")
        top = sorted(anchor.decline_code_mix.items(), key=lambda item: item[1], reverse=True)[:3]
        mix = ", ".join(f"{code} {share:.0%}" for code, share in top) or "no decline-mix shift on file"
        return (
            f"Facts: {_cell_text(anchor.cell)} · conversion {anchor.baseline_rate:.1%} → "
            f"{anchor.observed_rate:.1%} since {onset} · ${incident.burn_rate_usd_hour:,.0f}/hr · "
            f"${incident.cumulative_loss_usd:,.0f} lost so far ({incident.cost_basis}) · "
            f"declines: {mix}."
        )
