"""
Dispatcher Silver: Bronze record -> fila con el shape del schema canónico
(grain evento individual: status/amount/attempt_number/linked_order_id
poblados, attempts/approvals/actual_rate/baseline/recovery en None porque
esos son responsabilidad de baseline.py/recovery.py sobre agregados).

Nunca crashea: cualquier error de parsing manda el registro a quarantine y
devuelve None. Duplicados (mismo ID nativo del vendor, vía dedup.py) se
descartan silenciosamente (no son un error).

payment_method/merchant_id/attempt_number/linked_order_id no existen en
ningún shape de vendor verificado -> vienen de `routing_metadata` en el
registro Bronze (contexto que el orquestador ya conoce antes de llamar al
vendor). Ver pipeline/bronze/bronze_store.py y docs/decision_log.md.
"""
from datetime import datetime, timezone

from pipeline.silver.parsers import parse, CURRENCY_TO_COUNTRY
from pipeline.silver.bin_lookup import lookup_bank
from pipeline.silver.decline_mapping import resolve_status
from pipeline.generator.seasonality import minute_of_day, to_utc_iso
from pipeline.generator.weights import FX_RATE_TO_USD

# país -> moneda local (inversa de CURRENCY_TO_COUNTRY, que ya vive en
# parsers.py). Se usa para resolver `currency`/`amount_usd` cuando el
# vendor no trae moneda (ej. mercadopago) pero sí trae/derivamos country.
COUNTRY_TO_CURRENCY = {v: k for k, v in CURRENCY_TO_COUNTRY.items()}

CANONICAL_FIELDS_TEMPLATE = {
    "attempts": None,
    "approvals": None,
    "actual_rate": None,
    "expected_rate": None,
    "expected_std": None,
    "deviation_index": None,
    "recovery_rate": None,
    "recovery_rate_deviation": None,
    "confidence": None,
}


def _parse_bronze_ingested_at(bronze_record: dict) -> datetime:
    raw = bronze_record["ingested_at"]
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def normalize_bronze_record(bronze_record: dict, deduper, quarantine) -> dict:
    vendor = bronze_record.get("vendor")
    payload = bronze_record.get("payload")
    routing = bronze_record.get("routing_metadata") or {}

    if not vendor or payload is None:
        quarantine.add(bronze_record, "missing vendor or payload")
        return None

    if deduper.is_duplicate(vendor, payload):
        return None

    try:
        parsed = parse(vendor, payload)
    except Exception as exc:  # noqa: BLE001 - cualquier malformación cae aquí, nunca debe crashear
        quarantine.add(bronze_record, f"parse_error: {vendor}: {exc}")
        return None

    event_dt = parsed.get("created_dt")
    if event_dt is None:
        try:
            event_dt = _parse_bronze_ingested_at(bronze_record)
        except Exception as exc:  # noqa: BLE001
            quarantine.add(bronze_record, f"no usable timestamp: {exc}")
            return None
    # time_bucket queda floored al minuto (para el baseline por
    # minuto-del-día); event_ts guarda la precisión original — el contrato
    # con detección pide event time real, no bucketeado, para
    # ordenamiento/freshness (DATA-CONTRACT.md sección 6).
    bucket_dt = event_dt.replace(second=0, microsecond=0)

    country = routing.get("country") or parsed.get("parsed_country")
    # Ningún shape de vendor verificado trae payment_method -> viene de
    # routing_metadata (contexto del orquestador). Provider y payment_method
    # son dimensiones independientes en este dataset (PROVIDER_WEIGHTS y
    # METHOD_WEIGHTS_BY_COUNTRY del brief no traen matriz de compatibilidad),
    # así que si routing_metadata no lo trae, queda None (no se inventa un
    # default) — ver docs/decision_log.md.
    payment_method = routing.get("payment_method")
    issuing_bank = lookup_bank(parsed.get("issuer_id"))
    status = resolve_status(parsed["status"], parsed.get("canonical_decline_code"))

    currency = parsed.get("currency") or COUNTRY_TO_CURRENCY.get(country)
    amount = parsed["amount"]
    amount_usd = round(amount * FX_RATE_TO_USD[currency], 2) if currency in FX_RATE_TO_USD else None

    row = {
        "time_bucket": to_utc_iso(bucket_dt),
        "event_ts": to_utc_iso(event_dt),
        "minute_of_day": minute_of_day(bucket_dt),
        "weekday": bucket_dt.weekday(),
        "merchant_id": routing.get("merchant_id"),
        "provider": vendor,
        "payment_method": payment_method,
        "country": country,
        "issuing_bank": issuing_bank,
        "canonical_decline_code": parsed.get("canonical_decline_code"),
        "status": status,
        "amount": amount,
        "currency": currency,
        "amount_usd": amount_usd,
        "attempt_number": routing.get("attempt_number", 1),
        "linked_order_id": routing.get("linked_order_id"),
        **CANONICAL_FIELDS_TEMPLATE,
        "_native_id": parsed.get("native_id"),
        "_bronze_id": bronze_record.get("bronze_id"),
    }
    return row


def normalize_batch(bronze_records, deduper, quarantine):
    rows = []
    for record in bronze_records:
        row = normalize_bronze_record(record, deduper, quarantine)
        if row is not None:
            rows.append(row)
    return rows
