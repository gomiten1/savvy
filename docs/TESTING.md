# What we must test (and tune from)

Every number in the system is a guess until one of these runs. Order = priority.

## T0. Descriptive EDA on the backfill (BLOCKS T1 and the choice of estimator)
Before choosing any statistic, look at the data. Per cell, and per `(cell, hour_of_week)`:
distribution of windowed conversion rates, mean/median, variance vs. the binomial variance
`p(1-p)/n` (the ratio is the overdispersion factor), residual distribution after removing
the hour-of-week level, skew, fat tails, autocorrelation between consecutive windows, and
how many cells actually clear the volume gate at each candidate `min_attempts`.

**Only then** choose the dispersion estimator (std vs MAD vs empirical quantile vs
beta-binomial) and the gate values. Choosing before this is the same a-priori guessing we
rejected for the thresholds.

## T1. Noise calibration (BLOCKS the p-value / drop / volume thresholds)
Replay the full clean backfill through the detector with no injected incident.
**Pass = zero incidents opened.** Tune `p_threshold`, `min_abs_drop_pp`, `min_attempts`
until it passes with the widest possible margin. Record the final values and the margin.
Output: the pitch line "two weeks of normal traffic, zero alerts".

## T2. Detection latency
Replay backfill + one injected incident at a known T0. Measure sim-time from T0 to
`incident.opened_at`, for incidents of 3 magnitudes (-10pp, -25pp, -60pp) and 2 volumes
(high-traffic cell, near-threshold cell). Produces the honest latency table for the pitch.

## T3. Anchor correctness
For each injected incident with a known true cell, assert the incident's `identity_cell`
is the true cell or its direct parent. Catches anchor drift and clustering bugs.

## T4. Two-incident separation
Inject two disjoint incidents overlapping in time. Assert exactly 2 incidents open, with
disjoint clusters, and correct anchors. This is a scored demo item - test it explicitly.

## T5. Agent budget calibration (tunes the 120s / 15 tool-call cap)
Run the agent on 5+ recorded incidents. Record per run: tool calls used, wall seconds,
whether it hit the cap, and whether the diagnosis was correct. The cap is currently
chosen by feel - this test is what justifies it. If runs finish in 6 calls, lower it;
if correct diagnoses are being truncated at the cap, raise it.

## T6. Insufficient-evidence honesty
Inject an incident in a cell below the volume gate, and separately inject an ambiguous
one (two plausible causes). Assert the system reports insufficient evidence rather than
naming a cause. Bonus-point scenario - must be demoable on purpose, not by accident.

## T7. Repeat recognition
Seed `incidents.db` with a past incident, then inject the same cell again. Assert the
agent's output references the prior incident. Bonus-point scenario.

## T8. Deep (3-D) incident
Inject an incident that only exists at 3-D, where every 2-D projection is diluted.
Assert either: the agent drills down and finds it, OR the system honestly reports it
cannot isolate. Both are acceptable; silently naming the wrong 2-D cell is not.
This is the trial-by-fire rehearsal.

## T9. Alert volume / no spam
Run a 40-minute (sim) incident. Assert exactly ONE root Slack message, and that the
agent ran once. Catches the lifecycle/dedup logic.

## T10. Cost sanity
Assert burn rate is within an order of magnitude of a hand-computed value for one
injected incident. Guards against a number nobody sanity-checked reaching the exec line.
