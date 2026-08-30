"""
Schema del Gold layer — la superficie real del contrato con
detection/diagnosis (DATA-CONTRACT.md).

Dos motores, cada uno donde encaja (ver docs/decision_log.md, sección
"Gold layer: Parquet + DuckDB"):

  - Las dos tablas grandes, precalculadas UNA vez y de solo lectura para
    siempre después (rate_cells_minutely: ~5.7M filas; decline_cells_hourly:
    ~150K filas) -> Parquet + DuckDB. Esto es lo que de verdad es "mucha
    data" para un workload OLAP -- columnar, comprimido, se lee rápido con
    cualquier herramienta estándar (DuckDB, pandas, Spark), no solo desde
    este código.
  - La tabla chica, escrita fila a fila EN VIVO durante la demo mientras el
    equipo de detección probablemente la está leyendo en paralelo desde
    OTRO proceso (exactamente el escenario de trial-by-fire) ->
    **SQLite**, no DuckDB. Se probó empíricamente (ver decision_log.md):
    DuckDB no soporta lector+escritor concurrentes sobre un mismo archivo
    -- un solo proceso puede tener el archivo abierto, punto, ni siquiera
    en modo read_only si hay otro connection (de escritura o no) abierta.
    SQLite sí soporta ese patrón (para eso existe su locking), así que la
    tabla que necesita ese patrón se queda en SQLite.

Layout en disco (`data/gold/`):
    rate_cells_minutely.parquet     # Generador A, COPY una vez
    decline_cells_hourly.parquet    # Generador A, COPY una vez
    historical.duckdb               # meta + 2 VIEWs sobre los parquet de
                                     # arriba. Escrito una vez por el
                                     # Generador A, de solo lectura después
                                     # -- múltiples lectores concurrentes
                                     # (read_only=True) andan bien porque
                                     # nunca hay un writer conviviendo.
    live.sqlite                     # live_attempts. Un writer (GoldWriter,
                                     # todo el Generador B) + N lectores
                                     # concurrentes (get_counts/get_samples
                                     # desde otro proceso) -- el patrón que
                                     # SQLite soporta nativo.
"""
import sqlite3
from pathlib import Path

GOLD_DIRNAME = "gold"
RATE_PARQUET_FILENAME = "rate_cells_minutely.parquet"
DECLINE_PARQUET_FILENAME = "decline_cells_hourly.parquet"
HISTORICAL_DB_FILENAME = "historical.duckdb"
LIVE_DB_FILENAME = "live.sqlite"

# ---------------------------------------------------------------------------
# DuckDB: DDL de la conexión scratch usada SOLO durante la generación
# histórica (Generador A). No es el artefacto final -- eso son los VIEWs
# creados por create_historical_views() después del COPY a parquet.
# ---------------------------------------------------------------------------
DUCKDB_RATE_CELLS_MINUTELY_DDL = """
CREATE TABLE rate_cells_minutely (
    time_bucket TEXT NOT NULL,
    minute_of_day INTEGER NOT NULL,
    weekday INTEGER NOT NULL,
    merchant_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    country TEXT NOT NULL,
    method TEXT NOT NULL,
    issuing_bank TEXT NOT NULL,
    cell_id TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    approved INTEGER NOT NULL,
    declined INTEGER NOT NULL,
    error INTEGER NOT NULL,
    amount_usd_total DOUBLE NOT NULL
);
"""

DUCKDB_DECLINE_CELLS_HOURLY_DDL = """
CREATE TABLE decline_cells_hourly (
    hour_bucket TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    country TEXT NOT NULL,
    method TEXT NOT NULL,
    issuing_bank TEXT NOT NULL,
    decline_code TEXT NOT NULL,
    cell_id TEXT NOT NULL,
    declines INTEGER NOT NULL,
    recovered INTEGER NOT NULL,
    amount_usd_total DOUBLE NOT NULL
);
"""

DUCKDB_META_DDL = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"


def create_bulk_build_tables(conn):
    """Contra la conexión DuckDB scratch (en memoria) del Generador A."""
    conn.execute(DUCKDB_RATE_CELLS_MINUTELY_DDL)
    conn.execute(DUCKDB_DECLINE_CELLS_HOURLY_DDL)


def create_historical_views(conn, gold_dir: Path):
    """Contra historical.duckdb, DESPUÉS de que los parquet ya existen en
    disco (si no, el VIEW apuntaría a un archivo inexistente)."""
    conn.execute(
        f"CREATE OR REPLACE VIEW rate_cells_minutely AS "
        f"SELECT * FROM read_parquet('{gold_dir / RATE_PARQUET_FILENAME}')"
    )
    conn.execute(
        f"CREATE OR REPLACE VIEW decline_cells_hourly AS "
        f"SELECT * FROM read_parquet('{gold_dir / DECLINE_PARQUET_FILENAME}')"
    )
    conn.execute(DUCKDB_META_DDL)


# ---------------------------------------------------------------------------
# SQLite: live_attempts, la única tabla que en verdad necesita
# lector+escritor concurrentes.
# ---------------------------------------------------------------------------
SQLITE_LIVE_ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS live_attempts (
    attempt_id TEXT PRIMARY KEY,
    payment_id TEXT,
    attempt_number INTEGER NOT NULL,
    event_ts TEXT NOT NULL,
    merchant_id TEXT,
    provider_id TEXT NOT NULL,
    method TEXT,
    country TEXT,
    issuing_bank TEXT,
    status TEXT NOT NULL,
    decline_code TEXT,
    amount_minor INTEGER,
    currency TEXT,
    amount_usd REAL
);
"""

SQLITE_LIVE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_live_attempts_ts ON live_attempts(event_ts)",
    "CREATE INDEX IF NOT EXISTS idx_live_attempts_payment ON live_attempts(payment_id)",
]


def create_live_tables(conn: sqlite3.Connection):
    conn.execute(SQLITE_LIVE_ATTEMPTS_DDL)
    for stmt in SQLITE_LIVE_INDEXES:
        conn.execute(stmt)
    conn.commit()


GROUP_BY_DIMENSIONS = ("merchant_id", "provider_id", "country", "method", "issuing_bank", "decline_code")

# get_samples() filtra sobre filas reales de live_attempts, no sobre un
# agregado -- puede aceptar más columnas que las 6 dimensiones de group_by
# (ej. status, payment_id) sin que eso rompa la simetría con get_counts().
SAMPLE_FILTER_FIELDS = GROUP_BY_DIMENSIONS + ("status", "payment_id", "attempt_number")
