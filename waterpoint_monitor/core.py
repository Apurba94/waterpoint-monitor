"""Water point checks against planning targets.

The defaults follow figures commonly quoted from the Sphere Handbook (2018). They are starting
points, and each response should set its own targets with its WASH lead.
"""
from dataclasses import dataclass
from datetime import date

TARGETS = {"litres_per_person": 15.0, "people_per_tap": 250, "people_per_handpump": 500, "queue_minutes": 30, "chlorine_low": 0.2, "chlorine_high": 0.5, "distance_m": 500, "max_turbidity": 5.0}


@dataclass
class Reading:
    day: date
    litres: float = None
    people: int = None
    queue: float = None
    chlorine: float = None
    coliform: float = None
    turbidity: float = None


@dataclass
class Point:
    id: int
    name: str
    kind: str          # tap or handpump
    taps: int = 1
    distance: float = 0


def litres_per_person(r):
    if r.litres is None or not r.people:
        return None
    return r.litres / r.people


def check(point, r, t=TARGETS):
    """Problems with one reading, as (severity, message). Unsafe water is always critical."""
    out = []
    lpp = litres_per_person(r)
    if lpp is not None and lpp < t["litres_per_person"]:
        out.append(("high" if lpp < t["litres_per_person"] * 0.5 else "medium", f"{lpp:.1f} litres per person, target {t['litres_per_person']:g}"))
    if r.coliform is not None and r.coliform > 0:
        out.append(("critical", f"E. coli found: {r.coliform:g} per 100 ml"))
    if r.chlorine is not None:
        if r.chlorine < t["chlorine_low"]:
            out.append(("high", f"free chlorine {r.chlorine:g} mg/l is below {t['chlorine_low']:g}"))
        elif r.chlorine > t["chlorine_high"]:
            out.append(("medium", f"free chlorine {r.chlorine:g} mg/l is above {t['chlorine_high']:g}"))
    if r.turbidity is not None and r.turbidity > t["max_turbidity"]:
        out.append(("medium", f"turbidity {r.turbidity:g} NTU is above {t['max_turbidity']:g}"))
    if r.queue is not None and r.queue > t["queue_minutes"]:
        out.append(("medium", f"queue of {r.queue:g} minutes"))
    if r.people:
        limit = (t["people_per_handpump"] if point.kind == "handpump" else t["people_per_tap"]) * max(point.taps, 1)
        if r.people > limit:
            out.append(("medium", f"{r.people} people use it, limit {limit}"))
    if point.distance > t["distance_m"]:
        out.append(("low", f"{point.distance:g} m from households, target {t['distance_m']}"))
    return out


SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def worst(problems):
    return min((p[0] for p in problems), key=SEVERITY.get, default="ok")


def outages(events):
    """events: sorted list of (day, is_working). Returns finished outages as (start, end)."""
    out, start = [], None
    for day, working in events:
        if not working and start is None:
            start = day
        elif working and start is not None:
            out.append((start, day))
            start = None
    return out, start


def reliability(events, first, last):
    """Share of days working, breakdowns, mean days to repair and mean days between failures."""
    done, ongoing = outages(events)
    span = max((last - first).days, 1)
    down = sum((b - a).days for a, b in done)
    if ongoing:
        down += (last - ongoing).days
    fails = len(done) + (1 if ongoing else 0)
    return {"uptime": max(0.0, 1 - down / span), "breakdowns": fails, "mttr": sum((b - a).days for a, b in done) / len(done) if done else None, "mtbf": (span - down) / fails if fails else None, "down_now": ongoing is not None}


def repair_priority(points):
    """Rank broken points by people affected and how far they must go instead.
    points: list of dicts with name, people, nearest_other_m and days_down."""
    def score(p):
        return p["people"] * (1 + p.get("nearest_other_m", 0) / 500) * (1 + 0.1 * p.get("days_down", 0))
    return sorted(points, key=lambda p: (-score(p), p["name"]))


def coverage(points, readings_by_point, population):
    """Water supplied per person across the whole site, which can hide unequal access."""
    litres = sum(r.litres for rs in readings_by_point.values() for r in rs[-1:] if r.litres is not None)
    return litres / population if population else None


def equity(values):
    """Gini coefficient of litres per person across points: 0 is equal, 1 is one point has it all."""
    v = sorted(x for x in values if x is not None)
    n = len(v)
    if n < 2 or sum(v) == 0:
        return 0.0
    return sum((2 * i - n + 1) * x for i, x in enumerate(v)) / (n * sum(v))
