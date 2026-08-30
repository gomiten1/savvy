"""
Estacionalidad semanal + diaria + picos estacionales.

Todo esto es NORMAL (no anomalía) — se aplica como multiplicador de volumen
al generar el histórico y el stream en vivo. La tasa de aprobación (approval
rate) NO se ve afectada por estacionalidad, solo el volumen de intentos.
"""
from datetime import datetime, timezone

# Estacionalidad semanal (e-commerce general).
# ASUNCIÓN declarada por el brief: se invierte en verticales de impulso/ocio
# (fin de semana sube en vez de bajar) — no relevante para este dataset.
WEEKDAY_INDEX = {0: 1.023, 1: 1.136, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.875, 6: 0.875}
# 0=lunes ... 6=domingo (datetime.weekday())

# Picos estacionales (inyectados como parte del histórico normal, no como
# incidentes). country=None => aplica a todos los países.
SEASONAL_EVENTS = {
    "buen_fin_mx": {"multiplier": 2.0, "country": "MX"},
    "black_friday_mx": {"multiplier": 1.67, "country": "MX"},
    "black_friday_br": {"multiplier": 4.0, "country": "BR"},
    "holiday_season": {"multiplier": 1.4, "country": None},
}


def hourly_multiplier(hour: int) -> float:
    """Pico 20-23h x1.5 | pico secundario 11-14h x1.2 | valle 2-6h x0.10 | resto x0.7"""
    if 20 <= hour <= 23:
        return 1.5
    if 11 <= hour <= 14:
        return 1.2
    if 2 <= hour <= 6:
        return 0.10
    return 0.7


def weekday_multiplier(weekday: int) -> float:
    return WEEKDAY_INDEX[weekday]


def seasonal_event_multiplier(dt: datetime, country: str, active_events=None) -> float:
    """Multiplica por cualquier evento estacional activo en `active_events`
    (lista de nombres en SEASONAL_EVENTS) que aplique a `country`."""
    if not active_events:
        return 1.0
    mult = 1.0
    for name in active_events:
        ev = SEASONAL_EVENTS.get(name)
        if ev is None:
            continue
        if ev["country"] is None or ev["country"] == country:
            mult *= ev["multiplier"]
    return mult


def volume_multiplier(dt: datetime, country: str, active_events=None) -> float:
    """Multiplicador total de volumen para un minuto dado: día-de-semana x
    hora-del-día x eventos estacionales activos."""
    return (
        weekday_multiplier(dt.weekday())
        * hourly_multiplier(dt.hour)
        * seasonal_event_multiplier(dt, country, active_events)
    )


def minute_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
