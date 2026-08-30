# Data Contract — detection/diagnosis ← data pipeline

Owners: pipeline & generator = Maca, Malu. Consumer = detection + diagnosis.
Status: **DRAFT — needs confirmation from Maca/Malu.**

> Companion: `DASHBOARD-CONTRACT.md` is contract 2 (outbound: diagnosis → dashboard).

This is the only surface between the two halves. Anything not listed here, we do not
depend on. Anything listed here, we do.

## 1. Grain

One row = **one payment attempt** (not one payment). A retried payment produces
multiple rows sharing a `payment_id`. Attempt-level is the grain because that is the
layer providers and issuing banks actually degrade.

## 2. Row schema (gold layer)

| Field | Type | Null? | Notes |
|---|---|---|---|
| `attempt_id` | string | no | unique |
| `payment_id` | string | no | groups retries of the same customer payment |
| `attempt_number` | int | no | 1-based within `payment_id` |
| `event_ts` | timestamp UTC | no | **event time**, not ingest time |
| `merchant_id` | enum | no | 3 merchants |
| `provider_id` | enum | no | 3 providers |
| `method` | enum | no | e.g. card, pix, spei, oxxo, boleto |
| `country` | enum | no | MX, CO, BR |
| `issuing_bank` | enum | **yes** | null for non-card methods |
| `status` | enum | no | `approved` \| `declined` \| `error` |
| `decline_code` | enum | **yes** | non-null iff status != approved; **fixed shared taxonomy** |
| `amount_minor` | int | no | minor units |
| `currency` | enum | no | MXN, COP, BRL |
| `amount_usd` | float | no | **normalized by pipeline** — required for cross-country cost comparison |

`decline_code` must come from a fixed, documented enum (e.g. `insufficient_funds`,
`do_not_honor`, `risk_blocked`, `provider_timeout`, `invalid_card`, `3ds_failed`).
Free-text or per-provider raw codes break the detector.

## 3. History requirement (SCHEDULE RISK)

Baselines are per-cell and seasonal (hour-of-day, day-of-week). That requires
**2–4 weeks of backfilled historical attempts** with realistic seasonality, available
before the detector can be calibrated. A live stream alone is not sufficient.

Backfill must contain: daily traffic curve, weekend effect, per-country volume skew,
and a stable-ish baseline approval rate per provider/bank.

## 4. Access interface

We poll; we do not consume a stream. Two functions, however they are implemented
(SQL, DuckDB, HTTP, Python):

```
get_counts(start_ts, end_ts, bucket, group_by[], filters{})
  -> rows of (bucket_ts, *group_by keys, attempts, approved, declined, error, amount_usd_total)

get_samples(start_ts, end_ts, filters{}, limit)
  -> raw attempt rows (for evidence quoting in the alert)
```

`group_by` may be any subset of the six dimensions. `filters` is equality-only.

## 5. Injection (demo / trial by fire)

A UI/endpoint that applies a rule from a chosen moment forward, e.g.
`provider=P2 AND country=BR -> approval_rate *= 0.25, decline_code bias -> provider_timeout`.
Judges must be able to pick an arbitrary combination of dimensions, including one we
never rehearsed. The injector must NOT tell us what it injected.

## 6. Assumptions we are making (flag if wrong)

1. Event time is trustworthy and roughly ordered; late data beyond ~2 min is rare.
2. `amount_usd` is precomputed; we never do FX.
3. Retries exist in the data but are a minority of payments.
4. Gold layer is deduplicated at the attempt level.
5. Freshness: a row is queryable within ~10s of its `event_ts`.
