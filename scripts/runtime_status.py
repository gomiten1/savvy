"""Shared, atomic runtime heartbeats for the single-machine Fly process."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import fcntl


DEFAULT_STATUS_FILE = "data/runtime-status.json"


def status_path() -> Path:
    return Path(os.environ.get("RUNTIME_STATUS_FILE", DEFAULT_STATUS_FILE))


def read_status(path: str | Path | None = None) -> dict:
    try:
        return json.loads(Path(path or status_path()).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


@contextmanager
def _locked_status_file(target: Path):
    """Serialize read-modify-write updates from the two worker processes."""
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def update_status(path: str | Path | None = None, **values) -> dict:
    """Merge values into the status file without exposing process secrets."""
    target = Path(path or status_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with _locked_status_file(target):
        payload = read_status(target)
        payload.update(values)
        payload["updated_at"] = time.time()
        with tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False, encoding="utf-8") as tmp:
            json.dump(payload, tmp, sort_keys=True)
            tmp.write("\n")
            temporary = Path(tmp.name)
        temporary.replace(target)
    return payload
