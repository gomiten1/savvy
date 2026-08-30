"""Generate a clean, deterministic two-week gold-layer backfill for T0/T1."""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


FIELDS = ["attempt_id", "payment_id", "attempt_number", "event_ts", "merchant_id", "provider_id", "method", "country", "issuing_bank", "status", "decline_code", "amount_minor", "currency", "amount_usd"]
COUNTRIES = {"MX": ("MXN", 18.0), "CO": ("COP", 4000.0), "BR": ("BRL", 5.0)}
BANKS = {"MX": ("MX_B1", "MX_B2"), "CO": ("CO_B1", "CO_B2"), "BR": ("BR_B1", "BR_B2")}


def generate(path: Path, start: datetime, days: int, seed: int) -> int:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = start
    end = start + timedelta(days=days)
    attempt_number = 0
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        while current < end:
            hour_factor = 0.45 + 0.9 * max(0.0, math.sin((current.hour - 8) * math.pi / 12))
            weekend_factor = 0.72 if current.weekday() >= 5 else 1.0
            for country, country_factor in (("MX", 1.25), ("CO", 0.85), ("BR", 1.0)):
                # Enough traffic for 5-minute/windowed calibration while staying quick to
                # replay in a laptop demo.
                count = max(2, int(10 * hour_factor * weekend_factor * country_factor + rng.gauss(0, 2)))
                for _ in range(count):
                    attempt_number += 1
                    provider = rng.choices(("P1", "P2", "P3"), weights=(0.45, 0.35, 0.20))[0]
                    merchant = rng.choices(("M1", "M2", "M3"), weights=(0.5, 0.3, 0.2))[0]
                    method = rng.choices(("card", "pix", "spei"), weights=(0.72, 0.16 if country == "BR" else 0.02, 0.12 if country == "MX" else 0.02))[0]
                    if method == "pix" and country != "BR": method = "card"
                    if method == "spei" and country != "MX": method = "card"
                    bank = rng.choice(BANKS[country]) if method == "card" else None
                    base = 0.91 - {"P1": 0.00, "P2": 0.018, "P3": 0.035}[provider]
                    base -= {"M1": 0.0, "M2": 0.012, "M3": 0.022}[merchant]
                    base -= 0.014 if current.weekday() >= 5 else 0
                    approved = rng.random() < base
                    if approved:
                        status, code = "approved", None
                    elif rng.random() < 0.04:
                        status, code = "error", "provider_timeout"
                    else:
                        status, code = "declined", rng.choice(("do_not_honor", "insufficient_funds", "risk_blocked", "3ds_failed"))
                    currency, rate = COUNTRIES[country]
                    amount_usd = round(rng.lognormvariate(math.log(42), 0.55), 2)
                    event_ts = current + timedelta(seconds=rng.randrange(300))
                    writer.writerow({"attempt_id": f"a{attempt_number}", "payment_id": f"p{attempt_number}", "attempt_number": 1,
                                     "event_ts": event_ts.isoformat().replace("+00:00", "Z"), "merchant_id": merchant,
                                     "provider_id": provider, "method": method, "country": country, "issuing_bank": bank or "",
                                     "status": status, "decline_code": code or "", "amount_minor": round(amount_usd * rate * 100),
                                     "currency": currency, "amount_usd": amount_usd})
            current += timedelta(minutes=5)
    return attempt_number


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/synthetic_backfill.csv")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=20260829)
    args = parser.parse_args()
    count = generate(Path(args.output), datetime(2026, 8, 1, tzinfo=timezone.utc), args.days, args.seed)
    print(f"wrote {count} clean attempts to {args.output}")
