from __future__ import annotations

import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock

from scripts.health_server import HealthHandler, health_payload
from scripts.runtime_status import update_status


class HealthServerTests(unittest.TestCase):
    def handler(self, path: str):
        handler = Mock()
        handler.path, handler.wfile = path, BytesIO()
        handler.server.status_file = "/definitely-missing-runtime-status.json"
        handler.server.max_heartbeat_age_seconds = 60
        return handler

    def test_healthz_requires_fresh_worker_heartbeats(self) -> None:
        with TemporaryDirectory() as directory:
            status_file = f"{directory}/runtime-status.json"
            update_status(status_file, state="ready", generator_last_write=time.time(), detector_last_scan=time.time())
            healthy, payload = health_payload(status_file)
        self.assertTrue(healthy)
        self.assertEqual("ok", payload["status"])

    def test_healthz_returns_unhealthy_before_workers_are_ready(self) -> None:
        handler = self.handler("/healthz")
        HealthHandler.do_GET(handler)
        handler.send_response.assert_called_once_with(503)

    def test_unknown_path_returns_not_found(self) -> None:
        handler = self.handler("/")
        HealthHandler.do_GET(handler)
        handler.send_error.assert_called_once_with(404)


if __name__ == "__main__":
    unittest.main()
