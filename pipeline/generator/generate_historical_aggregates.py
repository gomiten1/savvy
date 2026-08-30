"""
Generador A — histórico precalculado.

Genera 14 días de agregados por minuto x celda dimensional (NO eventos
individuales — ver docs/decision_log.md y la nota de volumen en el brief).
Se corre UNA sola vez y el resultado se guarda como Parquet + un
`historical.duckdb` de solo lectura (`data/gold/`) — el resto del pipeline
(baseline.py, tests, demo, Gold) solo lee esos archivos.

El histórico se genera LIMPIO (sin incidentes inyectados) porque es la base
con la que se aprende expected_rate/expected_std por celda — un incidente
ahí contaminaría el baseline que se supone debe detectarlo. Sí incluye
estacionalidad (día de semana, hora del día, un evento estacional) porque
esa es variación NORMAL que el baseline debe aprender a esperar.

Escribe directo a las tablas del Gold layer (pipeline/gold/schema.py):
  - rate_cells_minutely: grain minuto x (merchant, provider, country,
    method, bank) — SIN decline_code (explotaría el conteo de filas: ver
    decision_log.md). Approved/declined/error como columnas separadas.
  - decline_cells_hourly: grain hora x (merchant, provider, country,
    method, bank, decline_code) — acá sí se abre por código, pero a
    resolución horaria para mantener el volumen de filas manejable.

Uso:
    python -m pipeline.generator.generate_historical_aggregates
"""
import argparse
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.domain.weights import (
    BASE_TXNS_PER_MINUTE,
    HISTORICAL_DAYS,
    CANONICAL_DECLINE_WEIGHTS,
    APPROVAL_RATE,
    MIN_SAMPLE_SIZE_PER_CELL,
    MERCHANT_WEIGHTS,
    ERROR_STATUS_CANONICAL_CODES,
    FX_RATE_TO_USD,
    AMOUNT_RANGE_BY_COUNTRY,
    RECOVERY_RATE_BY_CANONICAL_CODE,
    CURRENCY_BY_COUNTRY,
)
from pipeline.domain.seasonality import (
    volume_multiplier,
    minute_of_day,
    to_utc_iso,
)
from pipeline.generator.sampling import enumerate_rate_cells, cell_id, poisson_sample, apportion
from pipeline.gold.schema import GOLD_DIRNAME, HISTORICAL_DB_FILENAME, RATE_PARQUET_FILENAME, DECLINE_PARQUET_FILENAME
from pipeline.gold.materialize import open_scratch_build_conn, finalize_historical, insert_rate_rows, insert_decline_rows

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GOLD_DIR = DATA_DIR / GOLD_DIRNAME

# ASUNCIÓN: un solo evento estacional activado dentro de la ventana de 14
# días, para ejercitar el código de estacionalidad y sostener la demo de
# "pico estacional != anomalía" (el pico solo mueve volumen/attempts, la
# approval rate no se toca -> el detector de rate no debe alertar por esto).
SEASONAL_EVENT_DAY_OFFSET = 9  # día 10 de los 14 (0-indexado)
SEASONAL_EVENT_NAME = "black_friday_mx"


def active_seasonal_events(dt: datetime, history_start: datetime):
    day_offset = (dt.date() - history_start.date()).days
    if day_offset == SEASONAL_EVENT_DAY_OFFSET:
        return [SEASONAL_EVENT_NAME]
    return []


_CANONICAL_DECLINE_WEIGHTS_TOTAL = sum(CANONICAL_DECLINE_WEIGHTS.values())
# NOTA: CANONICAL_DECLINE_WEIGHTS del brief suma 1.08, no 1.0 (se usa "tal
# cual", no se tocan los valores) — se normaliza acá solo para poder
# repartir `declines` como una distribución de probabilidad válida que sume
# exacto al total. Ver docs/decision_log.md.
_NORMALIZED_DECLINE_WEIGHTS = {
    c: w / _CANONICAL_DECLINE_WEIGHTS_TOTAL for c, w in CANONICAL_DECLINE_WEIGHTS.items()
}


def distribute_declines(declines: int, rng: random.Random):
    """Reparte `declines` entre los 9 códigos canónicos según
    CANONICAL_DECLINE_WEIGHTS (normalizado) usando remanente-mayor
    (apportion), no aleatorio — el aleatorio ya pasó al samplear `declines`
    en sí (Poisson sobre attempts x (1-approval_rate))."""
    if declines <= 0:
        return {}
    counts = apportion(declines, _NORMALIZED_DECLINE_WEIGHTS)
    return {c: n for c, n in counts.items() if n > 0}


def _avg_amount_usd(country: str) -> float:
    lo, hi = AMOUNT_RANGE_BY_COUNTRY[country]
    avg_major = (lo + hi) / 2
    return avg_major * FX_RATE_TO_USD[CURRENCY_BY_COUNTRY[country]]


def build_database(history_start: datetime, history_end: datetime, seed: int, base_txns_per_minute: int, gold_dir: Path = None):
    gold_dir = gold_dir or GOLD_DIR
    gold_dir.mkdir(parents=True, exist_ok=True)
    # rebuild limpio: borra cualquier artefacto Gold previo, incluyendo
    # live.sqlite -- correr el Generador A resetea todo el mundo, incluida
    # data de una demo anterior (mismo comportamiento que antes).
    for fname in (RATE_PARQUET_FILENAME, DECLINE_PARQUET_FILENAME, HISTORICAL_DB_FILENAME, "live.sqlite"):
        (gold_dir / fname).unlink(missing_ok=True)
    conn, scratch_path = open_scratch_build_conn(gold_dir)  # SQLite temporal; ver finalize_historical() al final

    rng = random.Random(seed)
    rate_cells = enumerate_rate_cells()
    merchants = list(MERCHANT_WEIGHTS)
    avg_amount_usd_by_country = {c: _avg_amount_usd(c) for c in AMOUNT_RANGE_BY_COUNTRY}

    total_minutes = int((history_end - history_start).total_seconds() // 60)
    # (hour_iso, merchant, provider, country, method, bank, code) -> declines
    decline_acc = {}
    cell_total_attempts = {c.key: 0 for c in rate_cells}

    rate_buffer = []
    FLUSH_EVERY = 1440 * 5  # ~5 días de filas por flush

    t0 = time.time()
    for minute in range(total_minutes):
        dt = history_start + timedelta(minutes=minute)
        events = active_seasonal_events(dt, history_start)
        hour_bucket = dt.replace(minute=0, second=0, microsecond=0)
        hour_iso = to_utc_iso(hour_bucket)
        m_of_day = minute_of_day(dt)
        weekday = dt.weekday()
        time_bucket_iso = to_utc_iso(dt)

        for cell in rate_cells:
            vol_mult = volume_multiplier(dt, cell.country, active_events=events)
            mean_attempts = base_txns_per_minute * cell.weight * vol_mult
            if mean_attempts <= 0:
                continue
            attempts = poisson_sample(rng, mean_attempts)
            if attempts == 0:
                continue

            base_rate = APPROVAL_RATE[cell.provider]
            # ruido natural por minuto (~2 puntos de std) -> alimenta el
            # expected_std que aprende baseline.py; MIN_STD_FLOOR está
            # calibrado a esta magnitud.
            effective_rate = min(1.0, max(0.0, rng.gauss(base_rate, 0.02)))
            approved = min(attempts, max(0, round(attempts * effective_rate)))
            declines = attempts - approved

            by_code = distribute_declines(declines, rng) if declines > 0 else {}
            error_total = sum(n for c, n in by_code.items() if c in ERROR_STATUS_CANONICAL_CODES)
            declined_total = declines - error_total

            cid = cell_id(cell.provider, cell.country, cell.payment_method, cell.issuing_bank)
            amount_usd_avg = avg_amount_usd_by_country[cell.country]

            # Reparto DETERMINÍSTICO por merchant (apportion, no un nuevo
            # sampleo por merchant -- 6x el trabajo de random ya sería
            # notorio en tiempo de generación). Ver decision_log.md.
            attempts_by_merchant = apportion(attempts, MERCHANT_WEIGHTS)
            declined_by_merchant = apportion(declined_total, MERCHANT_WEIGHTS)
            error_by_merchant = apportion(error_total, MERCHANT_WEIGHTS)

            for merchant in merchants:
                m_attempts = attempts_by_merchant[merchant]
                if m_attempts == 0:
                    continue
                m_declined = declined_by_merchant[merchant]
                m_error = error_by_merchant[merchant]
                # salvaguarda: el apportion de cada componente redondea por
                # separado, así que en casos raros declined+error podría
                # superar por 1 a attempts para un merchant puntual.
                if m_declined + m_error > m_attempts:
                    overflow = (m_declined + m_error) - m_attempts
                    m_declined = max(0, m_declined - overflow)
                m_approved = m_attempts - m_declined - m_error

                rate_buffer.append(
                    {
                        "time_bucket": time_bucket_iso, "minute_of_day": m_of_day,
                        "weekday": weekday, "merchant_id": merchant,
                        "provider_id": cell.provider, "country": cell.country,
                        "method": cell.payment_method, "issuing_bank": cell.issuing_bank,
                        "cell_id": cid, "attempts": m_attempts, "approved": m_approved,
                        "declined": m_declined, "error": m_error,
                        "amount_usd_total": round(m_attempts * amount_usd_avg, 2),
                    }
                )
            cell_total_attempts[cell.key] += attempts

            if by_code:
                code_merchant_splits = {code: apportion(n, MERCHANT_WEIGHTS) for code, n in by_code.items()}
                for code, per_merchant in code_merchant_splits.items():
                    for merchant, n in per_merchant.items():
                        if n == 0:
                            continue
                        key = (hour_iso, merchant, cell.provider, cell.country, cell.payment_method, cell.issuing_bank, code)
                        decline_acc[key] = decline_acc.get(key, 0) + n

        if len(rate_buffer) >= FLUSH_EVERY:
            insert_rate_rows(conn, rate_buffer)
            conn.commit()
            rate_buffer.clear()

    if rate_buffer:
        insert_rate_rows(conn, rate_buffer)
        conn.commit()

    # segunda pasada: resolver recovered a partir de declines acumulados por hora
    decline_rows = []
    for (hour_iso, merchant, provider, country, method, bank, code), declines in decline_acc.items():
        expected = RECOVERY_RATE_BY_CANONICAL_CODE.get(code, 0.0)
        observed_rate = min(1.0, max(0.0, rng.gauss(expected, 0.05)))
        recovered = min(declines, max(0, round(declines * observed_rate)))
        cid = cell_id(provider, country, method, code)
        amount_usd_total = round(declines * avg_amount_usd_by_country[country], 2)
        decline_rows.append(
            {
                "hour_bucket": hour_iso, "merchant_id": merchant, "provider_id": provider,
                "country": country, "method": method, "issuing_bank": bank,
                "decline_code": code, "cell_id": cid, "declines": declines,
                "recovered": recovered, "amount_usd_total": amount_usd_total,
            }
        )
    insert_decline_rows(conn, decline_rows)
    conn.commit()

    # COPY rate/decline a Parquet, cierra la conexión scratch, abre
    # historical.duckdb con los VIEWs sobre esos parquet + meta ya creados.
    final_conn = finalize_historical(conn, scratch_path, gold_dir)

    meta = {
        "history_start": to_utc_iso(history_start),
        "history_end": to_utc_iso(history_end),
        "base_txns_per_minute": str(base_txns_per_minute),
        "seed": str(seed),
        "generated_at": to_utc_iso(datetime.now(timezone.utc)),
        "generation_seconds": f"{time.time() - t0:.1f}",
    }
    final_conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(meta.items()))

    smallest_cell = min(cell_total_attempts.items(), key=lambda kv: kv[1])
    print(f"[generate_historical_aggregates] {total_minutes} minutos x {len(rate_cells)} rate cells x {len(merchants)} merchants")
    print(f"[generate_historical_aggregates] tiempo de generación: {meta['generation_seconds']}s")
    print(
        f"[generate_historical_aggregates] celda más chica (sin split por merchant): {smallest_cell[0]} "
        f"-> {smallest_cell[1]} attempts totales (umbral MIN_SAMPLE_SIZE_PER_CELL={MIN_SAMPLE_SIZE_PER_CELL})"
    )
    final_conn.close()
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=HISTORICAL_DAYS)
    parser.add_argument(
        "--txns-per-minute", type=int, default=BASE_TXNS_PER_MINUTE, dest="txns_per_minute"
    )
    args = parser.parse_args()

    history_end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    history_start = history_end - timedelta(days=args.days)
    build_database(history_start, history_end, args.seed, args.txns_per_minute)


if __name__ == "__main__":
    main()
