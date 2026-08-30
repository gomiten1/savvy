"""
Constantes estadísticas del generador. Copiadas tal cual del brief
(docs/decision_log.md documenta la fuente y las decisiones no cubiertas
por el brief, como MERCHANT_WEIGHTS y BANKS_BY_COUNTRY).
"""

# ---------------------------------------------------------------------------
# Aprobación por proveedor
# ---------------------------------------------------------------------------
APPROVAL_RATE = {"stripe": 0.90, "adyen": 0.90, "mercadopago": 0.70, "dlocal": 0.70}

# ---------------------------------------------------------------------------
# Pesos de distribución
# ---------------------------------------------------------------------------
PROVIDER_WEIGHTS = {"stripe": 0.25, "adyen": 0.25, "mercadopago": 0.30, "dlocal": 0.20}
COUNTRY_WEIGHTS = {"MX": 0.40, "BR": 0.35, "CO": 0.25}
METHOD_WEIGHTS_BY_COUNTRY = {
    "MX": {"card": 0.55, "oxxo": 0.30, "wallet": 0.15},
    "BR": {"card": 0.45, "pix": 0.40, "boleto": 0.15},
    "CO": {"card": 0.50, "pse": 0.40, "wallet": 0.10},
}

# ASUNCIÓN (no especificada en el brief): distribución de merchants.
# El schema canónico y el diagnóstico root-cause requieren merchant_id como
# dimensión, pero el brief no da pesos. Se usa un set pequeño con
# distribución tipo Zipf para que el demo tenga variedad sin explotar el
# número de celdas dimensionales. Ver docs/decision_log.md.
# DECISIÓN (vs. DATA-CONTRACT.md, que pide 3 merchants): se mantienen los 6
# de acá — el brief nunca dio un número, y el pipeline/tests ya están
# construidos sobre 6. Si el equipo de detección necesita exactamente 3,
# es un cambio de una sola línea acá.
MERCHANT_WEIGHTS = {
    "merch_globex": 0.30,
    "merch_acme": 0.22,
    "merch_umbrella": 0.16,
    "merch_initech": 0.13,
    "merch_stark": 0.11,
    "merch_wayne": 0.08,
}

# ---------------------------------------------------------------------------
# Taxonomía canónica de decline codes
# ---------------------------------------------------------------------------
CANONICAL_DECLINE_WEIGHTS = {
    "51_insufficient_funds": 0.50,
    "05_do_not_honor": 0.25,
    "capture_error": 0.20,
    "54_expired_card": 0.03,
    "41_43_lost_stolen": 0.02,
    "57_not_permitted": 0.02,
    "59_suspected_fraud": 0.02,
    "61_exceeds_limit": 0.02,
    "91_96_network_timeout": 0.02,
}

RECOVERY_RATE_BY_CANONICAL_CODE = {
    "51_insufficient_funds": 0.55,
    "05_do_not_honor": 0.35,
    "capture_error": 0.65,
    "91_96_network_timeout": 0.60,
    "54_expired_card": 0.10,
    "41_43_lost_stolen": 0.02,
    "57_not_permitted": 0.05,
    "59_suspected_fraud": 0.05,
    "61_exceeds_limit": 0.55,
}

RECOVERY_BY_ATTEMPT = {1: (0.40, 0.60), 2: (0.15, 0.25), 3: (0.10, 0.15)}
# después del 3er intento, cae fuerte — no simular más de 3 intentos por transacción
MAX_ATTEMPTS_PER_TRANSACTION = 3

# ---------------------------------------------------------------------------
# Confianza estadística / cold start
# ---------------------------------------------------------------------------
MIN_DAYS_FOR_DAILY_PATTERN = 2
MIN_DAYS_FOR_WEEKLY_PATTERN = 14
DAYS_UNTIL_BAND_IS_TIGHT = 28
MIN_SAMPLE_SIZE_PER_CELL = 350

# ASUNCIÓN: piso mínimo de std para el z-score (evita explosión de
# deviation_index en celdas de varianza casi nula) y multiplicador de
# ensanchamiento de banda mientras la confianza es "wide_band".
MIN_STD_FLOOR = 0.02
WIDE_BAND_STD_MULTIPLIER = 1.5

# ---------------------------------------------------------------------------
# Reloj de incidentes
# ---------------------------------------------------------------------------
MTTR_RANGE_MINUTES = (22, 240)  # 22min automatizado -> 4hrs manual

# ---------------------------------------------------------------------------
# Presupuesto de ruido de alertas (test de validación)
# ---------------------------------------------------------------------------
MAX_ALERTS_PER_WEEK_NORMAL = 15
# ASUNCIÓN: umbral en desviaciones estándar para considerar una celda en
# estado de alerta, y cuántos minutos consecutivos por encima del umbral
# colapsan en UNA sola alerta (en vez de una por minuto).
ALERT_Z_THRESHOLD = 3.0
ALERT_MIN_CONSECUTIVE_MINUTES = 3

# ---------------------------------------------------------------------------
# Volumen
# ---------------------------------------------------------------------------
BASE_TXNS_PER_MINUTE = 2000
HISTORICAL_DAYS = 14
DEMO_SPEED_MULTIPLIER = 10  # 1 minuto simulado ~= 6 segundos reales

# ---------------------------------------------------------------------------
# Gold layer / data contract con detection+diagnosis (ver DATA-CONTRACT.md
# y docs/decision_log.md, sección "Reconciliación con DATA-CONTRACT.md")
# ---------------------------------------------------------------------------

# El contrato pide un 3er status además de approved/declined: "error"
# (fallas de infraestructura/proveedor, no rechazos de negocio). De los 9
# canonical_decline_code, solo el de timeout de red representa eso — el
# resto son declines de negocio genuinos.
ERROR_STATUS_CANONICAL_CODES = {"91_96_network_timeout"}

# ASUNCIÓN: el contrato pide amount_usd "precomputado por el pipeline"
# (asunción explícita del lado de detección: "we never do FX"). Tasas
# estáticas de referencia, no tiempo real — es un demo, no hace falta más.
FX_RATE_TO_USD = {
    "MXN": 1 / 18.5,
    "BRL": 1 / 5.4,
    "COP": 1 / 4100.0,
    "USD": 1.0,
}

# Rango de montos por país (unidades mayores) — antes vivía en
# generate_live_stream.py; se centraliza acá porque ahora tanto el
# Generador A (para amount_usd_total agregado) como el B (por evento) lo
# necesitan.
AMOUNT_RANGE_BY_COUNTRY = {"MX": (150, 3500), "BR": (50, 1200), "CO": (20000, 450000)}
