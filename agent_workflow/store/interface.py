"""Only interface the detector and agent use to read payment attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol, Sequence


# Public detector names map to the pipeline's gold-layer column names.
DIMENSION_COLUMNS = {
    "merchant": "merchant_id",
    "provider": "provider_id",
    "method": "method",
    "country": "country",
    "issuing_bank": "issuing_bank",
}


@dataclass(frozen=True)
class CountRow:
    bucket_ts: datetime
    dimensions: tuple[str | None, ...]
    attempts: int
    approved: int
    declined: int
    error: int
    amount_usd_total: float


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    payment_id: str
    attempt_number: int
    event_ts: datetime
    merchant_id: str
    provider_id: str
    method: str
    country: str
    issuing_bank: str | None
    status: str
    decline_code: str | None
    amount_minor: int
    currency: str
    amount_usd: float


class AttemptStore(Protocol):
    def get_counts(
        self,
        start_ts: datetime,
        end_ts: datetime,
        bucket: int | timedelta,
        group_by: Sequence[str],
        filters: Mapping[str, str | None],
    ) -> list[CountRow]:
        """Return closed, equality-filtered attempt aggregates."""

    def get_samples(
        self,
        start_ts: datetime,
        end_ts: datetime,
        filters: Mapping[str, str | None],
        limit: int,
    ) -> list[Attempt]:
        """Return raw attempts for diagnosis evidence."""

