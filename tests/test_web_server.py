from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline.generator.generate_live_stream import LiveStreamGenerator
import pipeline.generator.generate_live_stream as live_stream
from scripts.web_server import validate_injection, write_injection


class InjectionValidationTests(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "provider": "mercadopago",
            "country": "BR",
            "payment_method": "pix",
            "issuing_bank": "itau",
            "approval_rate_multiplier": 0.3,
            "duration_minutes": 60,
            "dominant_decline_code": "91_96_network_timeout",
        }

    def test_valid_payload_is_converted_to_generator_trigger(self) -> None:
        trigger = validate_injection(self.valid_payload())
        self.assertEqual("mercadopago", trigger["cell_filter"]["provider"])
        self.assertEqual("itau", trigger["cell_filter"]["issuing_bank"])
        self.assertEqual(0.3, trigger["approval_rate_multiplier"])

    def test_rejects_invalid_route_and_unsafe_values(self) -> None:
        invalid_method = self.valid_payload() | {"payment_method": "oxxo", "country": "BR"}
        with self.assertRaisesRegex(ValueError, "payment method"):
            validate_injection(invalid_method)
        invalid_multiplier = self.valid_payload() | {"approval_rate_multiplier": 1.0}
        with self.assertRaisesRegex(ValueError, "multiplier"):
            validate_injection(invalid_multiplier)
        invalid_bank = self.valid_payload() | {"provider": "stripe"}
        with self.assertRaisesRegex(ValueError, "Issuing-bank"):
            validate_injection(invalid_bank)

    def test_only_one_pending_injection_can_be_queued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trigger_path = Path(directory) / "incident_trigger.json"
            payload = validate_injection(self.valid_payload())
            write_injection(trigger_path, payload)
            self.assertEqual(payload, json.loads(trigger_path.read_text()))
            with self.assertRaises(FileExistsError):
                write_injection(trigger_path, payload)

    def test_rejects_an_in_flight_injection_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trigger_path = Path(directory) / "incident_trigger.json"
            reservation = trigger_path.with_name(".incident_trigger.json.lock")
            reservation.touch()
            with self.assertRaises(FileExistsError):
                write_injection(trigger_path, validate_injection(self.valid_payload()))

    def test_generator_consumes_the_api_trigger_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trigger_path = Path(directory) / "incident_trigger.json"
            write_injection(trigger_path, validate_injection(self.valid_payload()))
            generator = object.__new__(LiveStreamGenerator)
            generator.incidents = []
            generator.sim_now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
            generator.reveal_injections = False
            original_trigger_file = live_stream.TRIGGER_FILE
            try:
                live_stream.TRIGGER_FILE = trigger_path
                generator._check_trigger_file()
            finally:
                live_stream.TRIGGER_FILE = original_trigger_file
            self.assertFalse(trigger_path.exists())
            self.assertEqual(1, len(generator.incidents))
            self.assertEqual("mercadopago", generator.incidents[0].cell_filter["provider"])


if __name__ == "__main__":
    unittest.main()
