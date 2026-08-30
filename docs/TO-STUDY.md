# Accepted but not yet understood

Things we agreed to that Sebastian wants to actually understand before defending them
in front of judges. Not blockers - study debt.

## 1. Why the number of testable cells is bounded by `throughput / min_attempts`

**The claim:** scanning all 1-D and 2-D slices does not blow up as merchants, providers,
countries and banks are added. The number of cells we actually run a test on is bounded
by `attempts_in_window / min_attempts`, i.e. it grows with *traffic*, not with the
*number of possible combinations*.

**The intuition to check:** every attempt lands in exactly one cell per grouping. A cell
only becomes testable if it holds at least `min_attempts` attempts. So the attempts in
the window get divided among testable cells, and you cannot have more than
`total_attempts / min_attempts` of them - the same way 1000 coins cannot fill more than
20 jars if every jar needs 50 coins. Adding 1000 new banks with no traffic adds 1000
empty cells, and empty cells are never enumerated (`GROUP BY` returns only non-empty
groups) and never tested (volume gate).

**What to verify:** that this holds per *grouping* and what the constant actually is once
you sum over all 10 pairs plus 5 singles - the bound is
`15 x total_attempts / min_attempts` in the worst case, and far lower in practice because
payment traffic is power-law distributed (a few merchant/country/provider combos carry
most volume).

**Why it matters:** it is the difference between "we hand-picked dimensions because we had
to" and "we hand-picked a core for business relevance, and the rest is free". Only the
second survives a judge asking "does this scale?".

Related: D35, D36.

## 2. What the production architecture would be (so we can explain the MVP honestly)

We ship one process with threads (D67). That is the right MVP call, but we must be able to
say what we would build at scale and why we did not build it now.

| Concern | MVP (18h) | Production |
|---|---|---|
| Ingestion | Poll a local store | CDC from the payment service into a log (Kafka), or a columnar store with pre-aggregated rollups (ClickHouse / Druid) |
| Windowed aggregates | `GROUP BY` per poll | Incrementally maintained windowed state (Flink / Spark Structured Streaming) or materialized rollups refreshed continuously |
| Detector | Loop in a thread | Stateless horizontally-scaled service, **sharded by dimension subset**, so the 1-D/2-D scan parallelises |
| Baselines | Computed once at startup | Scheduled batch (Airflow/dbt) writing **versioned immutable artifacts** to object storage; atomic pointer swap; previous version retained for rollback (this part we already do - D40) |
| Incident registry | In-memory dict + SQLite write-through | Postgres, single leader-elected writer, so a restart does not lose in-flight incidents (D39's accepted limitation) |
| Agent execution | Worker thread + `queue.Queue` | Durable queue with retries and a dead-letter path; worker pool; per-incident idempotency key |
| Reports | `reports.db` (SQLite) | Same separation, on Postgres/ClickHouse (already the shape - D46) |
| Observability | Terminal logs | Metrics on the detector itself: cells evaluated, signals fired, incidents opened, agent latency, budget exhaustion rate. **Alerting on the alerting** |

The design already has the production seams in the right places - the store is behind an
interface (D12), baselines are immutable versioned artifacts (D40), reporting is a separate
non-blocking sink (D47). What is missing is durability and horizontal scale, and both are
substitutions behind existing seams rather than rewrites. That is the answer to "how would
you productionise this?".

## 3. Lower-priority: timezone handling in hour-of-week baselines

We key baselines on hour-of-week. Attempts span MX (UTC-6), CO (UTC-5) and BR (UTC-3), so a
UTC hour-of-week curve for a cell spanning countries is a *mix* of three local diurnal
curves. This is fine as long as the country mix within a cell is stable, which it is for
country-scoped cells and roughly is for others. Risk is low in practice (Brazil abolished
DST in 2019, Mexico in 2022, Colombia has none), but worth knowing if a baseline looks
wrong for a multi-country cell.
