"""Lanza la DuckDB UI local (localhost:4213), con el histórico Y la data
en vivo (si existe) attacheados como dos catálogos separados, para
explorar/visualizar todo el Gold layer sin escribir Python. Correr con
.venv/bin/python3.

  hist.rate_cells_minutely / hist.decline_cells_hourly   -- histórico (Parquet)
  live.live_attempts                                      -- stream en vivo (SQLite)
"""
import time
from pathlib import Path

import duckdb

GOLD_DIR = Path(__file__).resolve().parents[1] / "data" / "gold"
HIST_PATH = GOLD_DIR / "historical.duckdb"
LIVE_PATH = GOLD_DIR / "live.sqlite"

# La UI necesita su propio catálogo interno (para guardar tabs/historial de
# queries) -> se conecta a :memory: y cada fuente real se ATTACHea aparte,
# en modo read_only, en vez de abrirlas directo.
conn = duckdb.connect(":memory:")
conn.execute("INSTALL ui; LOAD ui;")

attached = []
if HIST_PATH.exists():
    conn.execute(f"ATTACH '{HIST_PATH}' AS hist (READ_ONLY)")
    attached.append(f"hist -> {HIST_PATH} (rate_cells_minutely, decline_cells_hourly, meta)")

if LIVE_PATH.exists():
    conn.execute("INSTALL sqlite; LOAD sqlite;")
    conn.execute(f"ATTACH '{LIVE_PATH}' AS live (TYPE sqlite, READ_ONLY)")
    attached.append(f"live -> {LIVE_PATH} (live_attempts)")

conn.execute("CALL start_ui();")
print("DuckDB UI corriendo en http://localhost:4213 (todo read-only, no escribe nada)")
for line in attached:
    print(f"  attached: {line}")
if not attached:
    print("  (nada attacheado todavía -- corré el Generador A y/o B primero)")
print("Ctrl+C para parar.")

try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    conn.close()
