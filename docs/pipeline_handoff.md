# Pipeline handoff — context for an agent building on top of this

**Read this if you are:** an AI coding agent (or a human) picking up the
detection/diagnosis module — root-cause ranking across dimension
intersections, the LLM explanation layer, or the Slack alert — for the
payment-orchestration monitoring project ("The Control Tower").

**You do not need to read any pipeline source code to use this system.**
Everything you need to query is described below: the two database
structures, the two functions that read them, the fixed vocabulary of
values you'll see in every row, and the reasoning behind the shape of it,
so you don't have to guess whether a quirk is a bug or a deliberate
tradeoff.

**What this pipeline already does, so you don't have to:** ingest raw
vendor webhooks, deduplicate them, translate 4 different decline-code
vocabularies into one shared taxonomy, and expose the resulting
attempt/approved/declined/error counts plus raw sample rows as two plain
Python functions. **What it deliberately does not do** — and what this
handoff exists to hand off — is decide what "normal" looks like for a
cell, measure deviation from it, decide *which* combination of dimensions
is the actual root cause of a drop, explain that in plain English, or
notify anyone. That's your job: this pipeline gives you clean counts to
compute all of that from, not pre-computed answers.

---

## 1. The shape of the system (one paragraph)

Four simulated payment providers (Stripe, Adyen, MercadoPago, dLocal) feed
two generators. **Generator A** runs once, before any of this matters to
you, and pre-aggregates 14 days of history directly into the two tables in
section 2.1 — it does not produce individual events. **Generator B** runs
live during a demo and produces real individual payment-attempt events,
which flow through a cleaning pipeline (parse → dedupe → translate decline
codes → validate) into the single table in section 2.2. Both sides land in
`data/gold/`, which is the only part of this system you need to touch.

```
Generator A (once, precomputed)  ──────────────────────────┐
                                                             ▼
Generator B (live) → Bronze → Silver (cleaning) ──────►  data/gold/  ──► get_counts() / get_samples()  ──► YOU
```

---

## 2. The database structures

Two engines, three tables, all under `data/gold/`. Open them read-only —
nothing in this system expects you to write to them.

### 2.1 Historical (Parquet + DuckDB) — 14 days, precomputed, read-only forever

File: `data/gold/historical.duckdb` (a DuckDB database holding VIEWs over
`rate_cells_minutely.parquet` and `decline_cells_hourly.parquet`, plus a
`meta` table). Open with `duckdb.connect(path, read_only=True)`.

```sql
-- rate_cells_minutely: grain = 1 minute × (merchant, provider, country, method, bank)
-- NO decline_code dimension here (kept out specifically to avoid row-count blowup)
time_bucket        TEXT     -- 'YYYY-MM-DDTHH:MM:SSZ', floored to the minute
minute_of_day      INTEGER  -- 0-1439
weekday            INTEGER  -- 0=Monday .. 6=Sunday
merchant_id        TEXT
provider_id        TEXT
country            TEXT
method             TEXT
issuing_bank       TEXT
cell_id            TEXT     -- "provider|country|method|bank"
attempts           INTEGER
approved           INTEGER
declined           INTEGER
error              INTEGER
amount_usd_total   DOUBLE

-- decline_cells_hourly: grain = 1 hour × (merchant, provider, country, method, bank, decline_code)
-- coarser in time, finer in decline reason -- the tradeoff that keeps row count sane
hour_bucket        TEXT     -- 'YYYY-MM-DDTHH:00:00Z'
merchant_id        TEXT
provider_id        TEXT
country            TEXT
method             TEXT
issuing_bank       TEXT
decline_code       TEXT     -- see taxonomy in section 3
cell_id            TEXT     -- "provider|country|method|decline_code"
declines           INTEGER
recovered          INTEGER  -- how many of those declines succeeded on a retry
amount_usd_total   DOUBLE

-- meta: key/value, one row per key
-- keys: history_start, history_end, base_txns_per_minute, seed, generated_at, generation_seconds
```

**Why aggregated instead of individual rows:** 14 days at individual-attempt
grain is ~32 million rows. Nobody diagnosing a live incident needs
transaction #4,281,003 from 9 days ago — they need "what does normal look
like for this cell at this minute." Counts are sufficient for that and cost
~90x less to store (11MB vs. ~1GB as a naive row-store). The
minute-vs-hour split between the two tables exists because adding
`decline_code` as a dimension at minute resolution would multiply the row
count past the point of being worth it — hourly is coarse enough to stay
small, fine enough that root-cause-by-reason is still answerable.

**Why read-only, and why this matters to you:** this file is written once
and never again. Open as many concurrent read-only connections as you want
— DuckDB only forbids concurrent access when a **write** connection is
also open, and nothing here ever writes to it again after generation.

### 2.2 Live (SQLite) — the demo stream, individual rows, being written right now

File: `data/gold/live.sqlite`. Open with the stdlib `sqlite3` module.

```sql
-- live_attempts: grain = 1 row per individual payment attempt. No aggregation.
attempt_id       TEXT PRIMARY KEY  -- "{provider}:{vendor_native_id}"
payment_id       TEXT              -- groups retries of the same payment together
attempt_number   INTEGER           -- 1, 2, or 3 (never higher)
event_ts         TEXT              -- 'YYYY-MM-DDTHH:MM:SSZ', real event time, NOT floored
merchant_id      TEXT
provider_id      TEXT
method           TEXT
country          TEXT
issuing_bank     TEXT
status           TEXT              -- 'approved' | 'declined' | 'error'
decline_code     TEXT              -- null when status='approved'
amount_minor     INTEGER           -- cents-equivalent (amount * 100, rounded)
currency         TEXT              -- 'MXN' | 'BRL' | 'COP'
amount_usd       REAL
```

**Why individual rows instead of aggregated like history:** two jobs an
aggregate can't do. (1) **Evidence** — an alert needs to cite a specific
failed payment, not just "38 declines happened." (2) **Retry tracking** —
knowing whether *this specific* declined payment got retried and succeeded
needs `payment_id`/`attempt_number` on a real row; a count has no concept
of "this one payment, tried three times."

**Why SQLite instead of DuckDB, and why this matters to you:** this file
is being written to, continuously, by a live process, at the same time you
might be reading it. That's the one pattern DuckDB genuinely cannot do —
tested directly: a DuckDB file with an open write connection rejected
**60/60** concurrent read attempts from a separate process. SQLite handles
exactly this pattern (one writer, concurrent readers) natively — tested at
**40/40** successful concurrent reads against an active writer. If you
write your own direct SQL against this file (rather than going through
`get_samples()`), open it in a way that doesn't try to acquire a write
lock, and don't hold a connection open indefinitely.

---

## 3. Fixed vocabulary — match these exactly in filters/queries

| dimension | values |
|---|---|
| `provider_id` | `stripe`, `adyen`, `mercadopago`, `dlocal` |
| `country` | `MX`, `BR`, `CO` |
| `method` | MX: `card`, `oxxo`, `wallet` · BR: `card`, `pix`, `boleto` · CO: `card`, `pse`, `wallet` |
| `issuing_bank` | Only resolves to a real bank for `mercadopago` (MX: `bbva`,`santander`,`banorte` · BR: `itau`,`bradesco`,`nubank` · CO: `bancolombia`,`davivienda`,`bbva`). **Every other provider is always `unknown_bank`** — none of the other 3 vendors' real webhooks expose bank info, so don't treat a `mercadopago`-only signal as "no bank data available," it's structural. |
| `status` | `approved`, `declined`, `error` (only `91_96_network_timeout` maps to `error` — every other decline is a business decline, not an infra failure) |
| `decline_code` | `51_insufficient_funds`, `05_do_not_honor`, `capture_error`, `54_expired_card`, `41_43_lost_stolen`, `57_not_permitted`, `59_suspected_fraud`, `61_exceeds_limit`, `91_96_network_timeout`, or `unknown` (an unrecognized code — treat as a real signal, not an error) |
| `merchant_id` | `merch_globex`, `merch_acme`, `merch_umbrella`, `merch_initech`, `merch_stark`, `merch_wayne` |

**Where these numbers came from, so you know how much to trust them:**
the approval rates, distribution weights, and this decline taxonomy came
from the hackathon brief, which asserted they were already researched —
not independently verified during this build. The specific bank names,
exact decline-code strings beyond one verified example per vendor, and FX
rates were filled in as reasonable assumptions where the brief was silent
(all logged in `decision_log.md`). If your root-cause math is sensitive to
the exact approval-rate numbers being accurate, that's worth someone
checking against real provider docs before relying on it for anything
beyond this demo.

---

## 4. How to read it — the only two functions you need

```python
from pipeline.gold.access import get_counts, get_samples

get_counts(
    start_ts: str,              # 'YYYY-MM-DDTHH:MM:SSZ'
    end_ts: str,
    bucket: str = "minute",     # "minute" | "hour" | "day"
    group_by: list = (),        # subset of: merchant_id, provider_id, country,
                                 #   method, issuing_bank, decline_code
    filters: dict = None,       # {dimension: value}, equality only, same keys as group_by
    gold_dir = ...,             # defaults to data/gold/
) -> list[dict]
# Each row: {bucket_ts, bucket_granularity, ...your group_by keys, attempts,
#            approved, declined, error, amount_usd_total}
# bucket_granularity is the resolution actually used for that row -- it can
# differ from the `bucket` you asked for: decline_code is only stored at
# hourly resolution pre-history_end, so a bucket="minute" request with
# decline_code in group_by/filters silently serves hour-bucketed rows for
# that portion of the range, and this field tells you when that happened.

get_samples(
    start_ts: str,
    end_ts: str,
    filters: dict = None,       # group_by dims + status / payment_id / attempt_number
    limit: int = 50,
    gold_dir = ...,
) -> list[dict]
# Raw live_attempts rows -- evidence for an alert. Empty list if the range
# is entirely inside history (no individual rows exist there by design).
```

**Routing you don't have to think about:** both functions automatically
split a requested time range at `history_end` (from the `meta` table) and
query the right store — `rate_cells_minutely`/`decline_cells_hourly` for
anything before it, `live_attempts` for anything after. If your range
spans both, `get_counts()` merges the two sources for you — a bucket
sitting exactly on the boundary comes back as one summed row, not two.

**Example — did mercadopago's approval rate in Brazil actually drop, or is
this normal variance:**
```python
rows = get_counts(
    "2026-08-29T20:00:00Z", "2026-08-29T21:00:00Z",
    bucket="minute",
    filters={"provider_id": "mercadopago", "country": "BR"},
)
# each row: approved/attempts gives you the minute-by-minute rate to compare
```

**Example — what's actually failing for a suspect cell, with evidence:**
```python
breakdown = get_counts(start, end, bucket="hour", group_by=["decline_code"],
                        filters={"provider_id": "adyen", "country": "MX", "method": "card"})
evidence = get_samples(start, end, filters={"provider_id": "adyen", "status": "declined"}, limit=10)
```

**Deciding what "normal" looks like is your job, on purpose.** This
pipeline deliberately does not compute an expected rate, a deviation
score, or a confidence label anywhere a consumer can reach — `get_counts()`
gives you raw counts only. (`pipeline/silver/baseline.py` and
`pipeline/silver/recovery.py` contain z-score/deviation-scoring code, but
it exists purely as internal validation tooling for this pipeline's own
test suite — e.g. confirming a clean historical week doesn't produce false
alarms, or that an injected incident is actually detectable — not as
something wired up for you to call. Root-cause analysis, including
deciding what counts as a deviation, belongs entirely to your module.)

---

## 5. Running it yourself

```bash
# one-time setup
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt

# regenerate the 14-day history (~29s, wipes data/gold/ and rebuilds it)
.venv/bin/python3 -m pipeline.generator.generate_historical_aggregates --seed 42

# run the live demo stream (populates live.sqlite in real time)
.venv/bin/python3 -m pipeline.generator.generate_live_stream --duration 90

# ad-hoc SQL from the terminal against both stores at once
.venv/bin/python3 scripts/query.py "SELECT * FROM hist.rate_cells_minutely LIMIT 5"
.venv/bin/python3 scripts/query.py "SELECT * FROM live.live_attempts ORDER BY event_ts DESC LIMIT 5"
```

Use `.venv/bin/python3`, not the system `python3` — `duckdb` only lives in
the virtualenv (the system Python is externally managed and refuses a
plain `pip install`).

---

## 6. Explicitly out of scope (this is what you're building)

Nothing past `get_counts()`/`get_samples()` exists yet:

- **Baseline / expected-rate calculation** — deciding what "normal" looks
  like for a cell (expected approval rate, expected recovery rate, how
  much variance is normal) so you can measure deviation from it.
  `get_counts()` gives you raw counts to compute this yourself.
- **Root-cause ranking** — deciding *which* combination of dimensions
  (provider × country × bank × decline_code, etc.) actually explains a
  drop, out of everything that's technically correlated.
- **The plain-English explanation** — turning a ranked cause into "here's
  what broke and since when."
- **Notification** — the Slack alert that reaches an actual human.

## 7. Where to go deeper

- `docs/decision_log.md` — every assumption made and why, including the
  full DuckDB-concurrency story with the actual test output.
- `docs/architecture_diagram.md` — the same architecture as Mermaid +
  ASCII diagrams.
- `DATA-CONTRACT.md` (repo root, if present) — the original interface
  spec from the detection team this was built to satisfy.
