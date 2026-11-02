"""The offline web app."""
from datetime import date

from . import core
from .fieldkit import App, Fail, Raw, bar_chart, card, form, line_chart, link, redirect, stats, table

SCHEMA = """
CREATE TABLE IF NOT EXISTS points (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, taps INTEGER NOT NULL DEFAULT 1, distance REAL NOT NULL DEFAULT 0, working INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY, point INTEGER NOT NULL REFERENCES points(id), day TEXT NOT NULL, litres REAL, people INTEGER, queue REAL, chlorine REAL, coliform REAL, turbidity REAL);
CREATE TABLE IF NOT EXISTS status (id INTEGER PRIMARY KEY, point INTEGER NOT NULL REFERENCES points(id), day TEXT NOT NULL, working INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '');
"""
COLOURS = {"critical": "bad", "high": "bad", "medium": "warn", "low": "muted", "ok": "good"}


def create_app(db="water.db", today=date.today):
    a = App("Waterpoint Monitor", db, SCHEMA, [("Overview", "/"), ("Add a point", "/points"), ("Repairs", "/repairs")])

    def point(r):
        return core.Point(r["id"], r["name"], r["kind"], r["taps"], r["distance"])

    def reading(r):
        return core.Reading(date.fromisoformat(r["day"]), r["litres"], r["people"], r["queue"], r["chlorine"], r["coliform"], r["turbidity"])

    def latest(pid):
        r = a.row("SELECT * FROM readings WHERE point = ? ORDER BY day DESC, id DESC LIMIT 1", pid)
        return reading(r) if r else None

    @a.route("/")
    def home(req):
        rows = []
        for p in a.rows("SELECT * FROM points ORDER BY name"):
            last = latest(p["id"])
            problems = core.check(point(p), last) if last else []
            rows.append({"p": p, "last": last, "problems": problems, "level": core.worst(problems) if p["working"] else "high"})
        rows.sort(key=lambda r: (core.SEVERITY.get(r["level"], 9), r["p"]["name"]))
        lpp = [core.litres_per_person(r["last"]) for r in rows if r["last"]]
        lpp = [x for x in lpp if x is not None]
        body = stats([("Points", len(rows)), ("Working", sum(r["p"]["working"] for r in rows)), ("Needs action", sum(1 for r in rows if r["level"] in ("critical", "high"))), ("Mean litres a person", f"{sum(lpp) / len(lpp):.1f}" if lpp else "n/a"), ("Unevenness (0 to 1)", f"{core.equity(lpp):.2f}")])
        body += card("Water points, most urgent first", table(rows, [("Point", lambda r: link(f"/point/{r['p']['id']}", r["p"]["name"])), ("State", lambda r: Raw(f'<span class="{COLOURS[r["level"]]}">{"broken" if not r["p"]["working"] else r["level"]}</span>')), ("Litres a person", lambda r: "" if not r["last"] or core.litres_per_person(r["last"]) is None else f"{core.litres_per_person(r['last']):.1f}"), ("Problems", lambda r: "; ".join(m for _, m in r["problems"]))], "Add a water point to begin."))
        return a.page("Overview", body)

    @a.route("/points", ("GET", "POST"))
    def points(req):
        if req.method == "POST":
            kind = req.text("kind")
            if kind not in ("tap", "handpump"):
                raise Fail("Unknown kind")
            a.run("INSERT INTO points (name, kind, taps, distance) VALUES (?, ?, ?, ?)", req.text("name"), kind, int(req.number("taps", 1, 1, 100)), req.number("distance", 0, 0))
            return redirect("/")
        return a.page("Add a point", card("Water point", form(a, "/points", [("name", "Name"), ("kind", "Kind", "select", [("tap", "Tap stand"), ("handpump", "Hand pump")]), ("taps", "Taps", "number", 1), ("distance", "Distance from furthest shelter (m)", "number", 0)], "Add")))

    @a.route("/point/<id>", ("GET", "POST"))
    def page(req, id):
        p = a.row("SELECT * FROM points WHERE id = ?", id)
        if p is None:
            raise Fail("No such point", 404)
        if req.method == "POST":
            if req.get("working") != "":
                working = int(req.number("working", low=0, high=1))
                a.run("UPDATE points SET working = ? WHERE id = ?", working, id)
                a.run("INSERT INTO status (point, day, working, note) VALUES (?, ?, ?, ?)", id, today().isoformat(), working, req.text("note", False))
            else:
                opt = lambda k: req.number(k) if str(req.get(k)).strip() else None
                a.run("INSERT INTO readings (point, day, litres, people, queue, chlorine, coliform, turbidity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", id, req.date("day"), opt("litres"), opt("people"), opt("queue"), opt("chlorine"), opt("coliform"), opt("turbidity"))
            return redirect(f"/point/{id}")
        rs = a.rows("SELECT * FROM readings WHERE point = ? ORDER BY day", id)
        events = [(date.fromisoformat(s["day"]), bool(s["working"])) for s in a.rows("SELECT * FROM status WHERE point = ? ORDER BY day, id", id)]
        body = ""
        if rs:
            first = date.fromisoformat(rs[0]["day"])
            rel = core.reliability(events, first, today())
            body += stats([("Uptime", f"{rel['uptime']:.0%}"), ("Breakdowns", rel["breakdowns"]), ("Days to repair", "n/a" if rel["mttr"] is None else f"{rel['mttr']:.1f}")])
            body += card("Litres per person", line_chart([(r["day"], core.litres_per_person(reading(r)) or 0) for r in rs]))
        body += card("Add a reading", form(a, f"/point/{id}", [("working", "", "hidden", ""), ("day", "Date", "date", today().isoformat()), ("litres", "Litres today", "number"), ("people", "People using it", "number"), ("queue", "Queue (minutes)", "number"), ("chlorine", "Free chlorine (mg/l)", "number"), ("coliform", "E. coli per 100 ml", "number"), ("turbidity", "Turbidity (NTU)", "number")], "Save"))
        body += card("Report status", form(a, f"/point/{id}", [("working", "Working?", "select", [(0 if p["working"] else 1, "Mark broken" if p["working"] else "Mark repaired")]), ("note", "Note")], "Save"))
        return a.page(p["name"], body)

    @a.route("/repairs")
    def repairs(req):
        rows = []
        for p in a.rows("SELECT * FROM points WHERE working = 0"):
            last = latest(p["id"])
            s = a.row("SELECT day FROM status WHERE point = ? AND working = 0 ORDER BY id DESC LIMIT 1", p["id"])
            rows.append({"name": p["name"], "people": (last.people if last and last.people else 0), "nearest_other_m": p["distance"], "days_down": (today() - date.fromisoformat(s["day"])).days if s else 0})
        return a.page("Repairs", card("Fix in this order", table(core.repair_priority(rows), [("Point", "name"), ("People affected", "people"), ("Days down", "days_down")], "Nothing is broken.")))

    return a
