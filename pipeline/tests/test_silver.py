"""
Tests de Silver: los 4 parsers contra los shapes reales verificados del
brief, dedup, y que el dispatcher (normalize.py) nunca crashea ante:
  - vendor desconocido -> quarantine
  - campo malformado / faltante -> quarantine, no excepción
  - decline code desconocido -> canonical_decline_code = "unknown"
  - duplicado (mismo ID nativo) -> se descarta silenciosamente
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline.silver.parsers import parse, extract_native_id, PARSERS
from pipeline.silver.dedup import Deduper
from pipeline.silver.quarantine import Quarantine
from pipeline.silver.normalize import normalize_bronze_record, normalize_batch
from pipeline.bronze.bronze_store import BronzeStore

# Payloads EXACTOS de los ejemplos verificados en el brief.
STRIPE_EXAMPLE = {
    "id": "ch_test123",
    "amount": 192700,
    "currency": "mxn",
    "status": "failed",
    "created": 1660177644,
    "failure_code": "insufficient_funds",
}
ADYEN_EXAMPLE = {
    "live": "false",
    "notificationItems": [
        {
            "NotificationRequestItem": {
                "eventCode": "AUTHORISATION",
                "success": "false",
                "eventDate": "2026-08-29T14:32:00+02:00",
                "pspReference": "psp_test_123",
                "amount": {"value": 19270, "currency": "MXN"},
                "reason": "ExpiredCard",
            }
        }
    ],
}
MERCADOPAGO_EXAMPLE = {
    "id": 47198050,
    "status": "rejected",
    "status_detail": "cc_rejected_insufficient_amount",
    "transaction_amount": 700.50,
    "issuer_id": 25,
    "site_id": "MLM",
}
DLOCAL_EXAMPLE = {
    "id": "D-4-abc123",
    "amount": 72.00,
    "status": "PAID",
    "status_code": "200",
    "currency": "USD",
    "country": "AR",
    "payment_method_type": "TICKET",
}


class TempStoresMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.quarantine = Quarantine(path=self.tmpdir / "quarantine.jsonl")
        self.deduper = Deduper()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class ParserTest(unittest.TestCase):
    def test_stripe_example(self):
        parsed = parse("stripe", STRIPE_EXAMPLE)
        self.assertEqual(parsed["status"], "declined")
        self.assertEqual(parsed["amount"], 1927.00)  # centavos -> unidades mayores
        self.assertEqual(parsed["canonical_decline_code"], "51_insufficient_funds")
        self.assertEqual(parsed["parsed_country"], "MX")
        self.assertEqual(parsed["native_id"], "ch_test123")

    def test_adyen_example(self):
        parsed = parse("adyen", ADYEN_EXAMPLE)
        self.assertEqual(parsed["status"], "declined")
        self.assertEqual(parsed["amount"], 192.70)
        self.assertEqual(parsed["canonical_decline_code"], "54_expired_card")
        self.assertEqual(parsed["parsed_country"], "MX")
        self.assertEqual(parsed["native_id"], "psp_test_123")

    def test_mercadopago_example(self):
        parsed = parse("mercadopago", MERCADOPAGO_EXAMPLE)
        self.assertEqual(parsed["status"], "declined")
        self.assertEqual(parsed["amount"], 700.50)
        self.assertEqual(parsed["canonical_decline_code"], "51_insufficient_funds")
        self.assertEqual(parsed["parsed_country"], "MX")
        self.assertEqual(parsed["issuer_id"], 25)

    def test_dlocal_example(self):
        parsed = parse("dlocal", DLOCAL_EXAMPLE)
        self.assertEqual(parsed["status"], "approved")
        self.assertEqual(parsed["amount"], 72.00)
        self.assertIsNone(parsed["canonical_decline_code"])
        self.assertEqual(parsed["parsed_country"], "AR")

    def test_all_four_vendors_covered(self):
        self.assertEqual(set(PARSERS.keys()), {"stripe", "adyen", "mercadopago", "dlocal"})

    def test_extract_native_id_all_vendors(self):
        self.assertEqual(extract_native_id("stripe", STRIPE_EXAMPLE), "ch_test123")
        self.assertEqual(extract_native_id("adyen", ADYEN_EXAMPLE), "psp_test_123")
        self.assertEqual(extract_native_id("mercadopago", MERCADOPAGO_EXAMPLE), 47198050)
        self.assertEqual(extract_native_id("dlocal", DLOCAL_EXAMPLE), "D-4-abc123")

    def test_unknown_decline_code_becomes_unknown(self):
        payload = dict(STRIPE_EXAMPLE, failure_code="some_brand_new_code_stripe_added")
        parsed = parse("stripe", payload)
        self.assertEqual(parsed["canonical_decline_code"], "unknown")

    def test_malformed_status_raises(self):
        with self.assertRaises(ValueError):
            parse("stripe", dict(STRIPE_EXAMPLE, status="weird_status"))

    def test_missing_required_field_raises(self):
        broken = dict(STRIPE_EXAMPLE)
        del broken["id"]
        with self.assertRaises(KeyError):
            parse("stripe", broken)


class DedupTest(unittest.TestCase):
    def test_second_occurrence_is_duplicate(self):
        deduper = Deduper()
        self.assertFalse(deduper.is_duplicate("stripe", STRIPE_EXAMPLE))
        self.assertTrue(deduper.is_duplicate("stripe", STRIPE_EXAMPLE))

    def test_different_ids_not_duplicate(self):
        deduper = Deduper()
        self.assertFalse(deduper.is_duplicate("stripe", STRIPE_EXAMPLE))
        other = dict(STRIPE_EXAMPLE, id="ch_other")
        self.assertFalse(deduper.is_duplicate("stripe", other))

    def test_malformed_payload_is_not_deduped_here(self):
        deduper = Deduper()
        self.assertFalse(deduper.is_duplicate("stripe", {"nothing": "useful"}))
        self.assertFalse(deduper.is_duplicate("stripe", {"nothing": "useful"}))


class NormalizeNeverCrashesTest(TempStoresMixin, unittest.TestCase):
    def _bronze(self, vendor, payload, routing=None):
        return {
            "bronze_id": "b1",
            "vendor": vendor,
            "ingested_at": "2026-08-29T14:32:00.000000Z",
            "payload": payload,
            "routing_metadata": routing or {},
        }

    def test_unknown_vendor_goes_to_quarantine(self):
        record = self._bronze("some_new_vendor_xyz", {"id": "1", "status": "failed"})
        row = normalize_bronze_record(record, self.deduper, self.quarantine)
        self.assertIsNone(row)
        entries = self.quarantine.read_all()
        self.assertEqual(len(entries), 1)
        self.assertIn("unknown vendor", entries[0]["reason"])

    def test_malformed_field_goes_to_quarantine_not_crash(self):
        record = self._bronze("stripe", {"id": "ch_x", "amount": "not_a_number", "currency": "mxn", "status": "failed", "created": 1660177644})
        row = normalize_bronze_record(record, self.deduper, self.quarantine)
        self.assertIsNone(row)
        self.assertEqual(len(self.quarantine.read_all()), 1)

    def test_missing_payload_goes_to_quarantine(self):
        record = self._bronze("stripe", None)
        row = normalize_bronze_record(record, self.deduper, self.quarantine)
        self.assertIsNone(row)
        self.assertEqual(len(self.quarantine.read_all()), 1)

    def test_completely_garbage_payload_never_raises(self):
        garbage_payloads = [{}, [], "a string", 42, None, {"random": {"nested": ["stuff"]}}]
        for vendor in ("stripe", "adyen", "mercadopago", "dlocal", "totally_unknown"):
            for payload in garbage_payloads:
                record = self._bronze(vendor, payload)
                try:
                    normalize_bronze_record(record, self.deduper, self.quarantine)
                except Exception as exc:  # pragma: no cover - el test falla si esto pasa
                    self.fail(f"normalize_bronze_record crasheó con vendor={vendor} payload={payload!r}: {exc}")

    def test_duplicate_bronze_record_dropped_silently(self):
        record = self._bronze("stripe", STRIPE_EXAMPLE, {"merchant_id": "m1", "payment_method": "card", "country": "MX"})
        row1 = normalize_bronze_record(record, self.deduper, self.quarantine)
        row2 = normalize_bronze_record(record, self.deduper, self.quarantine)
        self.assertIsNotNone(row1)
        self.assertIsNone(row2)
        self.assertEqual(len(self.quarantine.read_all()), 0)  # duplicado no es un error

    def test_valid_event_row_matches_canonical_schema_fields(self):
        record = self._bronze(
            "mercadopago",
            MERCADOPAGO_EXAMPLE,
            {"merchant_id": "merch_acme", "payment_method": "card", "attempt_number": 1, "linked_order_id": "ord_1"},
        )
        row = normalize_bronze_record(record, self.deduper, self.quarantine)
        self.assertIsNotNone(row)
        expected_keys = {
            "time_bucket", "minute_of_day", "weekday", "merchant_id", "provider",
            "payment_method", "country", "issuing_bank", "canonical_decline_code",
            "status", "amount", "attempt_number", "linked_order_id", "attempts",
            "approvals", "actual_rate", "expected_rate", "expected_std",
            "deviation_index", "recovery_rate", "recovery_rate_deviation", "confidence",
        }
        self.assertTrue(expected_keys.issubset(row.keys()))
        self.assertEqual(row["provider"], "mercadopago")
        self.assertEqual(row["issuing_bank"], "bbva")  # issuer_id 25 -> bbva
        self.assertEqual(row["status"], "declined")
        self.assertEqual(row["canonical_decline_code"], "51_insufficient_funds")
        # campos de agregado: no aplican a nivel evento individual
        for k in ("attempts", "approvals", "actual_rate", "expected_rate", "expected_std", "deviation_index"):
            self.assertIsNone(row[k])

    def test_batch_mixed_valid_and_broken(self):
        good = self._bronze("dlocal", DLOCAL_EXAMPLE, {"merchant_id": "merch_acme", "payment_method": "card"})
        bad = self._bronze("dlocal", {"status": "PAID"})  # falta 'id', 'amount', etc.
        rows = normalize_batch([good, bad, good], self.deduper, self.quarantine)
        self.assertEqual(len(rows), 1)  # good se procesa 1 vez, la repetición es duplicado
        self.assertEqual(len(self.quarantine.read_all()), 1)


class BronzeStoreTest(TempStoresMixin, unittest.TestCase):
    def test_append_and_read_roundtrip(self):
        store = BronzeStore(path=self.tmpdir / "events.jsonl")
        store.append("stripe", STRIPE_EXAMPLE, {"merchant_id": "m1"})
        records = store.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["payload"], STRIPE_EXAMPLE)
        self.assertEqual(records[0]["routing_metadata"]["merchant_id"], "m1")

    def test_append_many(self):
        store = BronzeStore(path=self.tmpdir / "events.jsonl")
        store.append_many(
            [
                ("stripe", STRIPE_EXAMPLE, {"merchant_id": "m1"}),
                ("dlocal", DLOCAL_EXAMPLE, {"merchant_id": "m2"}),
            ]
        )
        records = store.read_all()
        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
