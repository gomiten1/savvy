#!/usr/bin/env python3
"""Return success only after the live SQLite writer has committed a row."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def live_db_ready(path: str | Path) -> bool:
    db_path = Path(path)
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            conn.execute("PRAGMA busy_timeout=2000")
            row = conn.execute("SELECT MAX(event_ts) FROM live_attempts").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as error:
        print(f"[boot] live database not ready: {error}", file=sys.stderr)
        return False
    return bool(row and row[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/gold/live.sqlite")
    args = parser.parse_args()
    raise SystemExit(0 if live_db_ready(args.path) else 1)


if __name__ == "__main__":
    main()
