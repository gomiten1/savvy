# Data Contract 2 — incident reports → dashboard

Owners: producer = detection/diagnosis (us). Consumer = dashboard team.
Companion to `DATA-CONTRACT.md` (contract 1, **inbound**: pipeline → detector).
This is contract 2, **outbound**: diagnosis → dashboard.

Status: **DRAFT — dashboard team may start building against it now.**

## 1. Why a second store

`incidents.db` is operational hot-path state: small, latency-sensitive, written inside
the detector loop, read by the agent. This is a read-heavy analytical sink queried by a
dashboard over history. Different access pattern, different consumer, different failure
tolerance — so it is a separate store.

**Store:** `reports.db` (SQLite for MVP, swappable for Postgres later). Append-only.
**Join key:** `incident_id` — identical to the one in `incidents.db`.

## 2. The reporting process (brief)

```
detector → incident opens → Slack root message      ← critical path, never blocked
                          → agent investigates
                          → Slack thread reply       ← critical path ends here
                                  │
                                  └→ reporting step → reports.db → dashboard
                                        (asynchronous, may be slow, may retry)
```

The reporting step runs **after** diagnosis completes. It is strictly downstream:

- It is **not** on the alerting critical path. If it fails, lags, or is offline,
  Slack alerting is entirely unaffected.
- It may be a secondary agent (to expand prose for two audiences) or plain code.
  That choice is not yet made and does not block the dashboard team.
- It is **idempotent per `(incident_id, revision)`**. Safe to retry.

A new revision is published on: first diagnosis (revision 1), a material change that
re-runs the agent, and incident resolution.

## 3. THE ONE RULE THAT MATTERS

The reporting step **copies** every deterministic figure from the main path
(`onset_ts`, `burn_rate_usd_hour`, `blast_radius`, `affected_entities`, rates, counts).
It must **never recompute them**.

If a secondary agent re-derives the money or the onset independently, the dashboard and
the Slack message will disagree — in front of judges. The reporting step may only
*rewrite prose* (expand the ops explanation, generate an executive summary). It may
never restate a figure.

## 4. Table `incident_reports`

One row per `(incident_id, revision)`.

| Field | Type | Notes |
|---|---|---|
| `incident_id` | string | joins to `incidents.db` |
| `revision` | int | 1 on first publish; increments on re-run and on resolve |
| `published_at` | timestamp | |
| **what dropped** | | |
| `anchor_cell` | JSON | e.g. `{"provider":"P2","country":"BR"}` |
| `metric` | string | always `attempt_conversion_rate` for now |
| `baseline_rate` | float | |
| `observed_rate` | float | |
| `drop_pp` | float | absolute percentage points |
| `dominant_decline_code` | string | the characterization signal |
| `baseline_source` | enum | `hour_of_week` \| `all_time` \| `inherited_from_parent` \| `none` |
| **since when** | | |
| `onset_ts` | timestamp | **backdated**, not detection time |
| `detected_at` | timestamp | |
| `detection_latency_s` | int | onset → detected. Displayed, not hidden |
| `resolved_at` | timestamp | null while open |
| `status` | enum | `open` \| `resolved` |
| **who it affects** | | |
| `affected_entities` | JSON array | `[{"dimension":"merchant","value":"M1","share_of_impact":0.82}, ...]` |
| `blast_radius` | float | share of the anchor cell's own traffic failing |
| `affected_attempts` | int | |
| **how much money** | | |
| `burn_rate_usd_hour` | float | |
| `cumulative_loss_usd` | float | loss so far; grows while open |
| `cost_basis` | enum | `gross` \| `net_of_retry_recovery` — must be labelled, retry recovery may be 0 |
| **why the system believes that** | | |
| `exec_one_liner` | text | one sentence, money first |
| `ops_explanation` | text | operations language, not statistics |
| `evidence` | JSON array | `[{"claim": "...", "support": "..."}]` — same list sent to Slack |
| `confidence` | enum | `high` \| `medium` \| `insufficient_evidence` |
| `recommended_action` | text | never executed, only recommended |
| `alternatives_ruled_out` | JSON array | what the agent checked and rejected |

**Live view** = latest `revision` per `incident_id`.
**Audit/replay view** = full revision history.

## 5. DDL (create it now)

```sql
CREATE TABLE incident_reports (
  incident_id           TEXT    NOT NULL,
  revision              INTEGER NOT NULL,
  published_at          TEXT    NOT NULL,

  anchor_cell           TEXT    NOT NULL,   -- JSON
  metric                TEXT    NOT NULL DEFAULT 'attempt_conversion_rate',
  baseline_rate         REAL    NOT NULL,
  observed_rate         REAL    NOT NULL,
  drop_pp               REAL    NOT NULL,
  dominant_decline_code TEXT,
  baseline_source       TEXT    NOT NULL,

  onset_ts              TEXT    NOT NULL,
  detected_at           TEXT    NOT NULL,
  detection_latency_s   INTEGER NOT NULL,
  resolved_at           TEXT,
  status                TEXT    NOT NULL,

  affected_entities     TEXT    NOT NULL,   -- JSON array
  blast_radius          REAL    NOT NULL,
  affected_attempts     INTEGER NOT NULL,

  burn_rate_usd_hour    REAL    NOT NULL,
  cumulative_loss_usd   REAL    NOT NULL,
  cost_basis            TEXT    NOT NULL,

  exec_one_liner        TEXT    NOT NULL,
  ops_explanation       TEXT    NOT NULL,
  evidence              TEXT    NOT NULL,   -- JSON array
  confidence            TEXT    NOT NULL,
  recommended_action    TEXT,
  alternatives_ruled_out TEXT,              -- JSON array

  PRIMARY KEY (incident_id, revision)
);

CREATE INDEX idx_reports_status   ON incident_reports(status, published_at DESC);
CREATE INDEX idx_reports_incident ON incident_reports(incident_id, revision DESC);
```

## 6. Seed data — build against this today

The dashboard team should not wait on the detector or the agent. Seed `reports.db` with
four fabricated rows covering every UI state:

1. an **open** incident, high confidence, large burn rate (provider × country)
2. a **resolved** incident (bank × merchant), with `resolved_at` and `cumulative_loss_usd`
3. an **`insufficient_evidence`** incident — the UI must render a report with no named
   root cause and an empty-ish `alternatives_ruled_out`
4. a **repeat** of the cell in row 2, to exercise "this already happened" display

Also seed one incident with **two revisions** so the revision history view is exercised.

Example row (JSON fields shown expanded):

```json
{
  "incident_id": "inc_20260829_140300_a1",
  "revision": 1,
  "published_at": "2026-08-29T14:07:41Z",
  "anchor_cell": {"provider": "P2", "country": "BR"},
  "metric": "attempt_conversion_rate",
  "baseline_rate": 0.87, "observed_rate": 0.19, "drop_pp": 68.0,
  "dominant_decline_code": "provider_timeout",
  "baseline_source": "hour_of_week",
  "onset_ts": "2026-08-29T14:03:00Z",
  "detected_at": "2026-08-29T14:06:10Z",
  "detection_latency_s": 190,
  "resolved_at": null, "status": "open",
  "affected_entities": [
    {"dimension": "merchant", "value": "M1", "share_of_impact": 0.61},
    {"dimension": "merchant", "value": "M3", "share_of_impact": 0.39}
  ],
  "blast_radius": 0.78,
  "affected_attempts": 4120,
  "burn_rate_usd_hour": 4210.0,
  "cumulative_loss_usd": 279.0,
  "cost_basis": "gross",
  "exec_one_liner": "PagoTotal is losing ~$4.2K/hour on Brazil card volume through Provider 2 since 14:03.",
  "ops_explanation": "Provider 2 started timing out on Brazilian card traffic at 14:03. Approvals fell from 87% to 19%. Other providers in Brazil are unaffected, and Provider 2 is healthy in Mexico and Colombia, so this is Provider 2's Brazil route specifically, not a country-wide or issuer problem.",
  "evidence": [
    {"claim": "The drop is confined to Provider 2 in Brazil", "support": "P1 and P3 in BR held at 86-88% over the same window"},
    {"claim": "It is a provider fault, not an issuer fault", "support": "declines shifted from do_not_honor to provider_timeout (4% -> 81%)"},
    {"claim": "It is not country-wide", "support": "P2 approval in MX 88%, CO 86%, unchanged"}
  ],
  "confidence": "high",
  "recommended_action": "Route Brazil card traffic away from Provider 2 and open a severity-1 with them citing the timeout spike from 14:03 UTC.",
  "alternatives_ruled_out": [
    "Brazil-wide issue (other providers healthy)",
    "Issuing bank outage (spread across all BR issuers, not concentrated)",
    "Merchant-specific (both M1 and M3 affected proportionally to volume)"
  ]
}
```

## 7. Open items (do not block the dashboard team)

- Whether the reporting step is a secondary agent or plain code.
- Whether `reports.db` stays SQLite or moves to Postgres.
- Retention / revision pruning policy.
