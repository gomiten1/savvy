#!/usr/bin/env python3
"""Tiny liveness endpoint for the single-machine Fly deployment."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - HTTP verb name is stdlib convention
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = b'{"status":"ok"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return  # Fly's health probes do not need to fill application logs.


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()
