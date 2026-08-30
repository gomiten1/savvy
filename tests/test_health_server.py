from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import Mock

from scripts.health_server import HealthHandler


class HealthServerTests(unittest.TestCase):
    def handler(self, path: str):
        handler = Mock(spec=HealthHandler)
        handler.path, handler.wfile = path, BytesIO()
        return handler

    def test_healthz_returns_ok(self) -> None:
        handler = self.handler("/healthz")
        HealthHandler.do_GET(handler)
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(b'{"status":"ok"}\n', handler.wfile.getvalue())

    def test_unknown_path_returns_not_found(self) -> None:
        handler = self.handler("/")
        HealthHandler.do_GET(handler)
        handler.send_error.assert_called_once_with(404)


if __name__ == "__main__":
    unittest.main()
