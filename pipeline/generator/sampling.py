"""
Enumeración de celdas dimensionales + pesos combinados.

Dos grains distintos (ver docs/decision_log.md para la justificación):
  - "rate cell"     = (provider, country, payment_method, issuing_bank)
                      -> usada para el baseline de conversion rate.
  - "recovery cell" = (provider, country, payment_method, canonical_decline_code)
                      -> usada para el baseline de recovery rate.
Ninguna de las dos incluye merchant: el brief valida el volumen mínimo de
celda ("stripe x CO x wallet x 41_43_lost_stolen" = 393.8) usando solo estas
4 dimensiones, sin merchant. merchant_id se asigna a nivel de evento
individual (stream en vivo), no como dimensión del baseline agregado.
"""
from dataclasses import dataclass

from pipeline.domain.weights import (
    PROVIDER_WEIGHTS,
    COUNTRY_WEIGHTS,
    METHOD_WEIGHTS_BY_COUNTRY,
    CANONICAL_DECLINE_WEIGHTS,
)
from pipeline.domain.bin_lookup import issuers_for_country, UNKNOWN_BANK

PROVIDERS = tuple(PROVIDER_WEIGHTS)
COUNTRIES = tuple(COUNTRY_WEIGHTS)
CANONICAL_CODES = tuple(CANONICAL_DECLINE_WEIGHTS)


@dataclass(frozen=True)
class RateCell:
    provider: str
    country: str
    payment_method: str
    issuing_bank: str
    weight: float  # fracción [0,1] del volumen total, suma 1.0 sobre todas las rate cells

    @property
    def key(self):
        return (self.provider, self.country, self.payment_method, self.issuing_bank)


@dataclass(frozen=True)
class RecoveryCell:
    provider: str
    country: str
    payment_method: str
    canonical_decline_code: str

    @property
    def key(self):
        return (self.provider, self.country, self.payment_method, self.canonical_decline_code)


def _methods_for_country(country: str):
    return tuple(METHOD_WEIGHTS_BY_COUNTRY[country])


def enumerate_rate_cells():
    cells = []
    for provider, p_w in PROVIDER_WEIGHTS.items():
        for country, c_w in COUNTRY_WEIGHTS.items():
            for method, m_w in METHOD_WEIGHTS_BY_COUNTRY[country].items():
                base_weight = p_w * c_w * m_w
                if provider == "mercadopago":
                    issuers = issuers_for_country(country)
                    for _issuer_id, bank in issuers:
                        cells.append(
                            RateCell(provider, country, method, bank, base_weight / len(issuers))
                        )
                else:
                    cells.append(RateCell(provider, country, method, UNKNOWN_BANK, base_weight))
    return cells


def enumerate_recovery_cells():
    cells = []
    for provider, _p_w in PROVIDER_WEIGHTS.items():
        for country in COUNTRY_WEIGHTS:
            for method in METHOD_WEIGHTS_BY_COUNTRY[country]:
                for code in CANONICAL_DECLINE_WEIGHTS:
                    cells.append(RecoveryCell(provider, country, method, code))
    return cells


def cell_id(provider, country, method, dim4):
    """dim4 = issuing_bank (rate cell) o canonical_decline_code (recovery cell)."""
    return f"{provider}|{country}|{method}|{dim4}"


def weighted_choice(rng, weights: dict):
    keys = list(weights)
    values = list(weights.values())
    return rng.choices(keys, weights=values, k=1)[0]


def apportion(total: int, weights: dict) -> dict:
    """Reparte un entero `total` entre las claves de `weights` (método de
    remanente mayor / Hamilton) de forma DETERMINÍSTICA — sin muestreo
    nuevo. Se usa para partir un total ya sorteado (attempts, declines por
    código) en sub-totales por merchant sin pagar el costo de volver a
    samplear cada combinación por separado. La suma de los valores devueltos
    es exactamente `total`."""
    if total <= 0:
        return {k: 0 for k in weights}
    keys = list(weights)
    total_weight = sum(weights.values())
    raw = {k: total * weights[k] / total_weight for k in keys}
    base = {k: int(raw[k]) for k in keys}
    remainder = total - sum(base.values())
    if remainder > 0:
        order = sorted(keys, key=lambda k: raw[k] - base[k], reverse=True)
        for k in order[:remainder]:
            base[k] += 1
    return base


def poisson_sample(rng, lam: float) -> int:
    """Sampler Poisson correcto incluso para lambda chico (celdas de bajo
    volumen en un tick corto) — un gauss(mean, sqrt(mean)) redondeado se
    distorsiona fuerte cuando lambda < ~5, que es común acá."""
    if lam <= 0:
        return 0
    if lam < 30:
        import math

        limit = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= limit:
                return k - 1
    return max(0, round(rng.gauss(lam, lam**0.5)))
