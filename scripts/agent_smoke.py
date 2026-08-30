#!/usr/bin/env python3
"""One real agent investigation against a canned incident.  Needs OPENAI_API_KEY.

    PYTHONPATH=. python scripts/agent_smoke.py

Costs a fraction of a cent and takes ~20s.  Use it before a rehearsal to confirm the
whole diagnosis path is alive -- model id, tool schemas, the tool loop, JSON parsing,
the action guardrail and owner resolution -- without generating pipeline data or
waiting on the detector.  The fixture is a synthetic adyen x MX x card timeout
incident in the real vocabulary (D75); the tests cover the deterministic seams.
"""
import os, random, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

for line in Path(".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

from agent_workflow.agent.run import DiagnosisRunner
from agent_workflow.agent.tools import DiagnosisTools
from agent_workflow.baselines.load import BaselineLookup
from agent_workflow.detect.registry import Incident
from agent_workflow.detect.scan import Signal
from agent_workflow.economics import blast_radius, burn_rate_usd_hour
from agent_workflow.slack.templates import format_diagnosis
from agent_workflow.store.interface import Attempt
from agent_workflow.store.mock import MockStore

UTC = timezone.utc
NOW = datetime(2026, 8, 30, 14, 9, tzinfo=UTC)
ONSET = NOW - timedelta(minutes=6)
rng = random.Random(7)

# adyen x MX x card degrades into network timeouts from ONSET; everything else normal.
rows, n = [], 0
for minute in range(60):
    ts = NOW - timedelta(minutes=59 - minute)
    for provider in ("adyen", "stripe", "mercadopago"):
        for country in ("MX", "BR"):
            for method in ("card", "oxxo" if country == "MX" else "pix"):
                broken = provider == "adyen" and country == "MX" and method == "card" and ts >= ONSET
                for _ in range(12):
                    n += 1
                    ok = rng.random() < (0.30 if broken else 0.90)
                    if ok:
                        status, code = "approved", None
                    elif broken and rng.random() < 0.80:
                        status, code = "error", "91_96_network_timeout"
                    else:
                        status, code = "declined", rng.choice(["05_do_not_honor", "51_insufficient_funds"])
                    rows.append(Attempt(
                        attempt_id=f"a{n}", payment_id=f"p{n}", attempt_number=1, event_ts=ts,
                        merchant_id=rng.choice(["merch_globex", "merch_acme"]), provider_id=provider,
                        method=method, country=country,
                        issuing_bank="bbva" if provider == "mercadopago" else "unknown_bank",
                        status=status, decline_code=code, amount_minor=100_000,
                        currency="MXN" if country == "MX" else "BRL", amount_usd=60.0))

store = MockStore(rows)
lookup = BaselineLookup({"min_history_observations": 2, "cells": {
    "provider=adyen|country=MX|method=card": {"hour_of_week_rates": {}, "hour_of_week_observations": {},
                                              "all_time_rate": 0.90, "dispersion": 0.01}}})
anchor = Signal(cell={"provider": "adyen", "country": "MX", "method": "card"},
                observed_rate=0.31, baseline_rate=0.90, baseline_source="all_time", z_score=59.0,
                lost_approvals=425.0, attempts=720, approved=223, error_share=0.55,
                decline_code_mix={"91_96_network_timeout": 0.79, "05_do_not_honor": 0.12,
                                  "51_insufficient_funds": 0.09},
                amount_usd_total=43_200.0)
incident = Incident(incident_id="inc_20260830_140900_001", identity_cell=dict(anchor.cell),
                    current_anchor=anchor, onset_ts=ONSET, opened_at=NOW,
                    burn_rate_usd_hour=burn_rate_usd_hour(anchor), blast_radius=blast_radius(anchor),
                    last_evaluated_at=NOW)

calls = []
tools = DiagnosisTools(store, lookup)
original = tools.call
tools.call = lambda name, args: (calls.append((name, args)), original(name, args))[1]

t = time.monotonic()
diagnosis = DiagnosisRunner(tools).investigate(incident)
print(f"=== {time.monotonic() - t:.1f}s · {len(calls)} tool calls ===")
for name, args in calls:
    print(f"  {name}({ {k: v for k, v in args.items() if v not in (None, {}, [])} })"[:150])
print(f"\nconfidence={diagnosis.confidence}  action_id={diagnosis.action_id}  "
      f"params={diagnosis.action_parameters}  internal={diagnosis.low_confidence_reason}")
print("\n--- SLACK THREAD ---")
print(format_diagnosis(incident, diagnosis))
