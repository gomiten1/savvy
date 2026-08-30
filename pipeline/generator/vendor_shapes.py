"""
Construye eventos con el shape JSON exacto de cada vendor (verificado en el
brief) a partir de una representación canónica interna. Usado por el
Generador B (stream en vivo) para producir eventos individuales
vendor-shaped que fluyen por Bronze -> Silver en vivo.

Los 4 shapes (nombres de campo) están verificados contra el brief. Los
valores concretos de decline code vienen de
pipeline/silver/decline_mapping.py (misma tabla que usa el parser, así el
round-trip generar -> parsear es consistente por construcción).
"""
import random
import uuid
from dataclasses import dataclass
from datetime import datetime

from pipeline.domain.decline_mapping import pick_vendor_code
from pipeline.domain.bin_lookup import issuers_for_country
from pipeline.domain.weights import CURRENCY_BY_COUNTRY, SITE_ID_BY_COUNTRY

DLOCAL_METHOD_TYPE = {
    "card": "CARD",
    "oxxo": "TICKET",
    "boleto": "TICKET",
    "pix": "PIX",
    "pse": "PSE",
    "wallet": "WALLET",
}


@dataclass
class CanonicalEvent:
    """Representación interna previa al shaping vendor-specific."""

    txn_id: str
    provider: str
    country: str
    payment_method: str
    amount: float  # unidades mayores (ej. MXN, no centavos)
    approved: bool
    canonical_decline_code: str  # ignorado si approved=True
    created_dt: datetime
    issuer_id: int = None


def _rand_id(rng: random.Random, prefix: str = "") -> str:
    return f"{prefix}{uuid.UUID(int=rng.getrandbits(128), version=4)}"


def pick_issuer_id(rng: random.Random, country: str):
    issuers = issuers_for_country(country)
    if not issuers:
        return None
    issuer_id, _bank = rng.choice(issuers)
    return issuer_id


def stripe_event(ev: CanonicalEvent, rng: random.Random) -> dict:
    payload = {
        "id": _rand_id(rng, "ch_"),
        "amount": int(round(ev.amount * 100)),
        "currency": CURRENCY_BY_COUNTRY[ev.country].lower(),
        "status": "succeeded" if ev.approved else "failed",
        "created": int(ev.created_dt.timestamp()),
    }
    if not ev.approved:
        payload["failure_code"] = pick_vendor_code("stripe", ev.canonical_decline_code, rng)
    return payload


def adyen_event(ev: CanonicalEvent, rng: random.Random) -> dict:
    item = {
        "eventCode": "AUTHORISATION",
        "success": "true" if ev.approved else "false",
        "eventDate": ev.created_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "pspReference": _rand_id(rng).replace("-", "")[:16],
        "amount": {
            "value": int(round(ev.amount * 100)),
            "currency": CURRENCY_BY_COUNTRY[ev.country],
        },
    }
    if not ev.approved:
        item["reason"] = pick_vendor_code("adyen", ev.canonical_decline_code, rng)
    return {"live": "false", "notificationItems": [{"NotificationRequestItem": item}]}


def mercadopago_event(ev: CanonicalEvent, rng: random.Random) -> dict:
    issuer_id = ev.issuer_id if ev.issuer_id is not None else pick_issuer_id(rng, ev.country)
    status_detail = (
        "accredited"
        if ev.approved
        else pick_vendor_code("mercadopago", ev.canonical_decline_code, rng)
    )
    return {
        "id": rng.randint(10_000_000, 99_999_999),
        "status": "approved" if ev.approved else "rejected",
        "status_detail": status_detail,
        "transaction_amount": round(ev.amount, 2),
        "issuer_id": issuer_id,
        "site_id": SITE_ID_BY_COUNTRY[ev.country],
    }


def dlocal_event(ev: CanonicalEvent, rng: random.Random) -> dict:
    status_code = "200" if ev.approved else pick_vendor_code("dlocal", ev.canonical_decline_code, rng)
    return {
        "id": f"D-4-{_rand_id(rng)}",
        "amount": round(ev.amount, 2),
        "status": "PAID" if ev.approved else "REJECTED",
        "status_code": status_code,
        "currency": CURRENCY_BY_COUNTRY[ev.country],
        "country": ev.country,
        "payment_method_type": DLOCAL_METHOD_TYPE.get(ev.payment_method, "OTHER"),
    }


SHAPERS = {
    "stripe": stripe_event,
    "adyen": adyen_event,
    "mercadopago": mercadopago_event,
    "dlocal": dlocal_event,
}


def build_vendor_event(ev: CanonicalEvent, rng: random.Random = None) -> dict:
    rng = rng or random.Random()
    return SHAPERS[ev.provider](ev, rng)
