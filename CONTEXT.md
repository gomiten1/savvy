# Context

Domain glossary for the PagoTotal conversion monitoring and diagnosis system.
Glossary only - no implementation details. Decisions live in `docs/DECISIONS.md`.

## Attempt
A single try at charging a payment through one provider. **The grain of all our data.**
An attempt ends `approved`, `declined`, or `error`.

## Payment
One customer purchase. May consist of several Attempts if the orchestrator retried
across providers. A Payment is recovered if any of its Attempts is approved.

## Conversion rate
`approved_attempts / total_attempts` over a window, for a given Cell.
Always attempt-level. Payment-level recovery is used for money, never for detection.

## Dimension
An attribute an Attempt can be sliced by. Five **scan dimensions**: merchant, provider,
method, country, issuing_bank. `decline_code` is deliberately *not* a scan dimension -
approved attempts have no decline code, so a conversion rate grouped by it is meaningless.

## Decline code
Normalized reason an Attempt failed. A **characterization attribute**, not a scan
dimension: used to explain a Cell that is already anomalous (a shift from
`do_not_honor` toward `provider_timeout` distinguishes an issuer story from a provider
story).

## Cell
A specific combination of Dimension values, e.g. `{provider: P2, country: BR}`.
Cells form a lattice: `{provider: P2}` is the **parent** of `{provider: P2, country: BR}`.

## Baseline
The Conversion rate a Cell is expected to have, given the hour of week. Computed offline
from historical Attempts, excluding periods belonging to known Incidents.

## Signal
One Cell whose observed Conversion rate is anomalous versus its Baseline in the current
window, having passed the statistical, materiality and volume gates. A Signal is **not**
an alert - it is never shown to a human on its own.

## Lost approvals
`attempts x (baseline_rate - observed_rate)` for a Cell. The impact metric. Used to rank
Signals and to pick the Anchor cell.

## Cluster
A group of Signals judged to be the same underlying story, because their Cells are
lattice-related (one is a parent or child of another).

## Anchor cell
The Signal in a Cluster with the highest Lost approvals. Becomes the Cluster's
representative.

## Incident
A Cluster that has persisted long enough to be believed, promoted to a tracked object
with a lifecycle. Its **identity** is its *first* Anchor cell and never changes, even
though the current Anchor may drift as the Incident matures. One Incident produces
exactly one root Slack message.

## Blast radius
The share of a Cell's own traffic that is failing. Distinguishes a partial degradation of
a large Cell from a total outage of a small one.

## Burn rate
Estimated money lost per hour while an Incident is open. The executive-facing number.

## Diagnosis
The agent's account of an Incident: which Cell is the true origin, the evidence for it,
the confidence, and a recommended action. May legitimately be **insufficient evidence**.

## Playbook
The investigative guide given to the agent - how to explore, not what to compute.
Deliberately not an algorithm.
