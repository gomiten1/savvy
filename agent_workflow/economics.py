"""Single source of truth for the deterministic incident money figures."""

from __future__ import annotations

from agent_workflow.config import RETRY_RECOVERY_RATE, WINDOW_SECONDS
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_workflow.detect.scan import Signal


COST_BASIS = "gross" if RETRY_RECOVERY_RATE == 0 else "net_of_retry_recovery"


def blast_radius(signal: Signal) -> float:
    """Incremental failure share of the anchor cell's own traffic."""
    return max(0.0, signal.baseline_rate - signal.observed_rate)


def burn_rate_usd_hour(signal: Signal) -> float:
    """Estimate lost approval value per hour from one detector window."""
    if signal.attempts == 0:
        return 0.0
    average_amount = signal.amount_usd_total / signal.attempts
    lost_per_hour = signal.lost_approvals * (3600 / WINDOW_SECONDS)
    return lost_per_hour * average_amount * (1 - RETRY_RECOVERY_RATE)
