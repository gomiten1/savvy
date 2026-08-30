"""
Un parser por vendor: payload crudo (shape verificado) -> dict normalizado.
Cualquier excepción (KeyError/TypeError/ValueError/IndexError) señala un
payload malformado — el dispatcher en normalize.py la captura y manda el
registro a quarantine, nunca deja crashear el pipeline.

También expone `extract_native_id`, usado por dedup.py ANTES de parsear
(dedup por ID nativo del vendor).
"""
from datetime import datetime, timezone

from pipeline.generator.vendor_shapes import CURRENCY_BY_COUNTRY, SITE_ID_BY_COUNTRY
from pipeline.silver.decline_mapping import map_to_canonical

CURRENCY_TO_COUNTRY = {v.upper(): k for k, v in CURRENCY_BY_COUNTRY.items()}
SITE_ID_TO_COUNTRY = {v: k for k, v in SITE_ID_BY_COUNTRY.items()}


def _first_notification_item(payload: dict) -> dict:
    return payload["notificationItems"][0]["NotificationRequestItem"]


def extract_native_id(vendor: str, payload: dict):
    if vendor == "stripe":
        return payload["id"]
    if vendor == "adyen":
        return _first_notification_item(payload)["pspReference"]
    if vendor == "mercadopago":
        return payload["id"]
    if vendor == "dlocal":
        return payload["id"]
    raise ValueError(f"unknown vendor: {vendor}")


def parse_stripe(payload: dict) -> dict:
    status_raw = payload["status"]
    if status_raw not in ("succeeded", "failed"):
        raise ValueError(f"unexpected stripe status: {status_raw}")
    status = "approved" if status_raw == "succeeded" else "declined"
    currency = payload["currency"].upper()
    return {
        "native_id": payload["id"],
        "amount": payload["amount"] / 100.0,
        "currency": currency,
        "status": status,
        "raw_decline_code": payload.get("failure_code"),
        "canonical_decline_code": (
            map_to_canonical("stripe", payload.get("failure_code")) if status == "declined" else None
        ),
        "created_dt": datetime.fromtimestamp(payload["created"], tz=timezone.utc),
        "issuer_id": None,
        "parsed_country": CURRENCY_TO_COUNTRY.get(currency),
    }


def parse_adyen(payload: dict) -> dict:
    item = _first_notification_item(payload)
    success_raw = item["success"]
    if success_raw not in ("true", "false"):
        raise ValueError(f"unexpected adyen success value: {success_raw!r}")
    status = "approved" if success_raw == "true" else "declined"
    currency = item["amount"]["currency"].upper()
    return {
        "native_id": item["pspReference"],
        "amount": item["amount"]["value"] / 100.0,
        "currency": currency,
        "status": status,
        "raw_decline_code": item.get("reason"),
        "canonical_decline_code": (
            map_to_canonical("adyen", item.get("reason")) if status == "declined" else None
        ),
        "created_dt": datetime.fromisoformat(item["eventDate"]).astimezone(timezone.utc),
        "issuer_id": None,
        "parsed_country": CURRENCY_TO_COUNTRY.get(currency),
    }


def parse_mercadopago(payload: dict) -> dict:
    status_raw = payload["status"]
    if status_raw not in ("approved", "rejected"):
        raise ValueError(f"unexpected mercadopago status: {status_raw}")
    status = "approved" if status_raw == "approved" else "declined"
    status_detail = payload.get("status_detail")
    return {
        "native_id": str(payload["id"]),
        "amount": float(payload["transaction_amount"]),
        "currency": None,  # no viene en el shape verificado; se infiere por site_id -> country
        "status": status,
        "raw_decline_code": status_detail if status == "declined" else None,
        "canonical_decline_code": (
            map_to_canonical("mercadopago", status_detail) if status == "declined" else None
        ),
        "created_dt": None,  # mercadopago no trae timestamp en el shape verificado
        "issuer_id": payload.get("issuer_id"),
        "parsed_country": SITE_ID_TO_COUNTRY.get(payload.get("site_id")),
    }


def parse_dlocal(payload: dict) -> dict:
    status_raw = payload["status"]
    if status_raw not in ("PAID", "REJECTED"):
        raise ValueError(f"unexpected dlocal status: {status_raw}")
    status = "approved" if status_raw == "PAID" else "declined"
    status_code = payload.get("status_code")
    return {
        "native_id": payload["id"],
        "amount": float(payload["amount"]),
        "currency": payload.get("currency"),
        "status": status,
        "raw_decline_code": status_code if status == "declined" else None,
        "canonical_decline_code": (
            map_to_canonical("dlocal", status_code) if status == "declined" else None
        ),
        "created_dt": None,  # dlocal no trae timestamp en el shape verificado
        "issuer_id": None,
        "parsed_country": payload.get("country"),
    }


PARSERS = {
    "stripe": parse_stripe,
    "adyen": parse_adyen,
    "mercadopago": parse_mercadopago,
    "dlocal": parse_dlocal,
}


def parse(vendor: str, payload: dict) -> dict:
    if vendor not in PARSERS:
        raise ValueError(f"unknown vendor: {vendor}")
    return PARSERS[vendor](payload)
