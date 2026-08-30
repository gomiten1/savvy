"""
Mapeo canónico <-> código nativo de cada vendor.

Fuente única de verdad (single source of truth) consumida por:
  - pipeline/generator/vendor_shapes.py (genera el código nativo a partir
    del canonical_decline_code elegido)
  - pipeline/silver/parsers.py (parsea el código nativo de vuelta a
    canonical_decline_code)

Los shapes JSON (nombres de campo) están verificados contra el brief; los
VALORES concretos de cada código (ej. "cc_rejected_expired_card") son una
inferencia razonable siguiendo la convención real de cada vendor, ya que el
brief solo verificó UN ejemplo por vendor. Ver docs/decision_log.md.
"""

CANONICAL_CODES = (
    "51_insufficient_funds",
    "05_do_not_honor",
    "capture_error",
    "54_expired_card",
    "41_43_lost_stolen",
    "57_not_permitted",
    "59_suspected_fraud",
    "61_exceeds_limit",
    "91_96_network_timeout",
)

# vendor -> canonical_code -> [lista de códigos nativos plausibles]
CANONICAL_TO_VENDOR_CODES = {
    "stripe": {
        "51_insufficient_funds": ["insufficient_funds"],
        "05_do_not_honor": ["do_not_honor", "generic_decline"],
        "capture_error": ["processing_error"],
        "54_expired_card": ["expired_card"],
        "41_43_lost_stolen": ["lost_card", "stolen_card"],
        "57_not_permitted": ["restricted_card", "transaction_not_allowed"],
        "59_suspected_fraud": ["fraudulent", "security_violation"],
        "61_exceeds_limit": ["card_velocity_exceeded"],
        "91_96_network_timeout": ["issuer_not_available", "try_again_later"],
    },
    "adyen": {
        "51_insufficient_funds": ["InsufficientFunds", "NotEnoughBalance"],
        "05_do_not_honor": ["Refused", "GenericDecline"],
        "capture_error": ["AcquirerError"],
        "54_expired_card": ["ExpiredCard"],
        "41_43_lost_stolen": ["CardLostOrStolen", "StolenCard"],
        "57_not_permitted": ["RestrictedCard", "TransactionNotPermitted"],
        "59_suspected_fraud": ["FraudSuspected"],
        "61_exceeds_limit": ["WithdrawalAmountExceeded"],
        "91_96_network_timeout": ["IssuerUnavailable", "Timeout"],
    },
    "mercadopago": {
        "51_insufficient_funds": ["cc_rejected_insufficient_amount"],
        "05_do_not_honor": ["cc_rejected_other_reason"],
        "capture_error": ["cc_rejected_card_error"],
        "54_expired_card": ["cc_rejected_expired_card"],
        "41_43_lost_stolen": ["cc_rejected_card_disabled"],
        "57_not_permitted": ["cc_rejected_blacklist"],
        "59_suspected_fraud": ["cc_rejected_high_risk"],
        "61_exceeds_limit": ["cc_rejected_max_attempts"],
        "91_96_network_timeout": ["cc_rejected_call_for_authorize"],
    },
    "dlocal": {
        # status_code (numérico como string). status siempre "REJECTED" para
        # cualquier declined; status_code distingue el motivo.
        "51_insufficient_funds": ["301"],
        "05_do_not_honor": ["300"],
        "capture_error": ["500"],
        "54_expired_card": ["302"],
        "41_43_lost_stolen": ["303"],
        "57_not_permitted": ["304"],
        "59_suspected_fraud": ["305"],
        "61_exceeds_limit": ["306"],
        "91_96_network_timeout": ["402"],
    },
}


def _invert(vendor_table):
    inverted = {}
    for canonical, codes in vendor_table.items():
        for code in codes:
            inverted[code] = canonical
    return inverted


VENDOR_CODE_TO_CANONICAL = {
    vendor: _invert(table) for vendor, table in CANONICAL_TO_VENDOR_CODES.items()
}


def map_to_canonical(vendor: str, raw_code) -> str:
    """Código nativo del vendor -> canonical_decline_code. 'unknown' si no
    se reconoce (vendor nuevo, código nuevo, campo vacío)."""
    if not raw_code:
        return "unknown"
    table = VENDOR_CODE_TO_CANONICAL.get(vendor, {})
    return table.get(str(raw_code), "unknown")


def pick_vendor_code(vendor: str, canonical_code: str, rng) -> str:
    """Elige (random) un código nativo plausible para un canonical_code,
    usado solo por el generador."""
    codes = CANONICAL_TO_VENDOR_CODES[vendor][canonical_code]
    return rng.choice(codes)


def resolve_status(parsed_status: str, canonical_decline_code) -> str:
    """approved/declined/error final, para el contrato con detección
    (DATA-CONTRACT.md): un timeout de red es una falla de infraestructura,
    no un rechazo de negocio, así que se reclasifica a "error" incluso
    aunque el parser lo haya visto como "declined". Ver
    ERROR_STATUS_CANONICAL_CODES en weights.py."""
    from pipeline.generator.weights import ERROR_STATUS_CANONICAL_CODES

    if parsed_status == "declined" and canonical_decline_code in ERROR_STATUS_CANONICAL_CODES:
        return "error"
    return parsed_status
