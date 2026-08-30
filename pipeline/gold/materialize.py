"""
Escritores del Gold layer.

- `open_scratch_build_conn`/`insert_rate_rows`/`insert_decline_rows`/
  `finalize_historical`: usados por `generate_historical_aggregates.py`
  (Generador A) para acumular el histórico.

  IMPORTANTE (ver docs/decision_log.md, "por qué el scratch de generación
  es SQLite y no DuckDB"): la conexión scratch es **SQLite**, no DuckDB.
  Se probó (benchmark real, no supuesto): `executemany()` de DuckDB hace
  ~2.800 filas/seg vía su binding de Python, ~700x más lento que SQLite
  (~2M filas/seg) para el mismo patrón de inserción — a esa velocidad
  cargar 5.7M filas tardaría media hora. DuckDB SÍ es rapidísimo para
  volcados masivos vectorizados (esto no cambia la conclusión del resto
  del diseño: Parquet para lo grande, SQLite para lo chico) -- lo que no
  es rápido es alimentarlo fila a fila desde Python. Entonces: se acumula
  en un SQLite temporal (rápido, igual que `live_attempts`), y recién al
  final DuckDB hace UN solo `COPY` masivo desde ese SQLite a Parquet, vía
  su extensión `sqlite_scanner` (`ATTACH ... (TYPE sqlite)`) — eso sí es
  vectorizado y rápido (~1-2seg por millón de filas).

- `GoldWriter`: usado por `generate_live_stream.py` (Generador B) para
  insertar cada fila normalizada del stream en vivo a `live_attempts`
  (SQLite -- ver schema.py para por qué SQLite y no DuckDB acá también)
  tick a tick, en tiempo real.
"""
import sqlite3
from pathlib import Path

import duckdb

from pipeline.gold.schema import (
    create_bulk_build_tables,
    create_historical_views,
    create_live_tables,
    RATE_PARQUET_FILENAME,
    DECLINE_PARQUET_FILENAME,
    HISTORICAL_DB_FILENAME,
)

SCRATCH_FILENAME = "_scratch_build.sqlite"

RATE_COLUMNS = (
    "time_bucket", "minute_of_day", "weekday", "merchant_id", "provider_id",
    "country", "method", "issuing_bank", "cell_id", "attempts", "approved",
    "declined", "error", "amount_usd_total",
)
DECLINE_COLUMNS = (
    "hour_bucket", "merchant_id", "provider_id", "country", "method",
    "issuing_bank", "decline_code", "cell_id", "declines", "recovered",
    "amount_usd_total",
)
LIVE_COLUMNS = (
    "attempt_id", "payment_id", "attempt_number", "event_ts", "merchant_id",
    "provider_id", "method", "country", "issuing_bank", "status",
    "decline_code", "amount_minor", "currency", "amount_usd",
)


# ---------------------------------------------------------------------------
# Lado histórico: acumula rápido en SQLite, exporta a Parquet vía DuckDB.
# ---------------------------------------------------------------------------
def open_scratch_build_conn(gold_dir: Path):
    """Conexión SQLite (archivo temporal en gold_dir) para acumular el
    histórico durante la generación -- no es el artefacto final, se
    exporta a Parquet y se borra (ver finalize_historical). El DDL de
    schema.py está escrito en SQL genérico (tipos DOUBLE/TEXT/INTEGER)
    que SQLite acepta igual que DuckDB vía type affinity, así que no hace
    falta un DDL separado por motor acá."""
    gold_dir.mkdir(parents=True, exist_ok=True)
    scratch_path = gold_dir / SCRATCH_FILENAME
    scratch_path.unlink(missing_ok=True)
    conn = sqlite3.connect(scratch_path)
    create_bulk_build_tables(conn)
    conn.commit()
    return conn, scratch_path


def insert_rate_rows(conn, rows: list):
    """rows: dicts keyed by RATE_COLUMNS names -- el orden posicional para
    el INSERT lo decide esta función a partir de RATE_COLUMNS, no el
    caller, así que un solo lugar declara el orden de columnas."""
    if not rows:
        return
    placeholders = ",".join("?" * len(RATE_COLUMNS))
    tuples = [tuple(row[c] for c in RATE_COLUMNS) for row in rows]
    conn.executemany(f"INSERT INTO rate_cells_minutely VALUES ({placeholders})", tuples)


def insert_decline_rows(conn, rows: list):
    """rows: dicts keyed by DECLINE_COLUMNS names -- ver insert_rate_rows."""
    if not rows:
        return
    placeholders = ",".join("?" * len(DECLINE_COLUMNS))
    tuples = [tuple(row[c] for c in DECLINE_COLUMNS) for row in rows]
    conn.executemany(f"INSERT INTO decline_cells_hourly VALUES ({placeholders})", tuples)


def finalize_historical(scratch_conn, scratch_path: Path, gold_dir: Path):
    """Cierra la conexión SQLite scratch, usa DuckDB (extensión
    sqlite_scanner) para hacer UN COPY masivo y vectorizado de cada tabla
    a Parquet, borra el scratch, y devuelve una conexión NUEVA a
    historical.duckdb (con los VIEWs sobre esos parquet + la tabla meta ya
    creados) para que el caller escriba meta y la cierre."""
    scratch_conn.commit()
    scratch_conn.close()

    rate_path = gold_dir / RATE_PARQUET_FILENAME
    decline_path = gold_dir / DECLINE_PARQUET_FILENAME

    export_conn = duckdb.connect(":memory:")
    export_conn.execute("INSTALL sqlite; LOAD sqlite;")
    export_conn.execute(f"ATTACH '{scratch_path}' AS scratch (TYPE sqlite)")
    export_conn.execute(f"COPY scratch.rate_cells_minutely TO '{rate_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    export_conn.execute(f"COPY scratch.decline_cells_hourly TO '{decline_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    export_conn.close()
    scratch_path.unlink(missing_ok=True)

    final_conn = duckdb.connect(str(gold_dir / HISTORICAL_DB_FILENAME))
    create_historical_views(final_conn, gold_dir)
    return final_conn


# ---------------------------------------------------------------------------
# Lado en vivo (SQLite, Generador B) -- ver schema.py para el porqué.
# ---------------------------------------------------------------------------
class GoldWriter:
    """Traduce filas normalizadas de Silver (nombres internos: provider,
    payment_method, canonical_decline_code, linked_order_id, amount...) a
    la nomenclatura del contrato (provider_id, method, decline_code,
    payment_id, amount_minor/currency/amount_usd) e inserta en
    `live_attempts` (SQLite). Es la única escritura fila-a-fila del
    pipeline — el volumen del stream en vivo (~300/seg) lo permite; el
    histórico jamás pasa por acá."""

    def __init__(self, db_path=None, conn: sqlite3.Connection = None):
        if conn is None and db_path is not None:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = conn or sqlite3.connect(db_path)
        create_live_tables(self.conn)

    def write_live_batch(self, silver_rows: list):
        records = []
        for row in silver_rows:
            if row is None:
                continue
            amount = row.get("amount")
            amount_minor = int(round(amount * 100)) if amount is not None else None
            attempt_id = f"{row['provider']}:{row.get('_native_id')}"
            named = {
                "attempt_id": attempt_id,
                "payment_id": row.get("linked_order_id"),
                "attempt_number": row.get("attempt_number", 1),
                "event_ts": row.get("event_ts") or row["time_bucket"],
                "merchant_id": row.get("merchant_id"),
                "provider_id": row["provider"],
                "method": row.get("payment_method"),
                "country": row.get("country"),
                "issuing_bank": row.get("issuing_bank"),
                "status": row["status"],
                "decline_code": row.get("canonical_decline_code"),
                "amount_minor": amount_minor,
                "currency": row.get("currency"),
                "amount_usd": row.get("amount_usd"),
            }
            records.append(tuple(named[c] for c in LIVE_COLUMNS))
        if not records:
            return
        placeholders = ",".join("?" * len(LIVE_COLUMNS))
        self.conn.executemany(
            f"INSERT OR IGNORE INTO live_attempts VALUES ({placeholders})", records
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
