#!/usr/bin/env python3
"""Query el Gold layer desde la terminal, sin abrir Python a mano.

Uso:
    .venv/bin/python3 scripts/query.py "SELECT * FROM hist.rate_cells_minutely LIMIT 5"
    .venv/bin/python3 scripts/query.py "SELECT status, COUNT(*) FROM live.live_attempts GROUP BY status"

`hist.*` = histórico (rate_cells_minutely, decline_cells_hourly, meta) -- read-only.
`live.*` = stream en vivo (live_attempts) -- read-only acá también (este script
solo lee, nunca escribe).
"""
import sys
from pathlib import Path

import duckdb

GOLD_DIR = Path(__file__).resolve().parents[1] / "data" / "gold"
HIST_PATH = GOLD_DIR / "historical.duckdb"
LIVE_PATH = GOLD_DIR / "live.sqlite"


def main():
    if len(sys.argv) < 2:
        print('Uso: query.py "SELECT ... FROM hist.rate_cells_minutely ..."')
        sys.exit(1)
    sql = sys.argv[1]

    conn = duckdb.connect(":memory:")
    attached = []
    if HIST_PATH.exists():
        conn.execute(f"ATTACH '{HIST_PATH}' AS hist (READ_ONLY)")
        attached.append("hist")
    if LIVE_PATH.exists():
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        conn.execute(f"ATTACH '{LIVE_PATH}' AS live (TYPE sqlite, READ_ONLY)")
        attached.append("live")

    if not attached:
        print(f"Nada en {GOLD_DIR} todavía -- corré el Generador A primero.")
        sys.exit(1)

    conn.sql(sql).show(max_width=180, max_rows=50)
    conn.close()


if __name__ == "__main__":
    main()
