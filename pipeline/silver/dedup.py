"""
Dedup por ID nativo del vendor, ANTES de parsear (un vendor puede reenviar
el mismo webhook más de una vez — dedup temprano evita procesar/gastar
trabajo de parsing en algo que ya vimos, y evita doble-conteo en Silver).

Un registro sin ID nativo extraíble (payload malformado) NO se deduplica
aquí — pasa al parser, que lo mandará a quarantine.
"""
from pipeline.silver.parsers import extract_native_id


class Deduper:
    def __init__(self):
        self._seen = set()  # (vendor, native_id)

    def is_duplicate(self, vendor: str, payload: dict) -> bool:
        try:
            native_id = extract_native_id(vendor, payload)
        except Exception:
            return False  # no se pudo extraer id -> no dedupeable, sigue al parser
        if native_id is None:
            return False
        key = (vendor, native_id)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def reset(self):
        self._seen.clear()
