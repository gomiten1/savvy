"""
Lookup de issuing_bank.

Solo MercadoPago expone `issuer_id` (numérico) en su shape verificado — los
otros 3 vendors no traen ningún dato de BIN/issuer en los shapes del brief,
así que su issuing_bank siempre resuelve a "unknown_bank".

ASUNCIÓN (no dada por el brief, solo se pidió "necesita lookup table"): la
tabla issuer_id -> banco de abajo es inventada pero plausible (bancos reales
y grandes de cada país). Ver docs/decision_log.md.
"""

UNKNOWN_BANK = "unknown_bank"

# país -> [(issuer_id, nombre_banco), ...]
ISSUERS_BY_COUNTRY = {
    "MX": [(25, "bbva"), (26, "santander"), (27, "banorte")],
    "BR": [(101, "itau"), (102, "bradesco"), (103, "nubank")],
    "CO": [(201, "bancolombia"), (202, "davivienda"), (203, "bbva")],
}

ISSUER_ID_TO_BANK = {
    issuer_id: bank
    for issuers in ISSUERS_BY_COUNTRY.values()
    for issuer_id, bank in issuers
}


def issuers_for_country(country: str):
    return ISSUERS_BY_COUNTRY.get(country, [])


def lookup_bank(issuer_id) -> str:
    if issuer_id is None:
        return UNKNOWN_BANK
    try:
        issuer_id = int(issuer_id)
    except (TypeError, ValueError):
        return UNKNOWN_BANK
    return ISSUER_ID_TO_BANK.get(issuer_id, UNKNOWN_BANK)
