"""
La implementación real de la Sección 4 de DATA-CONTRACT.md:

    get_counts(start_ts, end_ts, bucket, group_by[], filters{})
      -> rows de (bucket_ts, *group_by keys, attempts, approved, declined, error, amount_usd_total)

    get_samples(start_ts, end_ts, filters{}, limit)
      -> raw attempt rows (para citar evidencia en la alerta)

"We poll; we do not consume a stream" -- ambas son funciones Python
comunes que corren SQL parametrizado contra `data/gold/`. No hay servidor,
no hay stream: quien consume las llama cuando quiere.

Dos motores (ver docs/decision_log.md, "Gold layer: Parquet + DuckDB", y
pipeline/gold/schema.py para el porqué): la porción histórica vive en
`historical.duckdb` (Parquet-backed views, se abre siempre con
`read_only=True` -- nunca hay un writer conviviendo, así que cualquier
número de lectores concurrentes anda bien); la porción en vivo vive en
`live.sqlite` (SQLite sí soporta lector+escritor concurrentes, que es
justo lo que necesita mientras el Generador B está escribiendo).

Cómo se resuelve un rango de tiempo:
  - la parte que cae ANTES de `history_end` (meta, en historical.duckdb) se
    sirve desde las tablas precalculadas (rate_cells_minutely /
    decline_cells_hourly) — rápido, pero decline_code solo a resolución
    horaria (no se guardó más fino para no explotar el conteo de filas).
  - la parte que cae DESPUÉS de `history_end` se sirve desde `live_attempts`
    (Generador B, grain real de 1 fila = 1 intento) -- ahí SÍ hay total
    flexibilidad, incluyendo minute-level + decline_code juntos, porque son
    filas reales, no un agregado precalculado.
  - si el rango pisa las dos, se consultan ambas fuentes y se concatena.
    OJO: el bucket que cae justo en el límite puede aparecer como DOS filas
    (una por fuente) en vez de una sola sumada -- para un demo de
    hackathon no vale la pena el merge fino; quien consuma puede sumarlas
    si le importa un bucket exacto en el borde.

get_samples() solo tiene sentido contra live_attempts (evidencia real fila
por fila) -- sobre el histórico puro no hay filas individuales guardadas
(ver brief: "NO generar 31.5M filas individuales"), así que devuelve lista
vacía para esa porción en vez de inventar datos.
"""
import sqlite3
from pathlib import Path

import duckdb

from pipeline.gold.schema import (
    GROUP_BY_DIMENSIONS,
    SAMPLE_FILTER_FIELDS,
    HISTORICAL_DB_FILENAME,
    LIVE_DB_FILENAME,
)
from pipeline.generator.weights import ERROR_STATUS_CANONICAL_CODES

GOLD_DIR = Path(__file__).resolve().parents[2] / "data" / "gold"

_VALID_BUCKETS = ("minute", "hour", "day")


def _rows_as_dicts(cursor):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _bucket_sql(bucket: str, time_col: str) -> str:
    if bucket == "day":
        return f"substr({time_col}, 1, 10) || 'T00:00:00Z'"
    if bucket == "hour":
        return f"substr({time_col}, 1, 13) || ':00:00Z'"
    return f"substr({time_col}, 1, 16) || ':00Z'"


def _validate(group_by, filters, allowed_filter_fields=GROUP_BY_DIMENSIONS):
    if bad := set(group_by) - set(GROUP_BY_DIMENSIONS):
        raise ValueError(f"group_by inválido: {bad}. Debe ser subset de {GROUP_BY_DIMENSIONS}")
    if bad := set(filters) - set(allowed_filter_fields):
        raise ValueError(f"filters inválido: {bad}. Debe ser subset de {allowed_filter_fields}")


def _read_history_end(hist_conn):
    row = hist_conn.execute("SELECT value FROM meta WHERE key='history_end'").fetchone()
    return row[0] if row else None


def _where(time_col, start_ts, end_ts, filters):
    clauses = [f"{time_col} >= ?", f"{time_col} < ?"]
    params = [start_ts, end_ts]
    for k, v in filters.items():
        clauses.append(f"{k} = ?")
        params.append(v)
    return " AND ".join(clauses), params


def _query_rate_cells(conn, start_ts, end_ts, bucket, group_by, filters):
    bucket_expr = _bucket_sql(bucket, "time_bucket")
    where_sql, params = _where("time_bucket", start_ts, end_ts, filters)
    dims_sql = "".join(f", {d}" for d in group_by)
    sql = f"""
        SELECT {bucket_expr} AS bucket_ts{dims_sql},
               SUM(attempts) AS attempts, SUM(approved) AS approved,
               SUM(declined) AS declined, SUM(error) AS error,
               SUM(amount_usd_total) AS amount_usd_total
        FROM rate_cells_minutely
        WHERE {where_sql}
        GROUP BY bucket_ts{dims_sql}
        ORDER BY bucket_ts
    """
    return _rows_as_dicts(conn.execute(sql, params))


def _query_decline_cells(conn, start_ts, end_ts, bucket, group_by, filters):
    # decline_cells_hourly no tiene resolución más fina que la hora -- si
    # piden "minute" igual devuelve buckets de hora (documentado arriba).
    effective_bucket = "day" if bucket == "day" else "hour"
    bucket_expr = _bucket_sql(effective_bucket, "hour_bucket")
    group_by_no_code = [d for d in group_by if d != "decline_code"]
    where_sql, params = _where("hour_bucket", start_ts, end_ts, filters)
    dims_sql = "".join(f", {d}" for d in group_by_no_code)
    # decline_code viaja siempre en el SELECT (aunque no se haya pedido
    # como group_by) para poder clasificar declined vs error por fila.
    sql = f"""
        SELECT {bucket_expr} AS bucket_ts{dims_sql}, decline_code,
               SUM(declines) AS declines, SUM(amount_usd_total) AS amount_usd_total
        FROM decline_cells_hourly
        WHERE {where_sql}
        GROUP BY bucket_ts{dims_sql}, decline_code
        ORDER BY bucket_ts
    """
    rows = []
    for row in _rows_as_dicts(conn.execute(sql, params)):
        declines = row.pop("declines")
        is_error = row["decline_code"] in ERROR_STATUS_CANONICAL_CODES
        if "decline_code" not in group_by:
            row.pop("decline_code")
        row["attempts"] = declines
        row["approved"] = 0
        row["declined"] = 0 if is_error else declines
        row["error"] = declines if is_error else 0
        rows.append(row)
    return rows


def _query_live_counts(conn, start_ts, end_ts, bucket, group_by, filters):
    bucket_expr = _bucket_sql(bucket, "event_ts")
    where_sql, params = _where("event_ts", start_ts, end_ts, filters)
    dims_sql = "".join(f", {d}" for d in group_by)
    sql = f"""
        SELECT {bucket_expr} AS bucket_ts{dims_sql},
               COUNT(*) AS attempts,
               SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN status='declined' THEN 1 ELSE 0 END) AS declined,
               SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error,
               COALESCE(SUM(amount_usd), 0) AS amount_usd_total
        FROM live_attempts
        WHERE {where_sql}
        GROUP BY bucket_ts{dims_sql}
        ORDER BY bucket_ts
    """
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params)]


def get_counts(start_ts: str, end_ts: str, bucket: str = "minute", group_by=(), filters: dict = None, gold_dir=GOLD_DIR):
    """start_ts/end_ts: ISO 'YYYY-MM-DDTHH:MM:SSZ'. bucket: minute|hour|day.
    group_by: subset de GROUP_BY_DIMENSIONS. filters: {dim: valor} equality-only."""
    filters = filters or {}
    gold_dir = Path(gold_dir)
    if bucket not in _VALID_BUCKETS:
        raise ValueError(f"bucket debe ser uno de {_VALID_BUCKETS}")
    _validate(group_by, filters)

    hist_path = gold_dir / HISTORICAL_DB_FILENAME
    live_path = gold_dir / LIVE_DB_FILENAME
    rows = []

    hist_conn = duckdb.connect(str(hist_path), read_only=True) if hist_path.exists() else None
    try:
        history_end = _read_history_end(hist_conn) if hist_conn else None

        hist_end = min(end_ts, history_end) if history_end else end_ts
        if hist_conn and start_ts < hist_end:
            if "decline_code" in group_by or "decline_code" in filters:
                rows += _query_decline_cells(hist_conn, start_ts, hist_end, bucket, group_by, filters)
            else:
                rows += _query_rate_cells(hist_conn, start_ts, hist_end, bucket, group_by, filters)
    finally:
        if hist_conn:
            hist_conn.close()

    if live_path.exists() and (history_end is None or end_ts > history_end):
        live_start = max(start_ts, history_end) if history_end else start_ts
        live_conn = sqlite3.connect(str(live_path))
        try:
            rows += _query_live_counts(live_conn, live_start, end_ts, bucket, group_by, filters)
        finally:
            live_conn.close()

    return rows


def get_samples(start_ts: str, end_ts: str, filters: dict = None, limit: int = 50, gold_dir=GOLD_DIR):
    """Filas de intento crudas para citar evidencia. Solo existen para la
    ventana en vivo (live_attempts) -- el histórico precalculado no guarda
    filas individuales por diseño (ver módulo docstring)."""
    filters = filters or {}
    gold_dir = Path(gold_dir)
    _validate((), filters, allowed_filter_fields=SAMPLE_FILTER_FIELDS)

    live_path = gold_dir / LIVE_DB_FILENAME
    if not live_path.exists():
        return []

    conn = sqlite3.connect(str(live_path))
    conn.row_factory = sqlite3.Row
    try:
        where_sql, params = _where("event_ts", start_ts, end_ts, filters)
        sql = f"""
            SELECT attempt_id, payment_id, attempt_number, event_ts, merchant_id,
                   provider_id, method, country, issuing_bank, status,
                   decline_code, amount_minor, currency, amount_usd
            FROM live_attempts
            WHERE {where_sql}
            ORDER BY event_ts DESC
            LIMIT ?
        """
        params = params + [limit]
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
