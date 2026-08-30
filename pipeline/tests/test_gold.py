"""
Tests del Gold layer (pipeline/gold/) -- la superficie real de
DATA-CONTRACT.md: get_counts()/get_samples() contra un `gold_dir` chico y
armado a mano (no el histórico completo de 14 días, que tarda ~35s en
generarse -- eso ya lo cubre test_baseline.py de punta a punta).

Dos motores acá también, igual que en producción (ver
pipeline/gold/schema.py): `historical.duckdb` (rate_cells_minutely /
decline_cells_hourly como TABLAS nativas en vez de Parquet-backed VIEWs --
para un fixture de test no vale la pena redondear por Parquet real,
insert_rate_rows/insert_decline_rows funcionan igual contra tablas nativas)
+ `live.sqlite` (live_attempts).
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import duckdb

from pipeline.gold.schema import (
    create_bulk_build_tables,
    create_historical_views,
    create_live_tables,
    DUCKDB_META_DDL,
    HISTORICAL_DB_FILENAME,
    LIVE_DB_FILENAME,
)
from pipeline.gold.materialize import GoldWriter, insert_rate_rows, insert_decline_rows
from pipeline.gold.access import get_counts, get_samples


class GoldFixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.gold_dir = self.tmpdir / "gold"
        self.gold_dir.mkdir()

        hist_conn = duckdb.connect(str(self.gold_dir / HISTORICAL_DB_FILENAME))
        create_bulk_build_tables(hist_conn)  # tablas nativas rate/decline (no parquet en este fixture)
        hist_conn.execute(DUCKDB_META_DDL)

        # dos minutos de rate_cells_minutely, 2 merchants, misma celda
        insert_rate_rows(
            hist_conn,
            [
                {"time_bucket": "2026-08-01T10:00:00Z", "minute_of_day": 600, "weekday": 5, "merchant_id": "merch_a", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "cell_id": "stripe|MX|card|unknown_bank", "attempts": 100, "approved": 90, "declined": 10, "error": 0, "amount_usd_total": 500.0},
                {"time_bucket": "2026-08-01T10:00:00Z", "minute_of_day": 600, "weekday": 5, "merchant_id": "merch_b", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "cell_id": "stripe|MX|card|unknown_bank", "attempts": 50, "approved": 40, "declined": 10, "error": 0, "amount_usd_total": 250.0},
                {"time_bucket": "2026-08-01T10:01:00Z", "minute_of_day": 601, "weekday": 5, "merchant_id": "merch_a", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "cell_id": "stripe|MX|card|unknown_bank", "attempts": 100, "approved": 95, "declined": 5, "error": 0, "amount_usd_total": 500.0},
            ],
        )
        # decline_cells_hourly: un código normal, un código "error"
        insert_decline_rows(
            hist_conn,
            [
                {"hour_bucket": "2026-08-01T10:00:00Z", "merchant_id": "merch_a", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "decline_code": "51_insufficient_funds", "cell_id": "stripe|MX|card|51_insufficient_funds", "declines": 8, "recovered": 4, "amount_usd_total": 100.0},
                {"hour_bucket": "2026-08-01T10:00:00Z", "merchant_id": "merch_a", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "decline_code": "91_96_network_timeout", "cell_id": "stripe|MX|card|91_96_network_timeout", "declines": 2, "recovered": 1, "amount_usd_total": 20.0},
            ],
        )
        # meta: history_end marca dónde termina lo precalculado
        hist_conn.execute("INSERT INTO meta VALUES ('history_end', '2026-08-01T12:00:00Z')")
        hist_conn.close()

        live_conn = sqlite3.connect(self.gold_dir / LIVE_DB_FILENAME)
        create_live_tables(live_conn)
        # dos filas después de history_end
        live_conn.execute(
            "INSERT INTO live_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "stripe:ch_1", "ord_1", 1, "2026-08-01T12:05:00Z", "merch_a", "stripe",
                "card", "MX", "unknown_bank", "declined", "51_insufficient_funds", 10000, "MXN", 5.4,
            ),
        )
        live_conn.execute(
            "INSERT INTO live_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "stripe:ch_2", "ord_1", 2, "2026-08-01T12:05:30Z", "merch_a", "stripe",
                "card", "MX", "unknown_bank", "approved", None, 10000, "MXN", 5.4,
            ),
        )
        live_conn.commit()
        live_conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rate_cells_pool_across_merchants_when_not_grouped(self):
        rows = get_counts("2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="minute", gold_dir=self.gold_dir)
        by_bucket = {r["bucket_ts"]: r for r in rows}
        self.assertEqual(by_bucket["2026-08-01T10:00:00Z"]["attempts"], 150)  # 100+50
        self.assertEqual(by_bucket["2026-08-01T10:00:00Z"]["approved"], 130)  # 90+40

    def test_rate_cells_group_by_merchant(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="minute",
            group_by=["merchant_id"], gold_dir=self.gold_dir,
        )
        merchants = {r["merchant_id"] for r in rows}
        self.assertEqual(merchants, {"merch_a", "merch_b"})

    def test_bucket_hour_aggregates_minutes(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="hour", gold_dir=self.gold_dir
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 250)  # 100+50+100

    def test_decline_code_query_splits_error_vs_declined(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="hour",
            group_by=["decline_code"], filters={"provider_id": "stripe", "country": "MX"},
            gold_dir=self.gold_dir,
        )
        by_code = {r["decline_code"]: r for r in rows}
        self.assertEqual(by_code["51_insufficient_funds"]["declined"], 8)
        self.assertEqual(by_code["51_insufficient_funds"]["error"], 0)
        self.assertEqual(by_code["91_96_network_timeout"]["error"], 2)
        self.assertEqual(by_code["91_96_network_timeout"]["declined"], 0)

    def test_decline_code_filter_without_group_by_hides_the_column(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="hour",
            filters={"decline_code": "91_96_network_timeout"}, gold_dir=self.gold_dir,
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("decline_code", rows[0])
        self.assertEqual(rows[0]["error"], 2)

    def test_spanning_query_merges_historical_and_live(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T13:00:00Z", bucket="hour", gold_dir=self.gold_dir
        )
        buckets = {r["bucket_ts"]: r for r in rows if r["bucket_ts"] == "2026-08-01T12:00:00Z"}
        self.assertIn("2026-08-01T12:00:00Z", buckets)
        live_row = buckets["2026-08-01T12:00:00Z"]
        self.assertEqual(live_row["attempts"], 2)
        self.assertEqual(live_row["approved"], 1)
        self.assertEqual(live_row["declined"], 1)

    def test_get_samples_returns_live_rows_with_contract_field_names(self):
        samples = get_samples("2026-08-01T12:00:00Z", "2026-08-01T13:00:00Z", gold_dir=self.gold_dir)
        self.assertEqual(len(samples), 2)
        ids = {s["attempt_id"] for s in samples}
        self.assertEqual(ids, {"stripe:ch_1", "stripe:ch_2"})
        self.assertEqual(samples[0]["payment_id"], "ord_1")

    def test_get_samples_filters_by_status(self):
        samples = get_samples(
            "2026-08-01T12:00:00Z", "2026-08-01T13:00:00Z", filters={"status": "declined"}, gold_dir=self.gold_dir
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["status"], "declined")

    def test_get_samples_empty_over_pure_historical_range(self):
        samples = get_samples("2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", gold_dir=self.gold_dir)
        self.assertEqual(samples, [])

    def test_invalid_group_by_raises(self):
        with self.assertRaises(ValueError):
            get_counts("2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", group_by=["not_a_dimension"], gold_dir=self.gold_dir)

    def test_invalid_filter_raises(self):
        with self.assertRaises(ValueError):
            get_counts("2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", filters={"nonsense": "x"}, gold_dir=self.gold_dir)

    def test_bucket_granularity_reflects_minute_to_hour_downgrade(self):
        # decline_code en group_by con bucket="minute" pedido -> no hay
        # resolución de minuto guardada en decline_cells_hourly, así que
        # cada fila debe confesar que en realidad quedó en "hour".
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="minute",
            group_by=["decline_code"], filters={"provider_id": "stripe", "country": "MX"},
            gold_dir=self.gold_dir,
        )
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["bucket_granularity"], "hour")

    def test_bucket_granularity_matches_request_when_no_downgrade(self):
        rows = get_counts("2026-08-01T09:00:00Z", "2026-08-01T11:00:00Z", bucket="minute", gold_dir=self.gold_dir)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["bucket_granularity"], "minute")


class BoundaryMergeTest(unittest.TestCase):
    """history_end a mitad de hora (no en un límite de hora limpio) para
    que una fila histórica y una fila en vivo caigan de verdad en el MISMO
    bucket_ts bajo bucket="hour" -- GoldFixtureTest's history_end cae justo
    en un límite de hora, por eso nunca ejercita este caso."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.gold_dir = self.tmpdir / "gold"
        self.gold_dir.mkdir()

        hist_conn = duckdb.connect(str(self.gold_dir / HISTORICAL_DB_FILENAME))
        create_bulk_build_tables(hist_conn)
        hist_conn.execute(DUCKDB_META_DDL)
        insert_rate_rows(
            hist_conn,
            [
                {"time_bucket": "2026-08-01T10:15:00Z", "minute_of_day": 615, "weekday": 5, "merchant_id": "merch_a", "provider_id": "stripe", "country": "MX", "method": "card", "issuing_bank": "unknown_bank", "cell_id": "stripe|MX|card|unknown_bank", "attempts": 100, "approved": 90, "declined": 10, "error": 0, "amount_usd_total": 500.0},
            ],
        )
        hist_conn.execute("INSERT INTO meta VALUES ('history_end', '2026-08-01T10:30:00Z')")
        hist_conn.close()

        live_conn = sqlite3.connect(self.gold_dir / LIVE_DB_FILENAME)
        create_live_tables(live_conn)
        # mismo bucket de hora (10:00) que la fila histórica de arriba,
        # pero después de history_end (10:30)
        live_conn.execute(
            "INSERT INTO live_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "stripe:ch_9", "ord_9", 1, "2026-08-01T10:45:00Z", "merch_a", "stripe",
                "card", "MX", "unknown_bank", "declined", "51_insufficient_funds", 10000, "MXN", 5.4,
            ),
        )
        live_conn.commit()
        live_conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_straddling_hour_bucket_returns_one_merged_row_not_two(self):
        rows = get_counts(
            "2026-08-01T09:00:00Z", "2026-08-01T12:00:00Z", bucket="hour", gold_dir=self.gold_dir
        )
        matching = [r for r in rows if r["bucket_ts"] == "2026-08-01T10:00:00Z"]
        self.assertEqual(len(matching), 1, f"expected exactly one merged row, got {matching}")
        row = matching[0]
        self.assertEqual(row["attempts"], 101)  # 100 histórico + 1 en vivo
        self.assertEqual(row["approved"], 90)
        self.assertEqual(row["declined"], 11)  # 10 histórico + 1 en vivo


class GoldWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.db_path = self.tmpdir / "live.sqlite"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_translates_silver_row_field_names_and_units(self):
        writer = GoldWriter(db_path=self.db_path)
        silver_row = {
            "provider": "stripe",
            "payment_method": "card",
            "country": "MX",
            "issuing_bank": "unknown_bank",
            "canonical_decline_code": "51_insufficient_funds",
            "status": "declined",
            "amount": 192.70,
            "currency": "MXN",
            "amount_usd": 10.42,
            "attempt_number": 1,
            "linked_order_id": "ord_xyz",
            "event_ts": "2026-08-29T14:32:00Z",
            "time_bucket": "2026-08-29T14:32:00Z",
            "merchant_id": "merch_acme",
            "_native_id": "ch_test123",
        }
        writer.write_live_batch([silver_row])

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT * FROM live_attempts").fetchone()
        conn.close()
        writer.close()

        self.assertIsNotNone(row)
        attempt_id, payment_id, attempt_number, event_ts, merchant_id, provider_id, method = row[:7]
        self.assertEqual(attempt_id, "stripe:ch_test123")
        self.assertEqual(payment_id, "ord_xyz")
        self.assertEqual(provider_id, "stripe")
        self.assertEqual(method, "card")
        amount_minor = row[11]
        self.assertEqual(amount_minor, 19270)  # 192.70 -> centavos

    def test_write_empty_batch_is_a_noop(self):
        writer = GoldWriter(db_path=self.db_path)
        writer.write_live_batch([])
        writer.write_live_batch([None, None])
        writer.close()  # no debe crashear

    def test_creates_parent_directory(self):
        nested = self.tmpdir / "nested" / "dir" / "live.sqlite"
        writer = GoldWriter(db_path=nested)
        writer.close()
        self.assertTrue(nested.exists())

    def test_live_writer_uses_wal_for_concurrent_detector_reads(self):
        writer = GoldWriter(db_path=self.db_path)
        self.assertEqual("wal", writer.conn.execute("PRAGMA journal_mode").fetchone()[0].lower())
        self.assertEqual(5000, writer.conn.execute("PRAGMA busy_timeout").fetchone()[0])
        writer.close()


if __name__ == "__main__":
    unittest.main()
