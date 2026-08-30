"""
Sink de quarantine: cualquier registro Bronze que no se pudo normalizar
(vendor desconocido, payload malformado, campo faltante) aterriza acá con
el motivo — nunca se descarta en silencio y nunca crashea el pipeline.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "silver"
DEFAULT_PATH = DATA_DIR / "quarantine.jsonl"


class Quarantine:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, bronze_record: dict, reason: str):
        entry = {
            "quarantined_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "reason": reason,
            "bronze_record": bronze_record,
        }
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_all(self):
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def clear(self):
        with self._lock:
            if self.path.exists():
                self.path.unlink()
