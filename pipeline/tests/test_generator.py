"""
Tests del Generador A/B: shapes de vendor contra los ejemplos verificados
del brief, round-trip de decline codes, pesos que suman 1, y el cálculo de
volumen validado en el brief (celda más chica ~393.8 transacciones).
"""
import random
import unittest
from datetime import datetime, timezone

from pipeline.generator.weights import (
    PROVIDER_WEIGHTS,
    COUNTRY_WEIGHTS,
    METHOD_WEIGHTS_BY_COUNTRY,
    CANONICAL_DECLINE_WEIGHTS,
    MERCHANT_WEIGHTS,
    APPROVAL_RATE,
    MIN_SAMPLE_SIZE_PER_CELL,
)
from pipeline.generator.seasonality import hourly_multiplier, weekday_multiplier, WEEKDAY_INDEX
from pipeline.generator.sampling import enumerate_rate_cells, enumerate_recovery_cells, poisson_sample
from pipeline.generator.vendor_shapes import CanonicalEvent, build_vendor_event
from pipeline.silver.decline_mapping import (
    CANONICAL_CODES,
    CANONICAL_TO_VENDOR_CODES,
    map_to_canonical,
    pick_vendor_code,
)

_CANONICAL_DECLINE_WEIGHTS_TOTAL = sum(CANONICAL_DECLINE_WEIGHTS.values())


def _assert_close(test, value, target, tol=1e-6):
    test.assertLess(abs(value - target), tol, f"{value} != {target}")


class WeightsSumToOneTest(unittest.TestCase):
    def test_provider_weights(self):
        _assert_close(self, sum(PROVIDER_WEIGHTS.values()), 1.0)

    def test_country_weights(self):
        _assert_close(self, sum(COUNTRY_WEIGHTS.values()), 1.0)

    def test_method_weights_per_country(self):
        for country, methods in METHOD_WEIGHTS_BY_COUNTRY.items():
            _assert_close(self, sum(methods.values()), 1.0)

    def test_canonical_decline_weights(self):
        # OJO: el brief da estos pesos "tal cual" y en realidad suman 1.08,
        # no 1.0 (0.50+0.25+0.20+0.03+0.02*5 = 1.08). No es un bug de este
        # test — es un quirk real de los datos fuente. distribute_declines()
        # en generate_historical_aggregates.py normaliza antes de repartir
        # (ver comentario ahí). Este test solo documenta el número exacto
        # para que no se "corrija" el diccionario sin querer.
        _assert_close(self, sum(CANONICAL_DECLINE_WEIGHTS.values()), 1.08)

    def test_merchant_weights(self):
        _assert_close(self, sum(MERCHANT_WEIGHTS.values()), 1.0)

    def test_rate_cells_weight_sums_to_one(self):
        cells = enumerate_rate_cells()
        _assert_close(self, sum(c.weight for c in cells), 1.0, tol=1e-9)

    def test_rate_cells_count(self):
        # mercadopago: 3 países x 3 métodos x 3 bancos = 27
        # otros 3 providers: 3 países x 3 métodos x 1 banco = 27
        self.assertEqual(len(enumerate_rate_cells()), 54)

    def test_recovery_cells_count(self):
        # 4 providers x 3 países x 3 métodos x 9 códigos
        self.assertEqual(len(enumerate_recovery_cells()), 4 * 3 * 3 * 9)


class SeasonalityTest(unittest.TestCase):
    def test_hourly_peak(self):
        for h in (20, 21, 22, 23):
            self.assertEqual(hourly_multiplier(h), 1.5)

    def test_hourly_secondary_peak(self):
        for h in (11, 12, 13, 14):
            self.assertEqual(hourly_multiplier(h), 1.2)

    def test_hourly_valley(self):
        for h in (2, 3, 4, 5, 6):
            self.assertEqual(hourly_multiplier(h), 0.10)

    def test_hourly_rest(self):
        for h in (0, 1, 7, 8, 9, 10, 15, 16, 17, 18, 19):
            self.assertEqual(hourly_multiplier(h), 0.7)

    def test_weekday_index_matches_brief(self):
        expected = {0: 1.023, 1: 1.136, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.875, 6: 0.875}
        self.assertEqual(WEEKDAY_INDEX, expected)
        for wd, mult in expected.items():
            self.assertEqual(weekday_multiplier(wd), mult)


class PoissonSampleTest(unittest.TestCase):
    def test_zero_lambda_is_zero(self):
        rng = random.Random(1)
        self.assertEqual(poisson_sample(rng, 0), 0)

    def test_small_lambda_mean_is_close(self):
        rng = random.Random(2)
        lam = 0.3
        samples = [poisson_sample(rng, lam) for _ in range(20000)]
        self.assertAlmostEqual(sum(samples) / len(samples), lam, delta=0.05)

    def test_large_lambda_mean_is_close(self):
        rng = random.Random(3)
        lam = 120.0
        samples = [poisson_sample(rng, lam) for _ in range(3000)]
        self.assertAlmostEqual(sum(samples) / len(samples), lam, delta=lam * 0.1)


class DeclineMappingRoundTripTest(unittest.TestCase):
    def test_all_canonical_codes_covered_per_vendor(self):
        for vendor, table in CANONICAL_TO_VENDOR_CODES.items():
            self.assertEqual(set(table.keys()), set(CANONICAL_CODES))

    def test_roundtrip_every_vendor_every_code(self):
        rng = random.Random(0)
        for vendor in CANONICAL_TO_VENDOR_CODES:
            for code in CANONICAL_CODES:
                for _ in range(5):
                    native = pick_vendor_code(vendor, code, rng)
                    self.assertEqual(map_to_canonical(vendor, native), code)

    def test_unknown_native_code_maps_to_unknown(self):
        self.assertEqual(map_to_canonical("stripe", "totally_bogus_code_xyz"), "unknown")
        self.assertEqual(map_to_canonical("stripe", None), "unknown")

    def test_unknown_vendor_maps_to_unknown(self):
        self.assertEqual(map_to_canonical("some_new_vendor", "insufficient_funds"), "unknown")


class VendorShapeTest(unittest.TestCase):
    """Valida que el shape de cada vendor tenga EXACTAMENTE las claves
    verificadas en el brief (ni de más, ni de menos, salvo los campos
    opcionales que el vendor real omite en el caso 'approved')."""

    def _event(self, provider, approved, code=None, issuer_id=None, country="MX"):
        return CanonicalEvent(
            txn_id="t1",
            provider=provider,
            country=country,
            payment_method="card",
            amount=1927.00,
            approved=approved,
            canonical_decline_code=code,
            created_dt=datetime(2026, 8, 29, 14, 32, 0, tzinfo=timezone.utc),
            issuer_id=issuer_id,
        )

    def test_stripe_declined_shape(self):
        rng = random.Random(5)
        ev = self._event("stripe", approved=False, code="51_insufficient_funds")
        payload = build_vendor_event(ev, rng)
        self.assertEqual(set(payload.keys()), {"id", "amount", "currency", "status", "created", "failure_code"})
        self.assertEqual(payload["status"], "failed")
        self.assertIsInstance(payload["amount"], int)
        self.assertEqual(payload["amount"], 192700)  # centavos
        self.assertIsInstance(payload["created"], int)

    def test_stripe_approved_shape_has_no_failure_code(self):
        rng = random.Random(5)
        ev = self._event("stripe", approved=True)
        payload = build_vendor_event(ev, rng)
        self.assertEqual(set(payload.keys()), {"id", "amount", "currency", "status", "created"})
        self.assertEqual(payload["status"], "succeeded")

    def test_adyen_declined_shape(self):
        rng = random.Random(6)
        ev = self._event("adyen", approved=False, code="54_expired_card")
        payload = build_vendor_event(ev, rng)
        self.assertEqual(set(payload.keys()), {"live", "notificationItems"})
        item = payload["notificationItems"][0]["NotificationRequestItem"]
        self.assertEqual(
            set(item.keys()), {"eventCode", "success", "eventDate", "pspReference", "amount", "reason"}
        )
        self.assertEqual(item["success"], "false")  # string, no bool
        self.assertIsInstance(item["success"], str)
        self.assertEqual(item["eventCode"], "AUTHORISATION")
        self.assertEqual(set(item["amount"].keys()), {"value", "currency"})

    def test_mercadopago_shape(self):
        rng = random.Random(7)
        ev = self._event("mercadopago", approved=False, code="51_insufficient_funds", issuer_id=25)
        payload = build_vendor_event(ev, rng)
        self.assertEqual(
            set(payload.keys()),
            {"id", "status", "status_detail", "transaction_amount", "issuer_id", "site_id"},
        )
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["site_id"], "MLM")
        self.assertEqual(payload["issuer_id"], 25)

    def test_dlocal_shape(self):
        rng = random.Random(8)
        ev = self._event("dlocal", approved=True, country="AR" if False else "MX")
        payload = build_vendor_event(ev, rng)
        self.assertEqual(
            set(payload.keys()),
            {"id", "amount", "status", "status_code", "currency", "country", "payment_method_type"},
        )
        self.assertEqual(payload["status"], "PAID")
        self.assertEqual(payload["status_code"], "200")


class VolumeValidationTest(unittest.TestCase):
    """Reproduce el cálculo de volumen validado en el brief: la celda de
    recovery más chica (stripe x CO x wallet x 41_43_lost_stolen) debe
    rondar ~393.8 transacciones en 14 días — no es un número mágico, sale
    de PROVIDER_WEIGHTS x COUNTRY_WEIGHTS x METHOD_WEIGHTS x
    CANONICAL_DECLINE_WEIGHTS x (1-APPROVAL_RATE) x estacionalidad promedio.
    """

    def test_smallest_cell_formula_matches_brief(self):
        avg_hourly = sum(hourly_multiplier(h) for h in range(24)) / 24
        avg_weekday = sum(WEEKDAY_INDEX.values()) / 7
        total_attempts_14d = 2000 * 20160  # BASE_TXNS_PER_MINUTE * minutos en 14 días
        fraction = (
            PROVIDER_WEIGHTS["stripe"]
            * COUNTRY_WEIGHTS["CO"]
            * METHOD_WEIGHTS_BY_COUNTRY["CO"]["wallet"]
            * (1 - APPROVAL_RATE["stripe"])
            * CANONICAL_DECLINE_WEIGHTS["41_43_lost_stolen"]
        )
        expected = total_attempts_14d * fraction * avg_hourly * avg_weekday
        # Usa CANONICAL_DECLINE_WEIGHTS crudo (sin normalizar) porque así
        # reproduce el 393.8 exacto del brief -> confirma que esa cifra fue
        # calculada con los pesos "tal cual" (que suman 1.08). El generador
        # real normaliza al repartir (ver distribute_declines), así que el
        # conteo real que sale de la DB es ~8% más chico (~365) pero sigue
        # arriba del umbral.
        self.assertAlmostEqual(expected, 393.8, delta=5.0)
        self.assertGreater(expected, MIN_SAMPLE_SIZE_PER_CELL)
        self.assertGreater(expected / _CANONICAL_DECLINE_WEIGHTS_TOTAL, MIN_SAMPLE_SIZE_PER_CELL)


if __name__ == "__main__":
    unittest.main()
