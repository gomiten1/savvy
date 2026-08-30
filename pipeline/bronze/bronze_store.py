"""
Bronze: store append-only trivial. No valida nada — esa es la
responsabilidad de Silver. Un registro Bronze es:
    {vendor, payload, routing_metadata, bronze_id, ingested_at}

`payload` es el body EXACTO devuelto por el vendor (shape verificado, sin
campos inventados). `routing_metadata` es el contexto que el orquestador ya
conoce ANTES de llamar al vendor (a qué payment_method/país/merchant se
enrutó, attempt_number, linked_order_id) — ningún shape de vendor de los 4
verificados trae payment_method, merchant_id ni linked_order_id, así que esa
info viaja como metadata propia del orquestador, no parseada del vendor. Ver
docs/decision_log.md.
"""
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "bronze"
DEFAULT_PATH = DATA_DIR / "events.jsonl"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024


class BronzeStore:
    def __init__(self, path: Path = DEFAULT_PATH, max_bytes: int = DEFAULT_MAX_BYTES):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        """Keep one previous bronze segment; Bronze is not read by the live path."""
        if self.max_bytes <= 0 or not self.path.exists():
            return
        if self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        rotated = self.path.with_name(f"{self.path.name}.1")
        os.replace(self.path, rotated)

    def append(self, vendor: str, payload: dict, routing_metadata: dict = None) -> dict:
        record = {
            "bronze_id": str(uuid.uuid4()),
            "vendor": vendor,
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "payload": payload,
            "routing_metadata": routing_metadata or {},
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._rotate_if_needed(len(line.encode("utf-8")) + 1)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return record

    def append_many(self, records: list) -> list:
        """Igual que append() pero para varios registros en un solo write —
        usado por el Generador B para no pagar el costo de abrir/flushear el
        archivo por cada transacción individual a ~333 txns/seg."""
        built = []
        lines = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for vendor, payload, routing_metadata in records:
            record = {
                "bronze_id": str(uuid.uuid4()),
                "vendor": vendor,
                "ingested_at": now,
                "payload": payload,
                "routing_metadata": routing_metadata or {},
            }
            built.append(record)
            lines.append(json.dumps(record, ensure_ascii=False))
        if not lines:
            return built
        with self._lock:
            self._rotate_if_needed(sum(len(line.encode("utf-8")) + 1 for line in lines))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return built

    def read_all(self):
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def clear(self):
        with self._lock:
            if self.path.exists():
                self.path.unlink()
