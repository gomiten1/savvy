"""Collapse detector projections for explicitly controlled demo scenarios."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Iterable

from agent_workflow.detect.scan import Signal


def active_controlled_filters(path: str | Path, now: datetime) -> list[dict[str, str]]:
    """Read unexpired controlled-demo targets; malformed handoff data is ignored."""
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    now_iso = now.isoformat()
    return [entry["cell_filter"] for entry in entries
            if isinstance(entry, dict) and entry.get("mode") == "controlled"
            and isinstance(entry.get("cell_filter"), dict) and entry.get("start", "") <= now_iso < entry.get("end", "")]


def collapse_controlled_projections(signals: Iterable[Signal], filters: Iterable[dict[str, str]]) -> list[Signal]:
    """Keep one most-specific detector view for each controlled scenario.

    The normal detector intentionally retains independent overlapping projections.
    A controlled injection is the exception: its handoff target provides the
    provenance needed to treat those projections as one demo scenario.
    """
    remaining = list(signals)
    selected: list[Signal] = []
    for target in filters:
        matching = [signal for signal in remaining if _matches_target(signal, target)]
        if not matching:
            continue
        primary = max(matching, key=lambda signal: (len(signal.cell), signal.lost_approvals))
        selected.append(primary)
        matching_ids = {id(signal) for signal in matching}
        remaining = [signal for signal in remaining if id(signal) not in matching_ids]
    return remaining + selected


def _matches_target(signal: Signal, target: dict[str, str]) -> bool:
    shared = set(signal.cell).intersection(target)
    return bool(shared) and all(signal.cell[key] == target[key] for key in shared)
