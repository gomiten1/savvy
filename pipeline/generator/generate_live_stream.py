"""
Generador B — stream en vivo para la demo.

DEMO_SPEED_MULTIPLIER = 10 -> el reloj simulado corre 10x más rápido que el
reloj real: 1 minuto simulado ~= 6 segundos reales. Con
BASE_TXNS_PER_MINUTE=2000 eso da ~333 transacciones/seg reales.

A diferencia del Generador A (agregados por minuto x celda), este SÍ produce
eventos individuales vendor-shaped (uno por intento de pago), porque tienen
que fluir por Bronze -> Silver en vivo, visiblemente, frente al panel. Cada
evento se escribe a Bronze en batch por tick (no uno a uno) para aguantar el
throughput sin pagar el costo de abrir/flushear el archivo por transacción.

Soporta inyección de incidentes:
  - "horneados" al construir el generador (lista de Incident), para armar el
    guión de la demo.
  - en vivo durante el "trial by fire": llamando a `trigger_incident(...)`
    directamente, o dejando caer un JSON en TRIGGER_FILE (ver
    `write_incident_trigger`) que el loop consume en el siguiente tick sin
    reiniciar el proceso.
"""
import argparse
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from pipeline.domain.weights import (
    BASE_TXNS_PER_MINUTE,
    DEMO_SPEED_MULTIPLIER,
    MERCHANT_WEIGHTS,
    APPROVAL_RATE,
    CANONICAL_DECLINE_WEIGHTS,
    RECOVERY_RATE_BY_CANONICAL_CODE,
    RECOVERY_BY_ATTEMPT,
    MAX_ATTEMPTS_PER_TRANSACTION,
    AMOUNT_RANGE_BY_COUNTRY,
)
from pipeline.domain.seasonality import volume_multiplier
from pipeline.generator.sampling import enumerate_rate_cells, weighted_choice, poisson_sample
from pipeline.generator.vendor_shapes import CanonicalEvent, build_vendor_event, pick_issuer_id
from pipeline.generator.inject_incidents import (
    Incident,
    effective_approval_rate,
    dominant_decline_code_override,
)
from pipeline.gold.schema import GOLD_DIRNAME, HISTORICAL_DB_FILENAME, LIVE_DB_FILENAME
from pipeline.bronze.bronze_store import BronzeStore
from pipeline.silver.dedup import Deduper
from pipeline.silver.quarantine import Quarantine
from pipeline.silver.normalize import normalize_batch

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GOLD_DIR = DATA_DIR / GOLD_DIRNAME
TRIGGER_FILE = DATA_DIR / "live" / "incident_trigger.json"
# The historical generator can finish during the thin overnight period.  Starting
# live there turns sparse merchant splits into misleading near-100% baselines.
# Pick a stable, daytime hour *after* history_end so live rows still land in the
# live database rather than overlapping the historical artifact.
DEFAULT_LIVE_START_HOUR_UTC = 12

# ASUNCIÓN sobre RECOVERY_BY_ATTEMPT (ver docs/decision_log.md): la clave N
# es "probabilidad de que el reintento que sigue al intento fallido N tenga
# éxito". Con MAX_ATTEMPTS_PER_TRANSACTION=3 solo se disparan reintentos
# para N=1 (-> attempt_number 2) y N=2 (-> attempt_number 3); la clave 3
# queda documentada pero nunca se dispara.
RETRY_ENGAGEMENT_MULTIPLIER = 1.4
RETRY_ENGAGEMENT_CAP = 0.95
RETRY_DELAY_SIM_SECONDS = (5, 30)


def write_incident_trigger(name, provider=None, country=None, payment_method=None,
                            issuing_bank=None, duration_minutes=60, approval_rate_multiplier=0.3,
                            dominant_decline_code=None):
    """Helper para que un operador/juez dispare un incidente en vivo sin
    tocar código: escribe TRIGGER_FILE, que el loop de run() consume solo."""
    TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    cell_filter = {
        k: v
        for k, v in {
            "provider": provider,
            "country": country,
            "payment_method": payment_method,
            "issuing_bank": issuing_bank,
        }.items()
        if v is not None
    }
    payload = {
        "name": name,
        "cell_filter": cell_filter,
        "duration_minutes": duration_minutes,
        "approval_rate_multiplier": approval_rate_multiplier,
        "dominant_decline_code": dominant_decline_code,
    }
    TRIGGER_FILE.write_text(json.dumps(payload), encoding="utf-8")


def _read_history_end(gold_dir: Path):
    hist_db = gold_dir / HISTORICAL_DB_FILENAME
    if not hist_db.exists():
        return None
    conn = duckdb.connect(str(hist_db), read_only=True)
    row = conn.execute("SELECT value FROM meta WHERE key='history_end'").fetchone()
    conn.close()
    if not row:
        return None
    return datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def default_sim_start(history_end: datetime | None) -> datetime:
    """Return the next 12:00 UTC after history_end (or now when no history exists)."""
    reference = history_end or datetime.now(timezone.utc)
    candidate = reference.replace(hour=DEFAULT_LIVE_START_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= reference:
        candidate += timedelta(days=1)
    return candidate


def _parse_sim_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO timestamp, e.g. 2026-08-31T12:00:00Z") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass
class _PendingRetry:
    scheduled_sim_dt: datetime
    provider: str
    country: str
    payment_method: str
    merchant_id: str
    linked_order_id: str
    attempt_number: int
    failed_attempt_number: int  # el intento que falló y disparó este retry
    amount: float
    canonical_decline_code: str  # código del intento original, reusado si el retry también falla


class LiveStreamGenerator:
    def __init__(self, incidents=None, seed=None, speed_multiplier=DEMO_SPEED_MULTIPLIER,
                 base_txns_per_minute=BASE_TXNS_PER_MINUTE, bronze_store=None, sim_start=None,
                 gold_writer=None, reveal_injections=False):
        import random

        self.rng = random.Random(seed)
        self.speed_multiplier = speed_multiplier
        self.base_txns_per_minute = base_txns_per_minute
        self.bronze = bronze_store or BronzeStore()
        self.rate_cells = enumerate_rate_cells()
        self.incidents = list(incidents or [])
        self._pending_retries = []
        self.sim_now = sim_start or default_sim_start(_read_history_end(GOLD_DIR))
        self.events_emitted = 0
        self._trigger_file_consumed = False
        # DATA-CONTRACT.md: "el injector must NOT tell us qué inyectó" — el
        # operador puede prender esto para su propio debugging, pero por
        # default no se imprime nada que revele el incidente (ni acá ni en
        # ningún log que el equipo de detección pudiera ver).
        self.reveal_injections = reveal_injections
        self.gold_writer = gold_writer
        self._deduper = Deduper()
        self._quarantine = Quarantine()

    def trigger_incident(self, incident: Incident):
        self.incidents.append(incident)

    def _check_trigger_file(self):
        if not TRIGGER_FILE.exists():
            return
        try:
            payload = json.loads(TRIGGER_FILE.read_text(encoding="utf-8"))
            incident = Incident(
                name=payload["name"],
                start=self.sim_now,
                duration_minutes=payload.get("duration_minutes", 60),
                approval_rate_multiplier=payload.get("approval_rate_multiplier", 0.3),
                cell_filter=payload.get("cell_filter", {}),
                dominant_decline_code=payload.get("dominant_decline_code"),
            )
            self.trigger_incident(incident)
            if self.reveal_injections:
                print(f"[live] incidente disparado vía trigger file: {incident.name} {incident.cell_filter}")
        finally:
            TRIGGER_FILE.unlink(missing_ok=True)

    def _sample_amount(self, country: str) -> float:
        lo, hi = AMOUNT_RANGE_BY_COUNTRY[country]
        return round(self.rng.uniform(lo, hi), 2)

    def _to_bronze_record(self, ev: CanonicalEvent, merchant_id: str, linked_order_id: str, attempt_number: int):
        vendor_payload = build_vendor_event(ev, self.rng)
        routing = {
            "merchant_id": merchant_id,
            "payment_method": ev.payment_method,
            "country": ev.country,
            "attempt_number": attempt_number,
            "linked_order_id": linked_order_id,
        }
        return (ev.provider, vendor_payload, routing)

    def _maybe_schedule_retry(self, sim_now, provider, country, payment_method, merchant_id,
                               linked_order_id, failed_attempt_number, amount, canonical_decline_code):
        if failed_attempt_number >= MAX_ATTEMPTS_PER_TRANSACTION:
            return
        base_recoverability = RECOVERY_RATE_BY_CANONICAL_CODE.get(canonical_decline_code, 0.0)
        retry_chance = min(RETRY_ENGAGEMENT_CAP, base_recoverability * RETRY_ENGAGEMENT_MULTIPLIER)
        if self.rng.random() >= retry_chance:
            return
        delay = self.rng.uniform(*RETRY_DELAY_SIM_SECONDS)
        self._pending_retries.append(
            _PendingRetry(
                scheduled_sim_dt=sim_now + timedelta(seconds=delay),
                provider=provider,
                country=country,
                payment_method=payment_method,
                merchant_id=merchant_id,
                linked_order_id=linked_order_id,
                attempt_number=failed_attempt_number + 1,
                failed_attempt_number=failed_attempt_number,
                amount=amount,
                canonical_decline_code=canonical_decline_code,
            )
        )

    def _fire_due_retries(self, sim_now):
        due = [r for r in self._pending_retries if r.scheduled_sim_dt <= sim_now]
        if not due:
            return []
        self._pending_retries = [r for r in self._pending_retries if r.scheduled_sim_dt > sim_now]
        records = []
        for r in due:
            success_lo, success_hi = RECOVERY_BY_ATTEMPT[r.failed_attempt_number]
            success_prob = self.rng.uniform(success_lo, success_hi)
            approved = self.rng.random() < success_prob
            issuer_id = pick_issuer_id(self.rng, r.country) if r.provider == "mercadopago" else None
            ev = CanonicalEvent(
                txn_id=str(uuid.uuid4()),
                provider=r.provider,
                country=r.country,
                payment_method=r.payment_method,
                amount=r.amount,
                approved=approved,
                canonical_decline_code=None if approved else r.canonical_decline_code,
                created_dt=sim_now,
                issuer_id=issuer_id,
            )
            records.append(self._to_bronze_record(ev, r.merchant_id, r.linked_order_id, r.attempt_number))
            self.events_emitted += 1
            if not approved:
                self._maybe_schedule_retry(
                    sim_now, r.provider, r.country, r.payment_method, r.merchant_id,
                    r.linked_order_id, r.attempt_number, r.amount, r.canonical_decline_code,
                )
        return records

    def _generate_originals(self, sim_now, tick_sim_seconds):
        total_expected = (self.base_txns_per_minute / 60.0) * tick_sim_seconds
        records = []
        for cell in self.rate_cells:
            # Estacionalidad (weekday x hora-del-día) aplicada acá igual que
            # en el Generador A -- ver seasonality.py. NO se pasan
            # active_events: los picos estacionales grandes (buen_fin_mx,
            # etc.) se disparan por offset de día dentro de la ventana fija
            # de 14 días del histórico, un concepto que el demo en vivo (un
            # sim_now corriendo, sin ventana) no tiene -- solo el patrón
            # semanal/horario aplica acá.
            vol_mult = volume_multiplier(sim_now, cell.country, active_events=None)
            expected = total_expected * cell.weight * vol_mult
            n = poisson_sample(self.rng, expected)
            if n <= 0:
                continue
            cell_dims = {
                "provider": cell.provider,
                "country": cell.country,
                "payment_method": cell.payment_method,
                "issuing_bank": cell.issuing_bank,
            }
            base_rate = APPROVAL_RATE[cell.provider]
            eff_rate = effective_approval_rate(base_rate, sim_now, cell_dims, self.incidents)
            forced_code = dominant_decline_code_override(sim_now, cell_dims, self.incidents)
            for _ in range(n):
                approved = self.rng.random() < eff_rate
                merchant_id = weighted_choice(self.rng, MERCHANT_WEIGHTS)
                issuer_id = pick_issuer_id(self.rng, cell.country) if cell.provider == "mercadopago" else None
                code = None if approved else (forced_code or weighted_choice(self.rng, CANONICAL_DECLINE_WEIGHTS))
                amount = self._sample_amount(cell.country)
                order_id = f"ord_{uuid.uuid4()}"
                ev = CanonicalEvent(
                    txn_id=str(uuid.uuid4()),
                    provider=cell.provider,
                    country=cell.country,
                    payment_method=cell.payment_method,
                    amount=amount,
                    approved=approved,
                    canonical_decline_code=code,
                    created_dt=sim_now,
                    issuer_id=issuer_id,
                )
                records.append(self._to_bronze_record(ev, merchant_id, order_id, attempt_number=1))
                self.events_emitted += 1
                if not approved:
                    self._maybe_schedule_retry(
                        sim_now, cell.provider, cell.country, cell.payment_method,
                        merchant_id, order_id, failed_attempt_number=1, amount=amount,
                        canonical_decline_code=code,
                    )
        return records

    def tick(self, tick_real_seconds: float):
        tick_sim_seconds = tick_real_seconds * self.speed_multiplier
        self.sim_now = self.sim_now + timedelta(seconds=tick_sim_seconds)
        self._check_trigger_file()
        records = self._generate_originals(self.sim_now, tick_sim_seconds)
        records += self._fire_due_retries(self.sim_now)
        if records:
            bronze_records = self.bronze.append_many(records)
            if self.gold_writer is not None:
                rows = normalize_batch(bronze_records, self._deduper, self._quarantine)
                self.gold_writer.write_live_batch(rows)
        return len(records)

    def run(self, duration_real_seconds=None, tick_interval=0.2, verbose=True):
        start = time.time()
        last_report = start
        while duration_real_seconds is None or time.time() - start < duration_real_seconds:
            loop_t0 = time.time()
            n = self.tick(tick_interval)
            if verbose and time.time() - last_report >= 2.0:
                elapsed = time.time() - start
                rate = self.events_emitted / elapsed if elapsed > 0 else 0
                print(
                    f"[live] t={elapsed:5.1f}s sim_now={self.sim_now.isoformat()} "
                    f"emitidos={self.events_emitted} ({rate:.0f}/s) pendientes_retry={len(self._pending_retries)}"
                )
                last_report = time.time()
            elapsed_loop = time.time() - loop_t0
            sleep_for = tick_interval - elapsed_loop
            if sleep_for > 0:
                time.sleep(sleep_for)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--duration", type=float, default=90.0, help="segundos reales; 0 = indefinido")
    parser.add_argument("--tick-interval", type=float, default=0.2)
    parser.add_argument(
        "--sim-start", type=_parse_sim_start,
        help="override simulated start (ISO UTC); default is the next 12:00 UTC after history_end",
    )
    parser.add_argument(
        "--reveal-injections", action="store_true",
        help="loguea qué incidente se disparó -- NO usar durante un trial-by-fire real",
    )
    args = parser.parse_args()

    from pipeline.gold.materialize import GoldWriter

    live_db_path = GOLD_DIR / LIVE_DB_FILENAME
    gold_writer = GoldWriter(db_path=live_db_path)
    gen = LiveStreamGenerator(seed=args.seed, gold_writer=gold_writer, sim_start=args.sim_start,
                              reveal_injections=args.reveal_injections)
    print(f"[live] arrancando en sim_now={gen.sim_now.isoformat()} speed={DEMO_SPEED_MULTIPLIER}x")
    print(f"[live] escribiendo a Bronze + Gold (live_attempts) en {live_db_path}")
    try:
        gen.run(duration_real_seconds=(None if args.duration == 0 else args.duration), tick_interval=args.tick_interval)
    finally:
        gold_writer.close()


if __name__ == "__main__":
    main()
