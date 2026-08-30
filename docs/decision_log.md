# Savvy Decision Log

This is Savvy's single decision log. It explains the choices that shape the product in plain English. It replaces the former `docs/DECISIONS.md` and the earlier pipeline decision log.

## The decisions that matter most

| Decision | Why it matters |
| --- | --- |
| Use rules to detect incidents; use AI to explain them | An alert is opened by measurable data, not by an AI guess. AI investigates an incident only after it exists and suggests next steps. |
| Compare each payment route with its own normal behavior | A problem affecting one provider, country, payment method, or bank can be hidden in a global average. Savvy checks each meaningful route against its historical baseline. |
| Keep generated payment evidence | Savvy saves the original simulated provider event and the routing context. This makes events inspectable and replayable after the demo. |
| Clean data according to each provider's business rules | Each simulated provider sends different fields and payment statuses. Savvy understands those differences instead of forcing every provider into the same incomplete shape. |
| Use separate storage for history and live traffic | Compact historical data is fast for pattern analysis. Live attempts are stored separately so the generator and detector can work at the same time during a demo. |
| Recommend actions, but do not make payment changes | Savvy can tell an owner what to investigate or change. It never changes payment routing automatically. |
| Keep reporting independent from alerting | A dashboard or report failure must not stop detection or delay the first alert. |

## Problem detection

- Savvy measures conversion as approved attempts divided by total attempts.
- It looks for statistically meaningful conversion drops with enough traffic and enough business impact. Better-than-normal conversion does not create an incident.
- It checks populated combinations of merchant, provider, payment method, country, and issuing bank. It does not create a huge set of empty combinations.
- Related signals are grouped into one incident. Independent issues stay separate, even when they happen at the same time.
- An incident opens only after repeated signals and resolves only after repeated healthy checks. This reduces alert noise.
- Lost approvals and estimated financial impact help prioritize incidents. A retry that later succeeds is not counted as permanently lost revenue.

## Baselines and historical patterns

Savvy needs a definition of “normal” before it can identify an unusual drop.

- Historical data contains normal traffic patterns and expected volume changes, but no injected incidents. A known failure should not become part of the normal baseline.
- Baselines are calculated for each route whenever enough history exists. When history is missing, Savvy reports insufficient evidence instead of pretending to be certain.
- Historical conversion data is kept at minute level. Decline and recovery analysis is kept at hourly level. This provides useful detail without creating an unnecessarily large raw-event history.
- Recovery after a decline is measured separately from conversion. A retry pattern is useful evidence, but it is not treated as the same metric as an approval-rate drop.


### Keeping generated event logs

Every live generated event is written to `data/bronze/events.jsonl`. Each record keeps:

- the original provider-shaped payload;
- the provider name and ingestion time; and
- the routing information known by the payment orchestrator, such as merchant, method, country, and retry context.

The file keeps one rotated previous segment when it reaches its size limit. Bronze data is useful for audit and replay, but Savvy does not scan it every time it checks for an incident.

### Building patterns efficiently

Savvy creates durable historical aggregates for conversion and decline patterns. They are stored in Parquet and `historical.duckdb` and are used to calculate baselines.

This separation is intentional:

- **Bronze logs** preserve the original generated evidence.
- **Historical aggregates** provide the compact, fast data needed to build and evaluate patterns.
- **Live SQLite data** holds individual current attempts while the demo is running.

### Cleaning provider data realistically

The cleaner also follows these rules:

- Duplicate vendor events are ignored before counting them, which models repeated webhooks or retries.
- Provider decline codes are mapped to a shared set of categories so they can be compared consistently.
- Savvy uses routing metadata only when it is actually available. Missing provider, method, country, or issuer information remains unknown instead of being guessed.
- Bad, unsupported, or incomplete events are written to `data/silver/quarantine.jsonl`. One bad event cannot stop the pipeline.

These are realistic rules for the simulated providers. They do not claim that Savvy is connected to production payment-provider APIs.

## Diagnosis and recommendations

After an incident opens, Savvy gathers evidence: counts, event samples, baseline context, and similar past incidents.

- The AI has a limited set of tools and a limited time budget. It cannot run arbitrary queries.
- A diagnosis can be `high`, `medium`, or `insufficient_evidence`. Savvy shows uncertainty instead of inventing a percentage.
- Recommendations come from a known action catalogue with owners and preconditions. This prevents impossible or unsafe suggestions.


## Alerts, reporting, and dashboard

- A deterministic Slack alert is sent as soon as an incident opens.
- The AI diagnosis is posted later as a reply when it is ready.
- Reports are versioned: Savvy creates a new revision for the first diagnosis, a meaningful change, and resolution.
- The dashboard reads the latest report for each incident and the detail view can show its history.
- Reports are exported atomically to `data/dashboard-reports.json`, so the browser receives a complete contract-shaped file.
- If notifications or publishing fail, detection continues.

## Demo and runtime choices

- The live demo uses accelerated simulated time and supports a controlled incident injection without restarting the generator.
- The intended flow is simple: show healthy traffic, inject a route-specific conversion drop, show the alert, then show the diagnosis and dashboard report.
- The MVP uses one main Python process for detection and diagnosis, plus a separate live generator. This is enough for the hackathon without introducing unnecessary infrastructure.
- The repository includes a Fly deployment configuration, but the local reproducible demo is the primary operating mode.

## Current limits

- Future calibration should optimize the cost of false alerts, missed incidents, and detection delay once labeled outcomes are available.

