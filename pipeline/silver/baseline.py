"""
Silver — baseline: expected_rate/expected_std por (minuto_del_día, celda),
aprendido del histórico del Generador A (data/gold/historical.duckdb,
Parquet-backed), y deviation_index (z-score con piso mínimo de std) +
nivel de confianza.

Decisión de diseño (ver docs/decision_log.md): el baseline se agrupa por
minuto-del-día PERO pooleando los 14 días juntos (sin separar por weekday).
La estacionalidad semanal (WEEKDAY_INDEX) mueve VOLUMEN, no approval rate en
este generador, así que la tasa esperada no depende del día de la semana —
poolear da 14 muestras por minuto-del-día en vez de ~2, mucho mejor para
estimar std con tan poca historia.

Escala de confianza (los 4 valores del schema canónico):
  insufficient_history  -> menos de MIN_DAYS_FOR_DAILY_PATTERN días de historia
  insufficient_sample   -> celda con < MIN_SAMPLE_SIZE_PER_CELL attempts acumulados
  wide_band              -> >= MIN_DAYS_FOR_DAILY_PATTERN pero < MIN_DAYS_FOR_WEEKLY_PATTERN días
  reliable                -> >= MIN_DAYS_FOR_WEEKLY_PATTERN días
DAYS_UNTIL_BAND_IS_TIGHT (28) no agrega un 5to nivel de confianza (el schema
solo define esos 4) — se usa para seguir angostando el std dentro de
"reliable" a medida que se acumula historia más allá de los 14 días que
genera este demo (con 14 días fijos, siempre queda en el extremo ancho de
"reliable").
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

import duckdb

from pipeline.domain.weights import (
    MIN_DAYS_FOR_DAILY_PATTERN,
    MIN_DAYS_FOR_WEEKLY_PATTERN,
    DAYS_UNTIL_BAND_IS_TIGHT,
    MIN_SAMPLE_SIZE_PER_CELL,
    MIN_STD_FLOOR,
    WIDE_BAND_STD_MULTIPLIER,
    APPROVAL_RATE,
)
from pipeline.gold.schema import HISTORICAL_DB_FILENAME
from pipeline.gold.queries import fetch_rate_cells_pooled_by_minute

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "gold" / HISTORICAL_DB_FILENAME


@dataclass
class CellStats:
    days_available: int
    total_attempts: int
    rate_samples: list  # una tasa (actual_rate) por día, para ese minuto_of_day


def classify_confidence(days_available: int, total_attempts: int) -> str:
    if days_available < MIN_DAYS_FOR_DAILY_PATTERN:
        return "insufficient_history"
    if total_attempts < MIN_SAMPLE_SIZE_PER_CELL:
        return "insufficient_sample"
    if days_available < MIN_DAYS_FOR_WEEKLY_PATTERN:
        return "wide_band"
    return "reliable"


def band_multiplier(days_available: int) -> float:
    """1.5x de ancho en MIN_DAYS_FOR_WEEKLY_PATTERN, angostando linealmente
    hasta 1.0x en DAYS_UNTIL_BAND_IS_TIGHT."""
    if days_available <= MIN_DAYS_FOR_WEEKLY_PATTERN:
        return WIDE_BAND_STD_MULTIPLIER
    if days_available >= DAYS_UNTIL_BAND_IS_TIGHT:
        return 1.0
    span = DAYS_UNTIL_BAND_IS_TIGHT - MIN_DAYS_FOR_WEEKLY_PATTERN
    progress = (days_available - MIN_DAYS_FOR_WEEKLY_PATTERN) / span
    return WIDE_BAND_STD_MULTIPLIER - progress * (WIDE_BAND_STD_MULTIPLIER - 1.0)


class BaselineStore:
    """Carga rate_cells_minutely (Gold layer) de SQLite, pooleando across
    merchants, y precalcula por (cell_id, minute_of_day) la media/std de
    actual_rate a través de los días disponibles + el total de attempts
    acumulado de la celda (para el check de MIN_SAMPLE_SIZE_PER_CELL)."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self._by_minute = {}  # (cell_id, minute_of_day) -> CellStats
        self._cell_total_attempts = defaultdict(int)
        self._cell_days_seen = defaultdict(set)
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} no existe — corré "
                "`python -m pipeline.generator.generate_historical_aggregates` primero"
            )
        conn = duckdb.connect(str(self.db_path), read_only=True)
        rows_by_key = defaultdict(list)
        # rate_cells_minutely tiene grain (celda x minuto x merchant) — para
        # el baseline de conversion rate se poolea across merchants acá
        # mismo en SQL (merchant no es dimensión del baseline, ver
        # decision_log.md), sumando attempts/approved por (cell_id, minuto).
        # SQL compartida con pipeline/gold/queries.py -- ver ese módulo.
        for time_bucket, minute_of_day, cell_id, attempts, approved in fetch_rate_cells_pooled_by_minute(conn):
            day = time_bucket[:10]
            key = (cell_id, minute_of_day)
            rate = approved / attempts if attempts else 0.0
            rows_by_key[key].append(rate)
            self._cell_total_attempts[cell_id] += attempts
            self._cell_days_seen[cell_id].add(day)
        conn.close()

        for key, rates in rows_by_key.items():
            cell_id, _minute = key
            self._by_minute[key] = CellStats(
                days_available=len(self._cell_days_seen[cell_id]),
                total_attempts=self._cell_total_attempts[cell_id],
                rate_samples=rates,
            )
        self._loaded = True

    def stats_for(self, cell_id: str, minute_of_day: int, provider: str = None):
        self.load()
        key = (cell_id, minute_of_day)
        stats = self._by_minute.get(key)
        if stats is None or not stats.rate_samples:
            fallback_rate = APPROVAL_RATE.get(provider, 0.8)
            return {
                "expected_rate": fallback_rate,
                "expected_std": MIN_STD_FLOOR,
                "days_available": 0,
                "total_attempts": self._cell_total_attempts.get(cell_id, 0),
            }
        expected_rate = mean(stats.rate_samples)
        expected_std = pstdev(stats.rate_samples) if len(stats.rate_samples) > 1 else 0.0
        return {
            "expected_rate": round(expected_rate, 4),
            "expected_std": round(expected_std, 4),
            "days_available": stats.days_available,
            "total_attempts": stats.total_attempts,
        }

    def score(self, cell_id: str, minute_of_day: int, attempts: int, approvals: int, provider: str = None) -> dict:
        stats = self.stats_for(cell_id, minute_of_day, provider=provider)
        actual_rate = approvals / attempts if attempts else 0.0
        confidence = classify_confidence(stats["days_available"], stats["total_attempts"])
        mult = band_multiplier(stats["days_available"]) if confidence == "reliable" else WIDE_BAND_STD_MULTIPLIER
        effective_std = max(stats["expected_std"] * mult, MIN_STD_FLOOR)
        deviation_index = (actual_rate - stats["expected_rate"]) / effective_std
        return {
            "actual_rate": round(actual_rate, 4),
            "expected_rate": stats["expected_rate"],
            "expected_std": stats["expected_std"],
            "deviation_index": round(deviation_index, 4),
            "confidence": confidence,
        }


def score_row(store: BaselineStore, cell_id: str, minute_of_day: int, attempts: int, approvals: int, provider: str = None) -> dict:
    return store.score(cell_id, minute_of_day, attempts, approvals, provider=provider)
