"""Regresje z 2026-10-29: Trace.rec z polem 'kind', 'podejdz tutaj' i obsluga smierci postaci.
Bez gry: metody RadarMap wolane na atrapie (SimpleNamespace z potrzebnymi polami)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recon"))
import botapi
import bottrace
import radar_map


class FakeTrace:
    def __init__(self):
        self.rows, self.lines = [], []

    def rec(self, _k, /, **f):
        self.rows.append(dict(f, k=_k))

    def text(self, line):
        self.lines.append(line)


def fake_radar(hero=(1000.0, 1000.0)):
    r = types.SimpleNamespace()
    tr = FakeTrace()
    r.trace = tr
    r._tr = lambda: tr
    r._dbg = lambda fmt, *a: tr.text(fmt % a if a else fmt)
    r._hero_world = lambda: hero
    r._goto_point = None
    r._route, r._last_plan, r._walk_prog = [1], 5.0, (1, 2)
    r.farm_on = r.autowalk_on = r.area_atk_on = False
    r._looting, r.dll_exec, r.native_drink = False, True, False
    r._engaged_vid, r._engaged_big = 77, True
    r.pot_count, r._last_pot = 3, 0.0
    r._hp, r._mhp = 0, 800
    r._combat_status = ""
    r.cleared = []
    r._clear_autoattack = lambda: r.cleared.append("atk")
    r._end_target = lambda: r.cleared.append("tgt")
    for name in ("set_goto_point", "_goto_point_done", "_on_death_change"):
        setattr(r, name, types.MethodType(getattr(radar_map.RadarMap, name), r))
    return r


class TraceRecTest(unittest.TestCase):
    def test_kind_field_does_not_clash_with_record_type(self):
        with tempfile.TemporaryDirectory() as d:
            old = bottrace.LOG_DIR
            bottrace.LOG_DIR = d
            try:
                t = bottrace.Trace("t")
                t.rec("plan", kind="walk", k="x", start=[1, 2])   # wczesniej: TypeError -> padala petla
                t.close()
                row = json.loads(Path(t.jsonl_path).read_text(encoding="utf-8"))
            finally:
                bottrace.LOG_DIR = old
        self.assertEqual(row["k"], "plan")
        self.assertEqual(row["kind"], "walk")
        self.assertEqual(row["k_"], "x")


class GotoPointTest(unittest.TestCase):
    def test_start_and_done_are_logged(self):
        r = fake_radar()
        r.set_goto_point(4000, 5000)
        self.assertEqual(r._goto_point[:2], (4000.0, 5000.0))
        self.assertEqual(r._route, [])
        r._goto_point_done("dotarlem", 3990, 4990)
        self.assertIsNone(r._goto_point)
        evs = [(x["k"], x.get("ev")) for x in r.trace.rows]
        self.assertEqual(evs, [("goto_point", "start"), ("goto_point", "end")])
        self.assertTrue(any("podejdz KONIEC: dotarlem" in l for l in r.trace.lines))

    def test_api_turns_farm_off_and_sets_point(self):
        r = fake_radar()
        r.farm_on = True
        r.farm_var = types.SimpleNamespace(set=lambda v: setattr(r, "farm_on", v))
        r._on_farm_check = lambda: None
        api = botapi.BotApi(r)
        api._goto_point_fn("goto_point", {"x": 123, "y": 456})()
        self.assertFalse(r.farm_on)
        self.assertEqual(r._goto_point[:2], (123.0, 456.0))
        with self.assertRaises(ValueError):
            api._goto_point_fn("goto_point", {"x": "a"})


class DeathTest(unittest.TestCase):
    def test_death_releases_target_and_logs(self):
        r = fake_radar()
        r._on_death_change(True)
        self.assertEqual(r.cleared, ["atk", "tgt"])
        self.assertIn("martwa", r._combat_status)
        self.assertEqual(r.trace.rows[-1]["k"], "death")
        r._hp = 500
        r._on_death_change(False)
        self.assertEqual(r.trace.rows[-1]["k"], "revive")
        self.assertEqual(r._combat_status, "")


if __name__ == "__main__":
    unittest.main()
