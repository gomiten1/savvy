# Savvy Conversion Incident Control

Savvy is a static dashboard for monitoring payment-conversion incidents. It shows open revenue risk, severity, affected routes, and evidence-backed incident reports.

## Run locally

No build step is required.

```bash
python -m http.server 8000
```

Open `http://localhost:8000` in a modern browser.

## Verify the dashboard

```bash
node verify-dashboard-contract.js
```

The verifier checks the seed data, contract fields, severity caps, aggregate totals, required UI states, and key accessibility safeguards.

## Project files

| File | Purpose |
| --- | --- |
| `index.html` | Dashboard and incident log |
| `incident-detail.html` | Incident report detail view |
| `dashboard-data.js` | Canonical seeded `incident_reports` data and validation |
| `dashboard-utils.js` | Formatting and time utilities |
| `dashboard-index.js` | Dashboard summaries, filters, sorting, and incident list |
| `dashboard-detail.js` | Detail rendering, revision history, and copy control |
| `dashboard.css` | Shared responsive UI system |
| `savvy.png` | Brand logo |
| `savvy.svg` | Page favicon |
| `DASHBOARD-CONTRACT.md` | Required data and presentation contract |

## Dashboard behavior

- Aggregates appear before the incident log.
- The incident log supports search, status, severity, confidence, and sort controls.
- Every row links to the latest incident revision.
- Detail pages answer what dropped, since when, who is affected, how much money is at risk, and why the system believes the report.
- Timestamps include relative and UTC absolute values.
- Amounts use USD formatting without compact rounding.

## Severity rules

Severity is derived from a shared risk score and is capped by confidence:

| Confidence | Maximum score | Highest possible severity |
| --- | ---: | --- |
| High | 100 | S1 |
| Medium | 74 | S2 |
| Insufficient evidence | 49 | S3 |

## Seed states

The seeded reports include:

- Open, high-confidence incident
- Resolved incident
- Insufficient-evidence incident
- Repeat incident
- Incident with visible revision history

## Browser support

The dashboard supports current Chrome, Edge, Firefox, Safari, and modern mobile browsers.
