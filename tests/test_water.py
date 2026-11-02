import unittest
from datetime import date, timedelta

from waterpoint_monitor import core, create_app
from waterpoint_monitor.core import Point, Reading, check, equity, litres_per_person, outages, reliability, repair_priority, worst

D = date(2026, 11, 2)
TAP = Point(1, "A", "tap", 2)


def msgs(p, r):
    return " | ".join(m for _, m in check(p, r))


class TestChecks(unittest.TestCase):
    def test_a_good_reading(self):
        self.assertEqual(check(TAP, Reading(D, 7500, 400, 10, 0.3, 0, 1)), [])

    def test_litres(self):
        self.assertEqual(litres_per_person(Reading(D, 1500, 100)), 15)
        self.assertIsNone(litres_per_person(Reading(D, 1500, 0)))
        self.assertEqual(check(TAP, Reading(D, 1000, 100))[0][0], "medium")
        self.assertEqual(check(TAP, Reading(D, 500, 100))[0][0], "high")

    def test_e_coli_is_critical_at_any_level(self):
        self.assertEqual(worst(check(TAP, Reading(D, coliform=1))), "critical")

    def test_chlorine_band(self):
        self.assertIn("below", msgs(TAP, Reading(D, chlorine=0.1)))
        self.assertIn("above", msgs(TAP, Reading(D, chlorine=0.9)))
        self.assertEqual(check(TAP, Reading(D, chlorine=0.2)), [])
        self.assertEqual(check(TAP, Reading(D, chlorine=0.5)), [])

    def test_a_zero_chlorine_reading_counts_as_a_reading(self):
        self.assertIn("below", msgs(TAP, Reading(D, chlorine=0)))

    def test_crowding_scales_with_taps_and_kind(self):
        self.assertEqual(check(TAP, Reading(D, people=500)), [])
        self.assertIn("limit 500", msgs(TAP, Reading(D, people=501)))
        self.assertEqual(check(Point(2, "B", "handpump"), Reading(D, people=500)), [])

    def test_distance_and_queue(self):
        self.assertIn("600 m", msgs(Point(1, "A", "tap", distance=600), Reading(D)))
        self.assertIn("queue", msgs(TAP, Reading(D, queue=45)))

    def test_custom_targets(self):
        t = {**core.TARGETS, "litres_per_person": 20}
        self.assertTrue(check(TAP, Reading(D, 1800, 100), t))

    def test_worst(self):
        self.assertEqual(worst([]), "ok")
        self.assertEqual(worst([("low", ""), ("high", "")]), "high")


class TestReliability(unittest.TestCase):
    def d(self, n):
        return D + timedelta(days=n)

    def test_outages(self):
        self.assertEqual(outages([(self.d(1), False), (self.d(3), True), (self.d(5), False)]), ([(self.d(1), self.d(3))], self.d(5)))

    def test_reliability(self):
        r = reliability([(self.d(10), False), (self.d(14), True), (self.d(20), False), (self.d(22), True)], D, self.d(100))
        self.assertEqual((r["breakdowns"], r["mttr"], r["down_now"]), (2, 3, False))
        self.assertAlmostEqual(r["uptime"], 0.94)
        self.assertEqual(r["mtbf"], 47)

    def test_ongoing_outage_counts_until_today(self):
        r = reliability([(self.d(90), False)], D, self.d(100))
        self.assertTrue(r["down_now"])
        self.assertAlmostEqual(r["uptime"], 0.9)

    def test_no_failures(self):
        r = reliability([], D, self.d(10))
        self.assertEqual((r["uptime"], r["mtbf"]), (1, None))

    def test_repair_order_favours_more_people(self):
        rows = repair_priority([dict(name="small", people=50), dict(name="big", people=800)])
        self.assertEqual(rows[0]["name"], "big")

    def test_repair_order_counts_distance_and_days(self):
        rows = repair_priority([dict(name="near", people=100, nearest_other_m=50), dict(name="far", people=100, nearest_other_m=800, days_down=4)])
        self.assertEqual(rows[0]["name"], "far")

    def test_equity(self):
        self.assertEqual(equity([10, 10, 10]), 0)
        self.assertAlmostEqual(equity([0, 0, 0, 100]), 0.75)
        self.assertEqual(equity([5]), 0)


class TestApp(unittest.TestCase):
    def setUp(self):
        self.a = create_app(":memory:", lambda: D)
        self.a.post("/points", name="Tap 1", kind="tap", taps=2, distance=100)
        self.a.post("/points", name="Pump 1", kind="handpump", taps=1, distance=100)

    def reading(self, pid=1, **kw):
        return self.a.post(f"/point/{pid}", working="", day="2026-11-01", **kw)

    def text(self, url="/"):
        return self.a.get(url).body.decode()

    def test_reading_flows_to_overview(self):
        self.assertEqual(self.reading(litres=1000, people=100, chlorine=0.3).status, 303)
        self.assertIn("10.0", self.text())
        self.assertIn("target 15", self.text())

    def test_unsafe_water_comes_first(self):
        self.reading(2, litres=5000, people=100, coliform=3)
        page = self.text()
        self.assertLess(page.index("Pump 1"), page.index("Tap 1"))
        self.assertIn("critical", page)

    def test_blank_fields_stay_blank(self):
        self.reading(litres=9000, people=400)
        self.assertIsNone(self.a.one("SELECT chlorine FROM readings"))

    def test_breakdown_and_repair_list(self):
        self.reading(2, litres=5000, people=300)
        self.assertEqual(self.a.post("/point/2", working=0, note="Handle snapped").status, 303)
        self.assertIn("broken", self.text())
        self.assertIn("Pump 1", self.text("/repairs"))
        self.a.post("/point/2", working=1)
        self.assertIn("Nothing is broken", self.text("/repairs"))
        self.assertIn("Breakdowns", self.text("/point/2"))

    def test_refusals(self):
        self.assertEqual(self.a.post("/points", name="x", kind="river").status, 400)
        self.assertEqual(self.a.post("/points", name="Tap 1", kind="tap").status, 409)
        self.assertEqual(self.a.get("/point/9").status, 404)
        self.assertEqual(self.reading(litres="lots").status, 400)


if __name__ == "__main__":
    unittest.main()
