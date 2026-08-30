# HANDOFF — PagoTotal conversion monitoring & diagnosis

**To:** the session/person implementing this.
**From:** a design session that closed ~68 decisions. The design is settled. This document
tells you exactly what to build. Do not re-open settled decisions; if something here seems
wrong, check `docs/DECISIONS.md` first — the reasoning and the rejected alternative are
usually recorded there.

**Time budget:** ~18 hours to demo (as of 2026-08-29).
**Stack:** Python, SQLite, OpenAI API, Slack incoming webhook. Local only.

---

## 0. Read in this order

1. `CONTEXT.md` — the glossary. **Read it first.** Terms like *Cell*, *Signal*, *Incident*,
   *Anchor cell*, *Lost approvals*, *Blast radius*, *Burn rate* are used precisely
   throughout this document and in the code. Use these exact names as identifiers.
2. This file (`docs/HANDOFF.md`) — what to build.
3. `docs/DECISIONS.md` — D1–D68, the *why* behind every choice, including reversals.
4. `docs/DATA-CONTRACT.md` — contract 1, **inbound** (pipeline → us). Maca and Malu have it.
5. `docs/DASHBOARD-CONTRACT.md` — contract 2, **outbound** (us → dashboard). That team has it.
6. `docs/TESTING.md` — T0–T10. T0 and T1 are not optional; they produce the threshold values.
7. `docs/TO-STUDY.md` — accepted-but-not-yet-understood items, plus the production
   architecture write-up for defending the MVP shape to judges.

---

## 1. What we own, and what we do not

| Owner | Responsibility |
|---|---|
| **Maca, Malu** | Mock transaction generator, medallion/gold pipeline, injection UI. They have `docs/DATA-CONTRACT.md`. |
| **Dashboard team** | The dashboard, reading `reports.db`. They have `docs/DASHBOARD-CONTRACT.md`. |
| **Third teammate** | Deployment/infra. Out of scope. Everything runs locally. |
| **US (this handoff)** | Baselines, detector, clusterer, incident lifecycle, economics, Slack, the diagnosis agent, memory, and the reporting sink that writes `reports.db`. |

Hard product constraint: **we detect, diagnose and recommend. We never remediate.** (D11)

---

## 2. The system in one page

```
  gold transactions (Maca/Malu)
            │  poll every ~10s, windowed aggregates
            ▼
  ┌──────────────────┐   baselines_v{n}.parquet   ┌────────────────────┐
  │  DETECTOR        │◄───────────────────────────│  BASELINE BUILDER  │
  │  scan 1-D + 2-D  │      (immutable, swapped   │  offline, from     │
  │  3 gates         │       atomically)          │  backfill          │
  └────────┬─────────┘                            └────────────────────┘
           │ Signals (never shown to a human)
           ▼
  ┌──────────────────┐
  │  CLUSTERER       │  greedy anchor + lattice containment
  └────────┬─────────┘
           │ Clusters
           ▼
  ┌──────────────────┐        ┌──────────────┐
  │  INCIDENT        │───────►│ incidents.db │  (state, gitignored)
  │  REGISTRY        │        └──────────────┘
  │  debounce/open/  │
  │  resolve         │
  └────────┬─────────┘
           │ on open
           ├──────────────► SLACK ROOT MESSAGE   ← deterministic, no LLM, immediate
           │                                       (this is the perceived latency)
           ▼
     queue.Queue
           ▼
  ┌──────────────────┐  tools: get_counts, get_samples, search_memory, get_baseline
  │  AGENT (worker   │  budget: 120s / 15 calls
  │  thread)         │  reads: playbook.md + context catalogue
  └────────┬─────────┘
           ├──────────────► SLACK THREAD REPLY   ← diagnosis, evidence, action
           │
           └──────────────► REPORTING STEP ──► reports.db ──► dashboard
                            (async, non-critical, may retry)
```

**The load-bearing property:** everything above the agent is deterministic. After build
step 3 you can demo scenarios 1, 2 and 3 **with no LLM at all**. The agent is additive,
not load-bearing. Protect this property — it is your insurance if the OpenAI API is slow
or down during the pitch.

---

## 3. Repo layout to create

```
pagototal/
├── config.py                 # every tunable number, one place
├── store/
│   ├── interface.py          # get_counts / get_samples — the ONLY seam to the pipeline
│   ├── mock.py               # our own fake store; build this FIRST so we are never blocked
│   └── duckdb_store.py       # real impl once Maca/Malu deliver
├── baselines/
│   ├── build.py              # offline: backfill -> baselines_v{n}.parquet
│   └── load.py               # atomic pointer read; 3-tier fallback lookup
├── detect/
│   ├── scan.py               # cell enumeration + the 3 gates -> Signals
│   ├── cluster.py            # Signals -> Clusters (greedy anchor + lattice containment)
│   └── registry.py           # Clusters -> Incidents; debounce, open, resolve, onset backdating
├── economics.py              # ALL money math. One file. Constants at the top.
├── agent/
│   ├── run.py                # OpenAI tool-calling loop + budget enforcement
│   ├── tools.py              # 4 tool functions + JSON schemas
│   ├── playbook.md           # the investigative guide (config, versioned in repo)
│   └── schema.py             # the structured output contract
├── memory/
│   └── incidents_db.py       # incidents.db read/write + lattice-related search
├── reporting/
│   └── publish.py            # writes reports.db per DASHBOARD-CONTRACT.md
├── slack/
│   ├── post.py               # webhook client, root + thread
│   └── templates.py          # ALL formatting. The LLM never formats Slack.
├── catalogue/
│   ├── providers.csv         # provider -> countries, methods, gateway|acquirer
│   ├── methods.csv           # method -> card_based? (issuing_bank meaningful?)
│   ├── actions.csv           # the action library + preconditions
│   └── ownership.csv         # provider/merchant/bank -> named contact
└── main.py                   # one process: detector thread + agent worker thread + queue

data/                         # gitignored
├── incidents.db
├── reports.db
└── baselines_v*.parquet
```

**Config vs state (D38):** everything in `catalogue/`, `playbook.md` and `config.py` is
**config** — it lives in the repo and is versioned. Everything in `data/` is **state** —
gitignored. This is the resolution of the original "will memory diverge from the repo?"
worry: only state can diverge, and state is not in the repo.

---

## 4. Build the components

### 4.1 `store/interface.py` — the seam (build first, ~1h)

Two functions. Nothing else crosses this boundary.

```python
def get_counts(start_ts, end_ts, bucket, group_by: list[str], filters: dict) -> list[Row]:
    """Row = (bucket_ts, *group_by values, attempts, approved, declined, error,
              amount_usd_total)"""

def get_samples(start_ts, end_ts, filters: dict, limit: int) -> list[Attempt]:
    """Raw attempt rows, for quoting evidence."""
```

`group_by` may be any subset of the **five scan dimensions**. `filters` is equality-only.

Build `store/mock.py` immediately: load a CSV/parquet backfill into an in-memory DataFrame
or DuckDB and implement both functions over it. **You must not wait for Maca and Malu to
start.** When their store lands, write `duckdb_store.py` behind the same interface.

### 4.2 The five scan dimensions — and the trap

Scan dimensions: `merchant`, `provider`, `method`, `country`, `issuing_bank`.

**`decline_code` is NOT a scan dimension (D34).** Approved attempts have a null decline
code, so a conversion rate grouped by decline code is 0% by construction and meaningless.
Decline code is a **characterization attribute**: given a Cell that is already firing, you
ask *how its decline mix shifted*. This is what distinguishes a provider story from an
issuer story. If you find yourself writing `group_by=['decline_code']` to compute a
conversion rate, stop — that is the bug this paragraph exists to prevent.

Same for `status`: `declined` and `error` both count as failures in the conversion rate
(one detector, D56), but `error_share` is carried alongside as characterization.

### 4.3 `baselines/` — level and dispersion (~1.5h, after T0)

A baseline has two parts:

- **level**: pooled `approved/attempts` for `(cell, hour_of_week)` over the backfill
- **dispersion**: robust scale of the residuals for that cell, **pooled across all windows**
  after removing the hour-of-week level

Dispersion must be pooled because 2–4 weeks gives only 2–4 observations per hour-of-week
slot — nowhere near enough to estimate spread per slot.

**Do not choose the dispersion estimator before running T0** (D68). Std, MAD, empirical
quantile and beta-binomial are all candidates; the EDA decides. What is settled is the
*principle*: every cell is compared to **its own** historical behaviour, never to a fixed
coin-flip assumption. (Why: real approval rates are overdispersed relative to binomial —
customer mix, BIN mix, amounts — so a naive `binomtest` on a high-volume cell over-fires
and the percentage-point gate silently ends up doing all the work.)

**Exclusion:** baseline computation must exclude windows belonging to known Incidents
(read them from `incidents.db`), or an incident trains the system to accept its own
degraded rate. Ship this as an explicit `WHERE ts NOT IN (incident windows)` clause even
if the MVP never re-runs the builder — you will be asked about it.

**Three-tier lookup (D53), cold start:**

1. `(cell, hour_of_week)` if it has ≥K historical observations
2. else `(cell, all_time)`
3. else the **parent cell's** baseline — `{provider:P3, country:CO}` falls back to `{provider:P3}`
4. else mark the cell `no_baseline` and route it to the **insufficient-evidence** path.
   Never silently drop it.

The tier used is attached to every Signal and surfaced as evidence and as
`baseline_source` in `reports.db`. "Baseline inherited from parent" displayed in the UI is
the visible form of the honesty that earns bonus points.

**Artifact handling (D40):** baselines are an immutable versioned file. Recompute writes
`baselines_v{n+1}.parquet`, then atomically swaps a pointer. Keep `v{n}` for rollback.
**Zero downtime** — a read-only artifact swap never needs any. Nightly over a trailing
4-week window in production; for the MVP compute once at startup and skip the scheduler.

### 4.4 `detect/scan.py` — Signals (~1.5h)

Every ~10s:

1. Ask the store for the last 5 closed 1-minute buckets, grouped by each scan grouping.
2. **Never enumerate the cube** — `GROUP BY` returns only non-empty cells (D36).
3. For each cell, sum the 5 buckets into one `(attempts, approved)` pair (the sliding window).
4. Apply **three gates**, all of which must pass:
   - **statistical** — the cell is anomalously low versus its own baseline (estimator per T0)
   - **materiality** — absolute drop ≥ `MIN_ABS_DROP_PP`
   - **volume** — `attempts ≥ MIN_ATTEMPTS` in the window
5. The test is **one-sided** (D57): only drops fire. Improvement is never an incident.
6. Emit a `Signal` carrying: cell, observed rate, baseline level, baseline source,
   `lost_approvals = attempts × (baseline_rate − observed_rate)`, `error_share`,
   decline-code mix, attempts, amount total.

**Scan set (D35):** a **core** set always scanned, plus opportunistic 1-D/2-D cells as
volume and compute allow.

Core (from payment-analytics practice — Yuno ranks issuer as the highest-priority lens,
then BIN, market, method, decline reason, and names *issuer × method* and *market × method*
explicitly):

```
issuing_bank
provider
provider × country
issuing_bank × country
issuing_bank × method
method × country
merchant × provider
```

**Why this scales** (D36, and see `docs/TO-STUDY.md §1`): the number of cells you actually
*test* is bounded by `attempts_in_window / MIN_ATTEMPTS` — linear in traffic, independent
of how many banks or merchants exist. Adding 1000 banks with no traffic adds 1000 empty
cells, and empty cells are never enumerated and never tested. Hand-picking the core is a
*business prior for priority*, not a scaling mechanism.

**The materiality gate is the one that actually saves you.** On a high-volume cell a 0.4pp
drop is wildly statistically significant and operationally meaningless. Pure statistical
significance is precisely how classic alerting ends up "firing on everything and getting
ignored." Say this in the pitch.

### 4.5 `detect/cluster.py` — Clusters (~45min)

Greedy anchor + lattice containment (D25):

```
signals sorted by lost_approvals desc
while signals remain:
    anchor = signals.pop(0)
    cluster = [anchor] + [s for s in signals if lattice_related(s.cell, anchor.cell)]
    remove cluster members from signals
    emit Cluster(anchor=anchor, members=cluster)
```

`lattice_related(a, b)` = one filter dict is a subset or superset of the other.
`{provider:P2}` and `{provider:P2, country:BR}` are related. `{bank:B7, merchant:M1}` is
not related to either.

Two disjoint clusters = two Incidents, **separated with zero LLM involvement**. This is
what makes the scored "two simultaneous incidents" scenario reliable in front of judges
instead of hoping a model gets it right. Ranking metric is `lost_approvals`, which is also
what Yuno recommends ranking by.

### 4.6 `detect/registry.py` — Incident lifecycle (~1.5h)

```
registry: dict[signature, Incident]
pending:  dict[signature, int]

every tick:
    clusters = cluster(signals)
    for c in clusters:
        match = open incident whose identity_cell is lattice_related to c.anchor.cell
        if match:
            match.miss_streak = 0
            match.current_anchor = c.anchor          # anchor may drift
            if material_change(match): enqueue re-run
        else:
            pending[sig(c.anchor.cell)] += 1
            if pending[...] == 2:                    # debounce
                open incident, backdate onset, post Slack root, enqueue agent
    for inc in open incidents not matched this tick:
        inc.miss_streak += 1
        if inc.miss_streak >= K: resolve, post to thread
```

Three subtleties, each of which will bite you if ignored:

- **Identity is the FIRST anchor cell and never changes** (D26). The anchor *drifts* —
  early on `{provider:P2}` may out-rank `{provider:P2, country:BR}`, and later the reverse.
  Track `current_anchor` separately. Without this, one incident splits into two Slack root
  messages mid-demo.
- **Onset is backdated** (D52). On open, walk the anchor cell's per-bucket rates *backwards*
  from now until you hit the first bucket that is not below baseline; the bucket after that
  is `onset_ts`. Report onset and detection time separately — *"started 14:03, detected
  14:06."* Showing the gap is stronger than hiding it: detection latency is the number the
  challenge is asking you to minimise, and pretending it is zero looks like you never
  measured it.
- **Resolution lags by the window length** — after an incident truly ends, the sliding
  window still contains bad buckets for 5 minutes. Do not fight it; just keep `K` small.

**Material change** (D66), which re-runs the agent and posts to the thread:
burn rate **doubles** versus the value at last diagnosis, **OR** the anchor changes lattice
branch (not mere drift within one) — **AND** ≥5 minutes of sim-time since the last agent
run. **Hard cap of 3 agent runs per incident.** Without the cooldown and cap, a slowly
worsening incident re-runs every tick and buries its own root message.

**Persistence (D39):** in-memory dict, write-through to `incidents.db` on open and on
close. A process restart loses in-flight incidents; accepted for MVP.

### 4.7 `economics.py` — the money (~30min)

One file. Constants at the top. Every money number in the system comes from here, so it
can be changed in one place when the demo numbers look wrong.

```
burn_rate_usd_hour = lost_approvals_per_hour × avg_amount_usd(cell) × (1 − retry_recovery_rate)
```

A **burn rate**, not a total (D33) — the challenge's own framing is "money lost by the
minute," and a rate does not require the incident to be over. `retry_recovery_rate` comes
from the backfill (what fraction of declined payments eventually succeed on retry). If
retries are not in the generated data in time, **set it to 0 and label the figure `gross`**
via `cost_basis`. Never emit an unlabelled number.

Detection is attempt-level; money is payment-level (D17). A retry that eventually succeeded
cost latency, not revenue.

### 4.8 `slack/` — two-phase alert (~1h)

**Phase 1, immediate, no LLM (D29).** The instant an incident opens, post the root message
from deterministic facts only: anchor cell, onset + detection time, baseline vs observed
rate, drop in pp, blast radius, burn rate, and the **exec one-liner in bold at the top**.

Three reasons this ordering matters: perceived latency becomes detection latency rather
than detection + diagnosis; the alert stays useful if the LLM fails or times out
(trial-by-fire insurance); and judges get two visible beats.

**Phase 2, thread reply.** When the agent finishes, post its ops detail, evidence list,
recommended action and confidence **into the thread**, never the channel.

**One channel** (D45). Exec reads the channel, ops opens the thread — that delivers the
two-audience bonus point structurally, with nothing extra built.

**All formatting lives in `templates.py` and is done by our code. The LLM never formats
Slack** (D31) — that is what stops a hallucinated number reaching the channel unlabelled.

**Storm cap (D65).** If more than 3 incidents are open simultaneously, post the top 3 by
burn rate individually and collapse the rest into one *"N further incidents open"* summary
with a table. **Only the top 3 get an agent run.** Per-incident dedup does not protect you
against many simultaneous incidents — that is the "fires on everything" failure arriving
through the door you did not guard, and it also caps latency and API spend if a judge
injects something very broad.

### 4.9 `agent/` — the diagnosis (~4h)

**Plain OpenAI function calling. No MCP** (D6, reversed). MCP would mean standing up a
server and a transport to let our own process call our own functions.

**Four tools** (D31): `get_counts`, `get_samples`, `search_memory`, `get_baseline`.

**Budget: 120s or 15 tool calls**, whichever comes first, then forced to answer with what
it has (D42 — *marked assumed, not settled*; test T5 exists to calibrate it. If runs finish
in 6 calls, lower it; if correct diagnoses are being truncated, raise it).

**`playbook.md` is a guide, not an algorithm.** This is deliberate and it is the one place
the LLM genuinely beats fixed code. It must contain at least:

- Start from the anchor cell.
- Confirm the drop is *absent* in sibling cells (P2 in MX and CO, other providers in BR).
- Check whether the decline-code mix shifted, and check `error_share`:
  **elevated `error_share` ⇒ provider/infrastructure story; normal `error_share` with a
  shifted decline mix ⇒ issuer/risk story** (D56).
- **If the anchor cell's loss is not fully explained within the anchor, split it by each
  remaining dimension and look for a child cell carrying most of the loss.** This is the
  drill-down that goes *deeper than the detector*, and it is your insurance against a
  trial-by-fire incident that only exists at 3-D where every 2-D projection is diluted
  below the materiality gate (D15/T8).
- Check memory for a lattice-related past incident.
- Consult the context catalogue before asserting anything about a provider/method/country
  combination.
- **If you cannot isolate to a specific cell, say so and list what you would need.**

**Structured JSON output** (`agent/schema.py`): `root_cause`, `confidence`, `evidence[]`,
`alternatives_ruled_out[]`, `recommended_action`, `ops_explanation`, `exec_one_liner`.

**Confidence is a three-level enum** — `high` / `medium` / `insufficient_evidence` (D63).
Never a fabricated percentage: an LLM-produced "87% confident" has no referent and dies to
the question "87% of what?"

Internal `low_confidence_reason`: `no_baseline`, `below_volume_gate` (both deterministic,
demoable on command), `competing_explanations` (≥2 cells each explaining a large share,
neither dominant), `budget_exhausted`.

**`budget_exhausted` is internal telemetry and must NEVER be surfaced to the user** (D64).
When the agent hits its cap without isolating, the output reports an *inconclusive
investigation*: what was checked, what was ruled out, what remains open for deeper
research. It never blames its own budget. This reads as rigour rather than failure, and it
is what the operator actually needs.

### 4.10 `catalogue/` — what the agent knows that the data does not (~45min)

Small CSVs, in the repo, read by the agent (D54, D60).

- `providers.csv` — provider → countries served, methods served, gateway vs acquirer
- `methods.csv` — method → is it card-based (so `issuing_bank` is meaningful) or a
  push/APM like Pix or SPEI (where "issuing bank down" means something different)
- Purpose: stop the agent producing domain nonsense like *"issuing bank B7 is declining Pix."*
  Ten rows that prevent the most embarrassing possible failure on stage.
- `actions.csv` — the **action library** (D58). ~6 entries, each with a **precondition**:
  reroute traffic for cell X to provider Y; disable method M in country C; enable retries
  with a different provider for decline class D; raise with the provider's support;
  throttle and monitor; no action — monitor only.
  The agent **selects and parameterizes** an entry, never invents one, and may return
  `no_safe_action` with reasoning. Because each entry states its precondition, **our code
  can reject a recommendation that contradicts the catalogue** (e.g. rerouting to a
  provider that does not serve that country) before it reaches Slack.
- `ownership.csv` — provider/merchant/bank → named operator contact (D59, D60). Contacts
  are fictional, consistent with the fictional PagoTotal scenario.

**The recommendation carries an owner and an addressable next step**, e.g. *"email Juan
Tellez (ops, Mercado Pago) with: <drafted message>"*. We deliberately stop at recommending
(D11), but we emit something *shaped* to be executed — a downstream actuator could send it.
This is the strongest product line in the pitch: it gestures at the real system without
violating the challenge's "diagnose, don't remediate" constraint.

### 4.11 `memory/incidents_db.py` (~1h — first thing to cut)

SQLite, gitignored, seeded with 2–3 fabricated past incidents so memory has something to
find during the demo. One row per resolved incident: `identity_cell` (JSON), `opened_at`,
`resolved_at`, `peak_lost_per_hour`, `dominant_decline_code`, `diagnosis_summary`,
`recommended_action`.

**Retrieval is deterministic SQL, not semantic** (D44): `search_memory(cell)` returns past
incidents whose `identity_cell` is lattice-related to the query cell, ranked by recency.
The LLM decides whether it is *really* the same story; SQL decides what it is allowed to
see. Semantic search over a handful of rows is theatre and invites false "this happened
Tuesday" claims.

### 4.12 `reporting/publish.py`

Implements `docs/DASHBOARD-CONTRACT.md`. Runs **after** the Slack thread reply, off the
critical path, idempotent per `(incident_id, revision)`. Revisions: 1 on first diagnosis,
increment on material-change re-run and on resolution.

**THE ONE RULE:** the reporting step **copies** every deterministic figure from the main
path (`onset_ts`, `burn_rate_usd_hour`, `blast_radius`, `affected_entities`, rates,
counts) and **never recomputes them**. It may only rewrite prose. If a secondary agent
re-derives the money or the onset, the dashboard and Slack will disagree in front of
judges. Whether this step is a secondary agent or plain code is **open** (D50) and blocks
nobody.

### 4.13 `main.py` — process topology (D67)

**One Python process, threads.** A detector loop thread, an agent worker thread (so a 120s
LLM call never stalls detection), and a `queue.Queue` between them. No broker, no
docker-compose. The generator is a separate process owned by Maca and Malu.

That queue is the entire "async architecture" and it is honest to describe it that way.
`docs/TO-STUDY.md §2` has the production shape (Kafka/Flink or ClickHouse rollups, sharded
stateless detector, Postgres registry with a leader-elected writer, durable queue with
dead-letter) for when a judge asks "how would you productionise this?" The design already
has the seams in the right places — store behind an interface, baselines as immutable
versioned artifacts, reporting as a separate non-blocking sink — so productionising is
substitution behind existing seams, not a rewrite.

---

## 5. Config — every tunable number, and what settles it

Put all of these in `config.py`. **The values below are placeholders, not answers.**

| Name | Placeholder | Settled by |
|---|---|---|
| `BUCKET_SECONDS` | 60 | — |
| `WINDOW_BUCKETS` | 5 | longer = more power but slower and more onset dilution |
| `EVAL_INTERVAL_SECONDS` | 10 | — |
| `SIM_SPEED` | 20× | demo pacing (D20) |
| `MIN_ATTEMPTS` | 50 | **T1 calibration** |
| `MIN_ABS_DROP_PP` | 5 | **T1 calibration** |
| `ANOMALY_THRESHOLD` | tbd | **T0 (estimator) then T1 (value)** |
| `DEBOUNCE_TICKS` | 2 | — |
| `RESOLVE_MISS_TICKS` (K) | 3 | keep small; window drain already adds lag |
| `AGENT_BUDGET_SECONDS` | 120 | **T5** — marked assumed (D42) |
| `AGENT_MAX_TOOL_CALLS` | 15 | **T5** |
| `MAX_CONCURRENT_ALERTS` | 3 | D65 |
| `MAX_AGENT_RUNS_PER_INCIDENT` | 3 | D66 |
| `AGENT_RERUN_COOLDOWN_MIN` | 5 | D66 |
| `RETRY_RECOVERY_RATE` | 0 until measured | backfill; label `cost_basis` accordingly |

Anyone who tells you 5pp is "correct" is bluffing. These are **outputs of a calibration
run, not opinions** (D24).

---

## 6. Build order and the cut line (D62)

| # | Step | Est. |
|---|---|---|
| 1 | `store/interface.py` + `store/mock.py` + backfill loader | 1h |
| 2 | **T0 EDA**, then baselines + detector + **T1 calibration** | 3h |
| 3 | Clusterer + incident lifecycle + Slack root message | 3h |
| 4 | Agent + playbook + tools + catalogue | 4h |
| 5 | Memory + T7 | 1.5h |
| 6 | Trial-by-fire rehearsal (T8) | 2h |
| — | Integration slack | 3.5h |

**After step 3 you are fully demoable with no LLM** — scenarios 1, 2 and 3 all pass
deterministically. Reach that point before touching the OpenAI API.

**Cut line if behind at hour 12:** drop **memory (4.11) and the resolution message**.
**Never drop the two-incident separation or the trial-by-fire rehearsal** — both are
explicitly scored.

**Rehearse the trial by fire with someone else choosing the injection**, or you have not
rehearsed it.

---

## 7. Testing

`docs/TESTING.md` has T0–T10 in priority order. Two are gating:

- **T0** — descriptive EDA on the backfill. Blocks the estimator choice and T1. Measures
  the overdispersion factor (observed variance ÷ `p(1−p)/n`), residual distribution after
  removing the hour-of-week level, autocorrelation between consecutive windows, and **how
  many cells clear the volume gate at each candidate `MIN_ATTEMPTS`** — run that last one
  early, it tells you whether the core scan set is even populated at 5-minute windows.
- **T1** — noise calibration. Replay the full clean backfill with no injection.
  **Pass = zero incidents opened.** This produces the threshold values *and* the strongest
  sentence in the pitch: *"we replayed two weeks of normal traffic through the detector; it
  fired zero alerts."*

T4 (two-incident separation), T6 (insufficient evidence), T7 (repeat recognition) and T8
(3-D trial-by-fire rehearsal) each map to a scored or bonus demo item. Test them on
purpose; do not hope for them.

---

## 8. Demo runbook (D61) — four beats, ~2 minutes

| Beat | ~Time | What judges see |
|---|---|---|
| 1 | 20s | Stream running at 20×, Slack channel **silent**, terminal showing "N cells evaluated, 0 signals" — the not-firing-on-noise claim made *visible* |
| 2 | 30s | Injection fires → root Slack message: cell, onset, burn rate, bold exec one-liner |
| 3 | 40s | Thread fills with diagnosis + evidence + recommended action; simultaneously the second incident opens as its own root message, ranked below by burn rate |
| 4 | 30s | Hand the injector to a judge |

**Keep a static screenshot of a successful run as a fallback slide.** If the API hangs at
20× in front of judges, narrate the screenshot and keep the pitch moving rather than
debugging on stage.

Priority display (D43): burn rate primary, blast radius as tiebreaker **and as a label** —
*"P1 — $4.2K/hr, 78% of Brazil card volume"* vs *"P2 — $900/hr, 100% of Bank B7 on Merchant
M1."* Both numbers are computed by our code; the agent sees one incident at a time and
never sees the ordering. Blast radius is what makes a small-but-total outage legible next
to a large partial one.

---

## 9. Traps already discovered — do not rediscover these

1. **`decline_code` as a scan dimension** produces cells that are 0% by construction. It is
   a characterization attribute. (D34)
2. **Anchor drift** splits one incident into two Slack root messages mid-demo unless
   identity is pinned to the *first* anchor. (D26)
3. **Overdispersion** makes a naive `binomtest` over-fire on high-volume cells; compare each
   cell to its own historical spread. (D68)
4. **The storm door**: per-incident dedup does not protect against ten incidents opening at
   once. Cap concurrent alerts and agent runs. (D65)
5. **`budget_exhausted` must never reach the user** — report an inconclusive investigation
   instead. (D64)
6. **The reporting step must copy, never recompute**, or the dashboard and Slack disagree
   on stage. (`DASHBOARD-CONTRACT.md` §3)
7. **Cold-start cells have no baseline** — fall back through three tiers, then to
   insufficient evidence. Never silently drop. (D53)
8. **Baselines poisoned by incidents**: exclude known-incident windows from baseline
   computation. (D41)
9. **Onset ≠ detection time.** Backdate, and show both. (D52)
10. **A 3-D-only incident** may be diluted below the materiality gate in every 2-D
    projection. The playbook's drill-down is the mitigation. (T8)
11. **`issuing_bank` is null for non-card methods** — the catalogue exists so the agent does
    not claim "bank B7 is declining Pix". (D54)

---

## 10. Still open (on purpose)

| ID | Item | Closed by |
|---|---|---|
| D42 | Agent budget 120s / 15 calls, chosen by judgement | T5 |
| D50 | Reporting step: secondary agent vs plain code | nobody is blocked; decide late |
| D68 | Dispersion estimator (std / MAD / quantile / beta-binomial) | T0 EDA |
| D55 | Team study debt: why testable cells are bounded by `throughput / MIN_ATTEMPTS` | `docs/TO-STUDY.md §1`, before defending it to judges |

---

## 11. First three actions for whoever picks this up

1. Read `CONTEXT.md`, then skim `docs/DECISIONS.md`.
2. Build `store/interface.py` + `store/mock.py` against a synthetic backfill you generate
   yourself. **Do not block on Maca and Malu** — they have the contract, but you must be
   able to run T0 and T1 today regardless of when their data lands.
3. Run **T0**, choose the dispersion estimator, then build the detector and run **T1** until
   it fires zero times on clean data. Everything downstream depends on that number.
