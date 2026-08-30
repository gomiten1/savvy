#!/usr/bin/env python3
"""Health endpoint backed by generator and detector heartbeats."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.runtime_status import read_status


STATUS_FILE = "/data/runtime-status.json"
MAX_HEARTBEAT_AGE_SECONDS = 60


def health_payload(status_file: str = STATUS_FILE, max_age_seconds: int = MAX_HEARTBEAT_AGE_SECONDS):
    status = read_status(status_file)
    now = time.time()
    missing_or_stale = [
        name for name in ("generator_last_write", "detector_last_scan")
        if not isinstance(status.get(name), (int, float)) or now - status[name] > max_age_seconds
    ]
    healthy = status.get("state") == "ready" and not missing_or_stale
    return healthy, {
        "status": "ok" if healthy else "unhealthy",
        "state": status.get("state", "unknown"),
        "missing_or_stale": missing_or_stale,
    }


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - HTTP verb name is stdlib convention
        if self.path != "/healthz":
            self.send_error(404)
            return
        healthy, payload = health_payload(
            getattr(self.server, "status_file", STATUS_FILE),
            getattr(self.server, "max_heartbeat_age_seconds", MAX_HEARTBEAT_AGE_SECONDS),
        )
        body = (json.dumps(payload) + "\n").encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return  # Fly's health probes do not need to fill application logs.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-file", default=STATUS_FILE)
    parser.add_argument("--max-heartbeat-age-seconds", type=int, default=MAX_HEARTBEAT_AGE_SECONDS)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), HealthHandler)
    server.status_file = args.status_file
    server.max_heartbeat_age_seconds = args.max_heartbeat_age_seconds
    print("[boot] health server listening on 8080")
    server.serve_forever()
