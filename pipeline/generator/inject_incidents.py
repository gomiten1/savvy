"""
Reloj de incidentes: modela una caída de approval rate en una celda durante
una ventana de tiempo, con duración tomada de MTTR_RANGE_MINUTES.

Diseño: el histórico de 14 días del Generador A se guarda LIMPIO (sin
incidentes) porque es la base con la que se aprende expected_rate/std — un
incidente ahí contaminaría el baseline. Los incidentes se inyectan en:
  1. El Generador B (stream en vivo / demo), vía `active_incidents`.
  2. Los tests, para probar que la detección funciona (ver
     pipeline/tests/test_baseline.py y test_generator.py).
"""
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pipeline.generator.weights import MTTR_RANGE_MINUTES


@dataclass
class Incident:
    name: str
    start: datetime
    duration_minutes: int
    approval_rate_multiplier: float  # ej. 0.35 = cae a 35% de la tasa normal
    cell_filter: dict = field(default_factory=dict)  # subset de {provider,country,payment_method,issuing_bank}
    dominant_decline_code: str = None  # opcional: sesga los declines inyectados a este código

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)

    def matches_cell(self, cell_dims: dict) -> bool:
        return all(cell_dims.get(k) == v for k, v in self.cell_filter.items())

    def is_active_at(self, dt: datetime) -> bool:
        return self.start <= dt < self.end


def random_duration_minutes(rng: random.Random = None) -> int:
    rng = rng or random
    lo, hi = MTTR_RANGE_MINUTES
    return rng.randint(lo, hi)


def active_incidents(dt: datetime, cell_dims: dict, incidents: list) -> list:
    return [inc for inc in incidents if inc.is_active_at(dt) and inc.matches_cell(cell_dims)]


def effective_approval_rate(base_rate: float, dt: datetime, cell_dims: dict, incidents: list) -> float:
    """Aplica el producto de los multiplicadores de todos los incidentes
    activos para esta celda/momento. Piso 0.01 para no llegar a 0 exacto."""
    active = active_incidents(dt, cell_dims, incidents)
    if not active:
        return base_rate
    rate = base_rate
    for inc in active:
        rate *= inc.approval_rate_multiplier
    return max(0.01, min(1.0, rate))


def dominant_decline_code_override(dt: datetime, cell_dims: dict, incidents: list):
    """Si algún incidente activo especifica un código dominante, lo devuelve
    (el último que matchea gana). None si ninguno lo especifica."""
    code = None
    for inc in active_incidents(dt, cell_dims, incidents):
        if inc.dominant_decline_code:
            code = inc.dominant_decline_code
    return code
