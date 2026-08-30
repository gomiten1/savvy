"""Deterministically group lattice-related signals into incident candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from agent_workflow.config import CLUSTER_CONTAINMENT_SHARE, CLUSTER_SPLIT_SHARE
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


def _distinct_branches(signals: Sequence[Signal]) -> list[Signal]:
    """Maximal set of mutually lattice-unrelated signals, impact-ranked.

    These are the rival stories under one parent: `adyen x MX` and `stripe x MX` are
    unrelated to each other but both sit inside `country=MX`.
    """
    branches: list[Signal] = []
    for signal in sorted(signals, key=lambda signal: signal.lost_approvals, reverse=True):
        if not any(lattice_related(signal.cell, chosen.cell) for chosen in branches):
            branches.append(signal)
    return branches


def _anchor_verdict(signal: Signal, others: Sequence[Signal]) -> str:
    """Whether a signal may anchor, or is a parent standing in for its children (D77).

    `lost_approvals = attempts x drop` is conserved going up the lattice: a country
    cell reports the same loss as the provider cell inside it that caused it, and the
    *sum* when two providers fail at once.  Ranking on it alone therefore hands every
    anchor to the diluted parent.
    """
    loss = signal.lost_approvals
    children = [other for other in others
                if len(other.cell) > len(signal.cell) and lattice_related(other.cell, signal.cell)]
    branches = _distinct_branches(children)
    if loss <= 0 or not branches:
        return "anchor"
    if len(branches) >= 2 and branches[1].lost_approvals >= CLUSTER_SPLIT_SHARE * loss:
        return "split"
    if branches[0].lost_approvals >= CLUSTER_CONTAINMENT_SHARE * loss:
        return "contained"
    return "anchor"


def cluster(signals: Iterable[Signal]) -> list[Cluster]:
    """Greedy, impact-ranked clustering (D25) with parent-dilution correction (D77)."""
    ordered = sorted(signals, key=lambda signal: signal.lost_approvals, reverse=True)
    verdicts = {id(signal): _anchor_verdict(signal, ordered) for signal in ordered}
    # A `split` parent is dropped outright: it is two incidents wearing one cell, and
    # registry derives burn rate and blast radius from the anchor alone, so folding it
    # into either child would inflate that child's money.  A `contained` parent stays
    # as evidence but may not anchor -- its child is the honest cell to alert on.
    remaining = [signal for signal in ordered if verdicts[id(signal)] != "split"]
    result: list[Cluster] = []
    while remaining:
        # Fall back to the largest remaining signal when every candidate is demoted, so
        # a parent whose children were absorbed elsewhere is never silently dropped.
        anchor = next((signal for signal in remaining if verdicts[id(signal)] == "anchor"), remaining[0])
        members = [anchor]
        unrelated = []
        for signal in remaining:
            if signal is anchor:
                continue
            if lattice_related(signal.cell, anchor.cell):
                members.append(signal)
            else:
                unrelated.append(signal)
        remaining = unrelated
        result.append(Cluster(anchor=anchor, members=tuple(members)))
    result.sort(key=lambda candidate: candidate.anchor.lost_approvals, reverse=True)
    return result
