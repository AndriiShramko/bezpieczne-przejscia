"""Generate SYNTHETIC demo data for the dashboard (no real observations).

Simulates 14 days of aggregate counters for one demonstration crossing,
with a realistic diurnal profile, sampled head-down proxy with Wilson CI,
random failover/downtime windows (rendered as no-data, never zeros),
and a camera-health event trail.
"""
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

random.seed(20260709)

DAYS = 14
END = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
START = END - timedelta(days=DAYS)


def diurnal(hour: int, weekday: int) -> float:
    """Pedestrian intensity profile 0..1 (morning/evening peaks, weekend flatter)."""
    base = 0.06 + 0.5 * math.exp(-((hour - 8) ** 2) / 6.0) + \
        0.62 * math.exp(-((hour - 16.5) ** 2) / 9.0)
    if weekday >= 5:
        base = 0.10 + 0.42 * math.exp(-((hour - 13) ** 2) / 16.0)
    return min(1.0, base)


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> None:
    # downtime windows (source failures): list of (start_offset_h, dur_h, kind)
    downtimes = []
    for _ in range(6):
        s = random.uniform(0, DAYS * 24 - 5)
        downtimes.append((s, random.uniform(0.4, 3.0)))

    def observed_frac(off_h: float) -> float:
        for s, d in downtimes:
            if s <= off_h < s + d:
                return 0.0 if (off_h - s) > 0.15 else 0.6  # partial first hour
        return 1.0

    hourly = []
    health = []
    t = START
    off = 0.0
    while t < END:
        w = diurnal(t.hour, t.weekday())
        frac = observed_frac(off)
        obs_sec = round(3600 * frac)
        if obs_sec < 900:  # <25% coverage -> no-data
            hourly.append({"t": t.strftime("%Y-%m-%dT%H:%MZ"), "no_data": True,
                           "observed_sec": obs_sec})
        else:
            ped = max(0, round(random.gauss(320 * w, 22 * (w + .2))))
            veh = max(0, round(random.gauss(520 * (0.35 + 0.65 * w), 30)))
            sample_n = min(ped, random.randint(18, 42)) if ped else 0
            head_k = round(sample_n * min(.9, max(.02, random.gauss(.17, .05))))
            lo, hi = wilson(head_k, sample_n)
            yield_v = max(0, round(random.gauss(2.6 * w, 1.1)))
            conflicts = max(0, round(random.gauss(1.1 * w, 0.8)))
            hourly.append({
                "t": t.strftime("%Y-%m-%dT%H:%MZ"), "no_data": False,
                "observed_sec": obs_sec,
                "ped": ped, "veh": veh,
                "head_sample_n": sample_n, "head_down_n": head_k,
                "head_down_ci": [round(lo, 3), round(hi, 3)],
                "yield_violations": yield_v, "conflicts": conflicts,
            })
        t += timedelta(hours=1)
        off += 1.0

    for s, d in sorted(downtimes):
        ts = START + timedelta(hours=s)
        health.append({"t": ts.strftime("%Y-%m-%dT%H:%MZ"), "source": "primary",
                       "event": "down", "detail": "stream stall"})
        health.append({"t": ts.strftime("%Y-%m-%dT%H:%MZ"), "source": "backup",
                       "event": "failover", "detail": "auto-switch"})
        te = START + timedelta(hours=s + d)
        health.append({"t": te.strftime("%Y-%m-%dT%H:%MZ"), "source": "primary",
                       "event": "recovered", "detail": "failback"})

    covered = sum(1 for h in hourly if not h["no_data"])
    data = {
        "synthetic": True,
        "generated_utc": "2026-07-09T00:00Z",
        "crossing": {
            "id": "demo-crossing",
            "label_pl": "Przejście demonstracyjne (dane syntetyczne)",
            "label_en": "Demonstration crossing (synthetic data)",
            "pool": [{"source": "primary", "state": "up"},
                     {"source": "backup", "state": "standby"}],
        },
        "coverage_pct": round(100.0 * covered / len(hourly), 1),
        "hourly": hourly,
        "health": health,
    }
    out = os.path.join(os.path.dirname(__file__), "public", "data")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "demo.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"demo.json: {len(hourly)} hourly buckets, {covered} covered, "
          f"{len(hourly) - covered} no-data, coverage {data['coverage_pct']}%")


if __name__ == "__main__":
    main()
