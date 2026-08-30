"""Deterministically group lattice-related signals into incident candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from agent_workflow.detect.scan import Signal


def canonical_cell(cell: Mapping[str, str | None]) -> tuple[tuple[str, str | None], ...]:
    return tuple(sorted(cell.items()))


def lattice_related(left: Mapping[str, str | None], right: Mapping[str, str | None]) -> bool:
    """True only when one cell contains the other in the dimension lattice."""
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    return all(larger.get(key) == value for key, value in smaller.items())


@dataclass(frozen=True)
class Cluster:
    anchor: Signal
    members: tuple[Signal, ...]


def cluster(signals: Iterable[Signal]) -> list[Cluster]:
    """Greedy, impact-ranked clustering as specified in D25."""
    remaining = sorted(signals, key=lambda signal: signal.lost_approvals, reverse=True)
    result: list[Cluster] = []
    while remaining:
        anchor = remaining.pop(0)
        members = [anchor]
        unrelated = []
        for signal in remaining:
            if lattice_related(signal.cell, anchor.cell):
                members.append(signal)
            else:
                unrelated.append(signal)
        remaining = unrelated
        result.append(Cluster(anchor=anchor, members=tuple(members)))
    return result
