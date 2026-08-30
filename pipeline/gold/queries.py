"""
Consultas de bajo nivel contra el storage de Gold, compartidas por más de
un caller para que el nombre de tabla/columnas de rate_cells_minutely viva
en un solo lugar.

fetch_rate_cells_pooled_by_minute() es usada por
pipeline/silver/baseline.py (BaselineStore.load(), un scan completo del
histórico pooleado por minuto-del-día para entrenar el baseline) -- una
forma de consulta distinta a la de access.py's _query_rate_cells
(parametrizada por rango/bucket/group_by del caller), así que no se
fusionan en una sola función, pero el texto SQL vive acá una sola vez.
"""

RATE_CELLS_POOLED_BY_MINUTE_SQL = """
    SELECT time_bucket, minute_of_day, cell_id,
           SUM(attempts) AS attempts, SUM(approved) AS approved
    FROM rate_cells_minutely
    GROUP BY cell_id, time_bucket, minute_of_day
"""


def fetch_rate_cells_pooled_by_minute(conn):
    """conn: conexión DuckDB read-only abierta contra historical.duckdb.
    Devuelve tuplas (time_bucket, minute_of_day, cell_id, attempts, approved)
    pooleadas across merchants (merchant no es dimensión del baseline)."""
    return conn.execute(RATE_CELLS_POOLED_BY_MINUTE_SQL).fetchall()
