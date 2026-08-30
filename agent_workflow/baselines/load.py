"""Read-only baseline lookup with seasonal, all-time, and parent fallbacks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from agent_workflow.config import MIN_HISTORY_OBSERVATIONS


def _key(cell: Mapping[str, str | None]) -> str:
    return "|".join(f"{name}={value if value is not None else '<null>'}" for name, value in cell.items())


@dataclass(frozen=True)
class Baseline:
    rate: float
    dispersion: float
    source: str


class BaselineLookup:
    def __init__(self, artifact: Mapping) -> None:
        self._artifact = artifact
        self._cells = artifact["cells"]
        self._minimum = artifact.get("min_history_observations", MIN_HISTORY_OBSERVATIONS)

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "BaselineLookup":
        directory = Path(data_dir)
        filename = (directory / "baselines_current").read_text().strip()
        return cls(json.loads((directory / filename).read_text()))

    def get(self, cell: Mapping[str, str | None], ts: datetime) -> Baseline | None:
        record = self._cells.get(_key(cell))
        if record:
            hour = str(ts.weekday() * 24 + ts.hour)
            if record["hour_of_week_observations"].get(hour, 0) >= self._minimum:
                return Baseline(record["hour_of_week_rates"][hour], record["dispersion"], "hour_of_week")
            return Baseline(record["all_time_rate"], record["dispersion"], "all_time")
        if len(cell) > 1:
            parent = dict(cell)
            parent.pop(next(reversed(parent)))
            result = self.get(parent, ts)
            if result:
                return Baseline(result.rate, result.dispersion, "inherited_from_parent")
        return None
