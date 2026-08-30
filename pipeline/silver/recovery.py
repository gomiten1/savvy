"""
Recovery tracking: cuántos de los pagos declinados se recuperan en un
reintento posterior (attempt_number 2 o 3, mismo linked_order_id), y cómo se
compara esa tasa observada contra RECOVERY_RATE_BY_CANONICAL_CODE.

Dos consumidores:
  - RecoveryTracker: stream de eventos individuales en vivo (Generador B /
    normalize.py), donde attempt_number/linked_order_id existen por evento.
  - recovery_deviation()/recovery_confidence(): funciones puras reusadas por
    el Generador A al post-procesar sus agregados horarios (que ya conocen
    declines/recovered directamente, sin necesitar trackear order_id a
    order_id).
"""
from collections import Counter

from pipeline.generator.weights import (
    RECOVERY_RATE_BY_CANONICAL_CODE,
    MAX_ATTEMPTS_PER_TRANSACTION,
    MIN_SAMPLE_SIZE_PER_CELL,
)


def expected_recovery_rate(canonical_decline_code: str):
    return RECOVERY_RATE_BY_CANONICAL_CODE.get(canonical_decline_code)


def recovery_deviation(observed_rate, canonical_decline_code: str):
    """Diferencia simple observado - esperado (no z-score: el brief pide
    comparar contra la constante RECOVERY_RATE_BY_CANONICAL_CODE, no contra
    un baseline histórico aprendido como en baseline.py)."""
    expected = expected_recovery_rate(canonical_decline_code)
    if observed_rate is None or expected is None:
        return None
    return round(observed_rate - expected, 4)


def recovery_confidence(sample_size: int) -> str:
    if sample_size < MIN_SAMPLE_SIZE_PER_CELL:
        return "insufficient_sample"
    return "reliable"


class RecoveryTracker:
    """Trackea attempt_number/linked_order_id a través de un stream de
    eventos individuales (grain: un evento por intento de pago)."""

    def __init__(self):
        self._pending = {}  # linked_order_id -> (cell_key, canonical_decline_code)
        self._opportunities = Counter()  # (cell_key, canonical_decline_code) -> int
        self._recovered = Counter()

    def record(self, *, linked_order_id, attempt_number, status, cell_key, canonical_decline_code):
        if not linked_order_id:
            return
        if status == "declined":
            if linked_order_id not in self._pending:
                self._opportunities[(cell_key, canonical_decline_code)] += 1
                self._pending[linked_order_id] = (cell_key, canonical_decline_code)
            if attempt_number >= MAX_ATTEMPTS_PER_TRANSACTION:
                self._pending.pop(linked_order_id, None)  # se agotaron los reintentos, no recuperado
        elif status == "approved":
            pending = self._pending.pop(linked_order_id, None)
            if pending is not None:
                self._recovered[pending] += 1

    def snapshot(self, cell_key, canonical_decline_code):
        key = (cell_key, canonical_decline_code)
        opportunities = self._opportunities.get(key, 0)
        recovered = self._recovered.get(key, 0)
        observed_rate = recovered / opportunities if opportunities else None
        return {
            "attempts": opportunities,
            "approvals": recovered,
            "recovery_rate": round(observed_rate, 4) if observed_rate is not None else None,
            "expected_rate": expected_recovery_rate(canonical_decline_code),
            "recovery_rate_deviation": recovery_deviation(observed_rate, canonical_decline_code),
            "confidence": recovery_confidence(opportunities),
        }

    def all_keys(self):
        return set(self._opportunities) | set(self._recovered)
