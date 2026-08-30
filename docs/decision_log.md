# Decision log — Generación de datos + Preprocesamiento (Bronze/Silver)

Este documento registra las decisiones tomadas al construir la capa
Bronze/Silver descrita en el brief, especialmente las que el brief dejó
implícitas o que generaron una tensión real entre dos instrucciones. Todas
están también como comentarios `ASUNCIÓN`/nota junto al código relevante —
esto es el resumen consolidado.

## Por qué dos generadores

El formato del hackathon (10 min/equipo, demo real de 2-3 min, trial by
fire) prohíbe generar 14 días de histórico en vivo. Por eso:

- **Generador A** (`generate_historical_aggregates.py`) corre UNA vez,
  antes de la demo, y escribe agregados por minuto x celda a `data/gold/`
  (Parquet + DuckDB, ver sección "Gold layer" más abajo). No genera eventos
  individuales — sería
  estadísticamente equivalente y ~2 órdenes de magnitud más pesado sin
  aportar nada (nadie va a ver una transacción individual del día 3 del
  histórico durante la demo).
- **Generador B** (`generate_live_stream.py`) corre durante la demo, con el
  reloj comprimido 10x (`DEMO_SPEED_MULTIPLIER`), y SÍ produce eventos
  individuales vendor-shaped porque tienen que fluir visiblemente por
  Bronze -> Silver frente al panel.

## Scope: dónde termina este trabajo

Se construyó exactamente Bronze + Silver (normalize, baseline, recovery),
schema de salida incluido. NO se construyó: ranking de root-cause entre
celdas correlacionadas, capa de explicación LLM, ni webhook a Slack — eso
es otro módulo (Gold), fuera de este scope por instrucción explícita del
brief.

## El schema canónico tiene dos "grains" en una sola forma

El schema de salida (sección 7 del brief) mezcla campos de evento individual
(`status`, `amount`, `attempt_number`, `linked_order_id`) con campos de
agregado (`attempts`, `approvals`, `actual_rate`, `expected_rate`,
`deviation_index`, `recovery_rate`, `confidence`). En vez de agregar un
campo nuevo tipo `record_type` (que sí sería tocar el contrato), se resolvió
así:

- **Filas "evento"** (las que produce `normalize.py` a partir del stream en
  vivo): `status`/`amount`/`attempt_number`/`linked_order_id` poblados,
  todo lo de agregado en `None`.
- **Filas "celda agregada"** (las que produce `baseline.py` /
  `recovery.py` sobre el histórico): `attempts`/`approvals`/`actual_rate`/
  baseline/recovery poblados; `status`/`amount`/`attempt_number` no
  aplican.

Un consumidor puede distinguir el tipo mirando si `attempts` es `None`.

## Dos grains de celda dimensional (ninguno incluye merchant)

- **Rate cell** = `(provider, country, payment_method, issuing_bank)` — la
  que usa `baseline.py` para expected_rate/std/deviation_index.
- **Recovery cell** = `(provider, country, payment_method,
  canonical_decline_code)` — la que usa `recovery.py`/el post-proceso del
  Generador A.

Ninguna de las dos incluye `merchant_id`, porque el propio ejemplo de
volumen validado en el brief ("`stripe x CO x wallet x
41_43_lost_stolen` alcanza 393.8 transacciones") usa exactamente esas 4
dimensiones sin merchant. Si merchant fuera una dimensión más del baseline,
esa celda se dividiría entre 6 merchants y el número validado no cuadraría.
`merchant_id` sí se captura a nivel de evento individual (para que Gold
pueda cortar por merchant sobre el stream en vivo si lo necesita), pero el
baseline pre-calculado (que existe justamente para ser rápido en la demo)
lo poolea.

## Gaps entre los shapes de vendor y el schema canónico

Los 4 shapes están verificados tal cual el brief los da — no se les agregó
ni quitó ningún campo. Pero **ninguno de los 4 trae `payment_method`, y
solo dLocal trae `country` directo, y solo MercadoPago trae algo mapeable a
`issuing_bank` (`issuer_id`)**. Tampoco traen `merchant_id`,
`attempt_number` ni `linked_order_id` — datos que un orquestador real ya
conoce ANTES de llamar al vendor (decidió enrutar a X porque el usuario
eligió PIX, para el merchant Y, es el intento N de la orden Z), no algo que
el vendor devuelva en su respuesta.

Resolución: el registro Bronze (`bronze_store.py`) guarda el `payload`
crudo del vendor (intacto) más un `routing_metadata` sibling con ese
contexto del orquestador. `normalize.py` combina ambos: verdad de
settlement (amount/status/decline code) del payload, verdad de ruteo
(payment_method/country/merchant/attempt) de `routing_metadata`. Si
`routing_metadata` no trae algo, el campo queda `None` — no se inventa un
default (ej. no se asume que Stripe/Adyen "solo procesan card": el brief da
`PROVIDER_WEIGHTS` y `METHOD_WEIGHTS_BY_COUNTRY` como tablas independientes
sin matriz de compatibilidad, así que el generador sí produce combinaciones
como `adyen+oxxo` — swaps raros en la vida real, pero consistentes con los
números que el brief pidió usar tal cual).

`created_dt`: MercadoPago y dLocal tampoco traen timestamp en el shape
verificado. Fallback: se usa el `ingested_at` de Bronze (hora de ingesta),
razonable para un pipeline en tiempo real donde el delay vendor->bronze es
de milisegundos.

## Decline codes: una sola fuente de verdad, valores inferidos

`pipeline/silver/decline_mapping.py` tiene la tabla canonical_code ->
[códigos nativos plausibles] por vendor, en ambas direcciones (generación y
parseo la comparten, así el roundtrip es consistente por construcción — ver
`test_generator.DeclineMappingRoundTripTest`). El brief verificó UN ejemplo
de código por vendor (`insufficient_funds`, `ExpiredCard`,
`cc_rejected_insufficient_amount`); los otros 8 valores por vendor son una
inferencia razonable siguiendo la convención real de cada uno. Para dLocal
en particular, que no trae razón de rechazo en texto (solo `status_code`
numérico) y el brief no dio la tabla real, se inventó un esquema
`3xx`/`500` documentado — el campo (`status_code`) es real, los valores
concretos no.

**Ojo con `CANONICAL_DECLINE_WEIGHTS`:** tal como está en el brief, suma
**1.08**, no 1.0 (`0.50+0.25+0.20+0.03+0.02*5`). Se usa tal cual (no se
tocó el diccionario), pero `distribute_declines()` en
`generate_historical_aggregates.py` normaliza antes de repartir declines
entre códigos (repartir un total conocido entre categorías tiene que sumar
ese total). El ejemplo de volumen validado del brief (393.8) sí reproduce
exacto usando los pesos crudos sin normalizar — confirmado en
`test_generator.VolumeValidationTest`, que documenta ambas versiones.

## `issuing_bank`: solo MercadoPago lo resuelve

Ningún otro shape trae dato de BIN/issuer. `bin_lookup.py` tiene una tabla
`issuer_id -> banco` inventada pero plausible (3 bancos grandes reales por
país). Los otros 3 providers siempre resuelven a `unknown_bank`.

## `merchant_id`: dimensión no dada por el brief

El brief lista merchant como dimensión de root-cause y el schema lo pide,
pero no da `MERCHANT_WEIGHTS`. Se usó un set de 6 merchants sintéticos con
distribución tipo Zipf (`weights.py`). No afecta el baseline (ver arriba).

## Histórico limpio, incidentes solo en vivo/tests

El histórico de 14 días del Generador A **no tiene incidentes inyectados**.
Es la base con la que se aprende `expected_rate`/`expected_std` por celda —
un incidente ahí distorsionaría el propio baseline que se supone debe
detectarlo después. Sí incluye estacionalidad normal (día de semana, hora
del día, y un evento estacional — `black_friday_mx`, un día completo dentro
de la ventana de 14 días, solo para ejercitar ese código). La estacionalidad
mueve **volumen**, nunca approval rate, así que un pico estacional nunca
dispara el detector de conversion rate por construcción — no hace falta
lógica especial para distinguir "pico normal" de "anomalía", quedan
separados por diseño.

Los incidentes viven en `inject_incidents.py` y se usan en dos lugares: (1)
el Generador B para la demo/trial-by-fire (`trigger_incident()` en caliente,
o un JSON en `data/live/incident_trigger.json` que el loop consume sin
reiniciar el proceso), y (2) los tests, para probar que la detección
funciona (`test_baseline.LiveIncidentDetectionTest`).

## Baseline: por qué se poolea por minuto-del-día ignorando weekday

Con 14 días de historia, separar por (weekday, minuto) da ~2 muestras por
bucket — muy poco para estimar un std razonable.
`WEEKDAY_INDEX` en este generador solo mueve **volumen**, no approval rate
(la tasa de aprobación no tiene motivo estructural para variar por día de
semana en este modelo). Entonces poolear los 14 días por minuto-del-día
(ignorando weekday) da 14 muestras por bucket en vez de 2, sin perder
información real, porque no hay señal de rate específica de weekday que se
esté promediando.

Escala de confianza (los 4 valores que pide el schema):

| confidence | condición |
|---|---|
| `insufficient_history` | `days_available < MIN_DAYS_FOR_DAILY_PATTERN` (2) |
| `insufficient_sample` | `total_attempts` de la celda (acumulado, no por minuto) `< MIN_SAMPLE_SIZE_PER_CELL` (350) |
| `wide_band` | `days_available < MIN_DAYS_FOR_WEEKLY_PATTERN` (14) |
| `reliable` | `days_available >= 14` |

`DAYS_UNTIL_BAND_IS_TIGHT` (28) no es un 5to nivel de confianza — el schema
solo define esos 4. Se usa para seguir angostando el std *dentro* de
"reliable" a medida que se junta historia más allá de los 14 días de este
demo (con exactamente 14 días fijos, siempre queda en el extremo ancho de
"reliable" — comportamiento correcto y esperado, documentado por si se
corre con más historia en el futuro).

`deviation_index` es un z-score con piso mínimo de std
(`MIN_STD_FLOOR=0.02`, calibrado al ruido natural que el generador inyecta
por minuto) y ensanchamiento de banda (`WIDE_BAND_STD_MULTIPLIER=1.5`)
mientras la confianza no es plenamente "reliable".

`ALERT_Z_THRESHOLD` (3.0) y `ALERT_MIN_CONSECUTIVE_MINUTES` (3) **no son
parte del pipeline** (eso es criterio de alerting/ranking de Gold) — son
solo una heurística de test para poder validar el presupuesto de "<15
alertas/semana sin incidente" del brief sin contar cada minuto como una
alerta separada (un incidente de 90 minutos debe colapsar en ~1 alerta, no
90).

## Recovery: interpretación de `RECOVERY_BY_ATTEMPT`

Se interpretó la clave `N` del diccionario como "probabilidad de que el
reintento que sigue al intento fallido N tenga éxito". Con
`MAX_ATTEMPTS_PER_TRANSACTION=3`, solo se disparan reintentos para intentos
fallidos N=1 (-> attempt_number 2) y N=2 (-> attempt_number 3); la clave 3
queda documentada pero nunca se alcanza — coherente con "no simular más de
3 intentos".

En el stream en vivo, si un intento se declina, la probabilidad de que
SIQUIERA se reintente (no la de que tenga éxito) se modela como
`min(0.95, RECOVERY_RATE_BY_CANONICAL_CODE[code] * 1.4)` — un código con
baja recuperabilidad (ej. `41_43_lost_stolen`, 0.02) casi nunca se
reintenta; uno alto (ej. `capture_error`, 0.65) casi siempre. El reintento
se agenda con un delay aleatorio de 5-30 segundos simulados (visible en el
demo, no instantáneo).

Para el histórico agregado (Generador A), no se trackea order_id a
order_id — se calcula `decline_cells_hourly` directamente: declines por
hora x celda de recovery, y `recovered = declines * (RECOVERY_RATE_BY_CANONICAL_CODE
+ ruido)`. El tracking fino por order_id (`RecoveryTracker` en
`recovery.py`) es solo para el stream en vivo, donde attempt_number/
linked_order_id sí existen por evento.

`recovery_rate_deviation` es una diferencia simple
(`observado - esperado`), no un z-score — el brief pide compararlo contra
la constante `RECOVERY_RATE_BY_CANONICAL_CODE`, no contra un baseline
histórico aprendido como en `deviation_index`.

## Otras asunciones menores (impacto bajo, documentadas por transparencia)

- Moneda por país uniforme en los 4 vendors (MX->MXN, BR->BRL, CO->COP),
  aunque el ejemplo de dLocal en el brief usa USD/AR (ese ejemplo era solo
  para verificar el campo `currency`, no una instrucción de moneda).
- Montos de transacción por país (`AMOUNT_RANGE_BY_COUNTRY` en
  `generate_live_stream.py`) son un rango arbitrario solo para que la demo
  se vea realista — no afectan ninguna métrica.
- Muestreo Poisson (algoritmo de Knuth para lambda < 30, Gauss arriba de
  eso) en vez de Gauss puro para todo — con celdas de bajo volumen y ticks
  cortos, lambda suele ser < 5, donde Gauss redondeado se distorsiona.

## Reconciliación con DATA-CONTRACT.md (detection/diagnosis)

El equipo de detección/diagnóstico mandó un DRAFT de contrato de datos
(`DATA-CONTRACT.md`, en Desktop, owners "Maca, Malu"). Se revisó campo por
campo contra lo que ya estaba construido. Decisiones (Maca dio la señal de
"ustedes deciden" en varios puntos):

**Taxonomías: se mantienen las nuestras, se ignoran las del contrato.**
El contrato pide 3 providers, un `decline_code` de 6 valores sin prefijo
numérico (`insufficient_funds`, `do_not_honor`, `risk_blocked`,
`provider_timeout`, `invalid_card`, `3ds_failed`), y menciona `spei` como
method. Nada de eso está en el brief original, que sí fue validado
matemáticamente (los 4 providers, los 9 códigos canónicos, los métodos por
país). Se mantiene el brief tal cual: **4 providers**, **9 canonical
decline codes**, métodos sin `spei`. El equipo de detección tiene que
consumir estos valores, no los suyos — si de verdad necesitan otra
taxonomía, es una conversación de negocio, no algo que se resuelve
cambiando el generador.

**Merchants: se mantienen los 6 ya construidos** (el contrato pide 3, pero
el brief nunca dio un número — ver la sección de `MERCHANT_WEIGHTS` más
arriba). Cambiarlo a 3 es trivial si hace falta.

**El gap real: no había forma de consultar los datos.** El contrato pide
`get_counts()`/`get_samples()` ("we poll, we do not consume a stream") y
antes de esto el pipeline no exponía nada así — Bronze era un JSONL, el
histórico eran dos tablas SQLite con grain fijo (no cualquier
`group_by`), y no había ninguna tabla a nivel de intento individual
persistida más allá de la ventana de vida de un proceso Python. Esto se
resolvió construyendo un **Gold layer relacional de verdad**
(`pipeline/gold/`), tres tablas (nota: la primera versión de esto fue
SQLite para las tres; se migró a Parquet + DuckDB para las dos grandes
poco después — ver la sección "Gold layer: Parquet + DuckDB" más abajo
para el porqué):

```
rate_cells_minutely   grain: minuto x (merchant, provider, country, method, bank)
                       SIN decline_code -> columnas attempts/approved/declined/error
                       + amount_usd_total. ~5.7M filas para 14 días.

decline_cells_hourly  grain: hora x (merchant, provider, country, method, bank,
                       decline_code) -> declines/recovered/amount_usd_total.
                       ~150K filas para 14 días.

live_attempts          grain: 1 fila = 1 intento real (solo Generador B).
                        Acá SÍ están las 6 dimensiones completas por fila,
                        exactamente el shape que pide la Sección 2 del
                        contrato (attempt_id, payment_id, event_ts,
                        amount_minor, currency, amount_usd, status con
                        approved/declined/error).
```

**Por qué NO es una sola tabla de intentos para los 14 días completos**:
el brief original fue explícito en no generar 31.5M filas individuales de
histórico (por velocidad de demo). Una tabla de intentos con las 6
dimensiones completas a resolución de minuto para 14 días sí llegaría a
esa escala. La solución de compromiso: `rate_cells_minutely` mantiene
resolución de minuto pero sin decline_code (la dimensión que más multiplica
filas); `decline_cells_hourly` sí abre por decline_code pero a resolución
de hora. Ambas siguen siendo agregados PRECALCULADOS, no eventos
individuales — coherente con "correr una vez, guardar a disco" del brief.
`live_attempts`, en cambio, sí es grain real, porque el volumen de la
ventana en vivo (~300/seg, minutos de demo) lo permite sin problema, y es
justamente donde `get_samples()` (evidencia cruda para la alerta) tiene
sentido — sobre el histórico agregado no hay filas individuales que devolver,
y `get_samples()` lo refleja devolviendo lista vacía en vez de inventar
datos.

**`get_counts()`/`get_samples()`** (`pipeline/gold/access.py`) ruteán la
consulta según de qué lado de `history_end` (meta) cae el rango pedido:
antes -> tablas precalculadas (decline_cells_hourly si `decline_code` está
en `group_by`/`filters`, si no `rate_cells_minutely`); después ->
`live_attempts` con flexibilidad total. Si el rango pisa las dos fuentes,
se consultan ambas y se concatena (el bucket exacto en el borde puede salir
partido en dos filas en vez de una sola sumada — aceptable para un demo,
documentado en el código).

**Nomenclatura**: las tablas Gold usan los nombres de columna del contrato
(`provider_id`, `method`, `decline_code`, `attempt_id`, `payment_id`,
`amount_minor`, `amount_usd`) para que detección pueda consumirlas
directo. Los valores de esas columnas siguen siendo los nuestros (4
providers, 9 decline codes). Internamente, Silver (`normalize.py`) sigue
usando sus propios nombres (`provider`, `payment_method`,
`canonical_decline_code`, `linked_order_id`) — `pipeline/gold/materialize.py`
es la única capa que traduce de uno a otro, así el resto del pipeline no
se tocó más de lo necesario ("mantenerlo lo más parecido posible a como ya
funcionaba").

## Gold layer: Parquet + DuckDB (post-mortem de una decisión que cambió dos veces)

Después de construir el Gold layer en SQLite (arriba), Maca pidió pasar la
parte analítica a un formato columnar de verdad — el argumento: SQLite es
row-store, esto es un workload OLAP moviendo millones de filas, y
`rate_cells_minutely` ya pesaba **1.0GB** como SQLite para solo 14 días.
Pidió Parquet específicamente.

**Intento 1 — todo en un solo motor DuckDB, con `live_attempts` como
tabla nativa DuckDB** (Maca no quería un diseño "hecho para hackathon" con
dos motores distintos si había una opción sólida con uno solo). La premisa
técnica de esto era: DuckDB soporta "un escritor + N lectores
`read_only=True` concurrentes", así que un solo archivo `gold.duckdb`
alcanzaría para todo, incluida la tabla chica que el Generador B escribe
en vivo mientras detección la lee desde otro proceso.

**Esa premisa era falsa.** Se armó un spike real (dos procesos: uno
sostiene una conexión de escritura abierta, otro intenta abrir
`read_only=True` y hacer un SELECT) y dio **60/60 lecturas fallidas**:

```
IOException: Could not set lock on file "...": Conflicting lock is held
in [...] (PID ...). See also https://duckdb.org/docs/stable/connect/concurrency
```

El modelo real de DuckDB para un archivo es: un solo proceso puede tenerlo
abierto, punto — ni siquiera en modo `read_only` si otra conexión (de
escritura o no) ya lo tiene abierto. No es "un escritor + lectores
concurrentes" como en SQLite; es "un proceso a la vez, sin excepciones,
mientras haya alguna conexión abierta". Se verificó por separado que
múltiples conexiones `read_only=True` SÍ conviven bien entre sí cuando NO
hay ningún escritor con el archivo abierto — por eso `historical.duckdb`
(que el Generador A escribe una sola vez y nunca más) es seguro de leer
desde cualquier cantidad de procesos concurrentes.

**Intento 2 — y el que quedó**: exactamente el híbrido que se había
descartado por "sentirse hecho para hackathon", pero ahora confirmado como
la única opción sólida, no una concesión:

- `rate_cells_minutely.parquet` / `decline_cells_hourly.parquet` +
  `historical.duckdb` (VIEWs sobre esos parquet + tabla `meta`) — escrito
  UNA vez por el Generador A, de solo lectura para siempre después.
  Cualquier número de lectores concurrentes (`read_only=True`) anda bien
  porque nunca hay un escritor conviviendo.
- `live.sqlite` (`live_attempts`) — SQLite, no DuckDB. Es la única tabla
  que de verdad necesita lector+escritor concurrentes (el Generador B
  escribiendo mientras detección hace poll desde otro proceso — el
  escenario real de trial-by-fire), y ese es exactamente el patrón que
  SQLite soporta nativo. Se verificó con el mismo tipo de spike (writer +
  reader en procesos separados, esta vez contra SQLite): **40/40 lecturas
  exitosas** mientras el writer emitía 3672 eventos en 10s.

Esto NO es "dos motores porque no pudimos decidirnos" — es "hot path
transaccional chico -> el motor hecho para eso; cold path analítico
grande -> el motor hecho para eso", el mismo patrón que separa un
operational store de un data warehouse en cualquier plataforma de datos
real.

**Gotcha extra, no relacionado a concurrencia**: `duckdb.Connection.executemany()`
vía el binding de Python resultó ser ~700x más lento que el de SQLite para
inserts fila a fila (~2.800 filas/seg vs. ~2M filas/seg, medido) — a esa
velocidad cargar 5.7M filas hubiera tardado media hora. La solución: el
Generador A acumula el histórico en un SQLite **temporal** (mismo patrón
rápido de siempre, `insert_rate_rows`/`insert_decline_rows` sin cambios —
el SQL con placeholders `?` es idéntico en ambos motores) y recién al
final DuckDB hace UN `COPY` masivo y vectorizado desde ese SQLite a
Parquet vía su extensión `sqlite_scanner` (`ATTACH ... (TYPE sqlite)`) —
esa ruta sí es rápida (~2s para exportar 5.7M+150K filas). DuckDB es
rapidísimo para volcados masivos vectorizados; lo que no es rápido es
alimentarlo fila a fila desde Python — la separación Parquet/SQLite del
diseño no cambia por esto, solo cómo se llega a los archivos Parquet.

**Resultado medido**: `data/gold/` completo (historical.duckdb +
2 parquet) pesa **~11MB** para 14 días de histórico — el archivo SQLite
equivalente pesaba 1.0GB (~90x más chico gracias a compresión ZSTD
columnar). El Generador A completo (generación + accumulate SQLite +
export Parquet) corre en ~29s, comparable o más rápido que la versión
SQLite pura de antes.

Nueva dependencia: `duckdb` (pip), la primera dependencia externa formal
del repo (antes todo era stdlib). Requiere un virtualenv (`.venv/`,
gitignored) porque el Python del sistema es Homebrew/externally-managed y
rechaza `pip install` directo (PEP 668) — correr con `.venv/bin/python3`,
no con el `python3` del PATH.

**Otros gaps del contrato, cerrados:**
- `status` ahora tiene 3 valores (`approved`/`declined`/`error`), no 2.
  Solo `91_96_network_timeout` se reclasifica a `error` (ver
  `ERROR_STATUS_CANONICAL_CODES` en `weights.py` y `resolve_status()` en
  `decline_mapping.py`) — es la única falla de infraestructura genuina
  entre los 9 códigos; el resto son rechazos de negocio.
- `amount_usd`: se agregó `FX_RATE_TO_USD` (tasas estáticas de referencia,
  no tiempo real — es un demo) en `weights.py`, aplicado en `normalize.py`
  para eventos individuales y en el post-proceso del Generador A para los
  agregados (`amount_usd_total = attempts x monto_promedio_del_país_en_usd`).
- `currency`: ahora es un campo real en la fila de Silver (antes se
  calculaba internamente pero nunca se exponía).
- `event_ts`: se agregó como campo separado de `time_bucket` — `time_bucket`
  sigue floored al minuto (lo usa el baseline por minuto-del-día);
  `event_ts` guarda el timestamp real del evento, sin bucketear, para que
  freshness/ordering tengan sentido del lado de detección.
- `attempt_id`/`amount_minor`: solo existen en la tabla Gold
  (`live_attempts`), construidos por `GoldWriter` (`{provider}:{native_id}`
  y `round(amount*100)` respectivamente) — no se tocó el schema interno de
  Silver para esto, ya que ahí `_native_id`/`amount` (unidades mayores)
  siguen siendo suficientes para el resto del pipeline.
- **Fuga de la inyección**: el trigger de incidentes en vivo imprimía por
  stdout qué incidente se había disparado, violando "the injector must NOT
  tell us what it injected" del contrato. `LiveStreamGenerator` ahora tiene
  `reveal_injections=False` por default (silencioso); el operador puede
  prenderlo para su propio debugging con `--reveal-injections` en el CLI,
  pero nunca por default.

**Sin resolver / seguir hablando con el equipo de detección:**
- `issuing_bank` solo se resuelve a un banco real para MercadoPago (la
  única de las 4 shapes verificadas que trae `issuer_id`) — para
  Stripe/Adyen/dLocal siempre es `"unknown_bank"`, incluso en transacciones
  `method=card`. El contrato asume que debería resolver siempre que el
  método sea card; en este dataset simulado no es así, y no hay forma de
  arreglarlo sin inventar un campo que ningún shape de vendor verificado
  trae.
- El "UI/endpoint" de inyección de la Sección 5 del contrato sigue siendo
  una función Python + un trigger file JSON (`write_incident_trigger` /
  `data/live/incident_trigger.json`), no una interfaz con UI. Suficiente
  para que alguien con terminal dispare el trial-by-fire, no para que un
  juez lo haga sin ayuda.

## Validación cruzada con el número del brief

`test_generator.VolumeValidationTest` reconstruye la fórmula
(`PROVIDER_WEIGHTS x COUNTRY_WEIGHTS x METHOD_WEIGHTS x (1-APPROVAL_RATE) x
CANONICAL_DECLINE_WEIGHTS x estacionalidad promedio`) y confirma que da
393.8 ± 5 para la celda `stripe x CO x wallet x 41_43_lost_stolen` — el
número exacto que el brief dice "ya validado, no recalcular". Esto
confirma que el modelo de generación implementado es el mismo que se usó
para validar el volumen en el brief.
