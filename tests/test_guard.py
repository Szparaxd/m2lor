"""Ochrona (recon/guard.py): wykrywanie graczy/GM, wstrzymanie i wznowienie, pomijanie swoich postaci,
wybor celu walki w trakcie wstrzymania. Bez gry: atrapa radaru (SimpleNamespace)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recon"))
import botapi
import guard
import radar_map


class FakeTrace:
    def __init__(self):
        self.rows, self.lines = [], []

    def rec(self, _k, /, **f):
        self.rows.append(dict(f, k=_k))

    def text(self, line):
        self.lines.append(line)


def fake_radar(actors, marks=(), nick="Bot1"):
    """actors: lista (x, y, nick, is_pc, vid). Postac w (0, 0)."""
    r = types.SimpleNamespace()
    tr = FakeTrace()
    r.trace = tr
    r._tr = lambda: tr
    r._dbg = lambda fmt, *a: tr.text(fmt % a if a else fmt)
    r._hero_world = lambda: (0.0, 0.0)
    r.ident = {"nick": nick}
    r.actors = list(actors)
    r.gm_marks = list(marks)
    r._iter_actors = lambda hx, hy: [(x, y, 1, pc, nm, vid) for x, y, nm, pc, vid in r.actors]
    r._mob_name = lambda o: o
    r._route, r._last_plan, r._walk_prog = [1], 5.0, (1, 2)
    r._looting = False
    r.ended_loot = []
    r._end_looting = lambda now: r.ended_loot.append(now)
    r.autoloot_on, r.area_atk_on, r.farm_on = True, True, True
    r._engaged_vid, r._engaged_cls, r.walk_idx = 5, "mob", 0
    return r


def make_guard(r, **st):
    with mock.patch.object(guard, "own_nicks", lambda now=None: set()), \
            mock.patch.object(guard, "IGNORE_PATH", str(Path(tempfile.gettempdir()) / "brak_guard_ignore.txt")):
        g = guard.Guard(r, dict(dict(sound=False), **st))
        g._refresh_lists(0)                     # bez beaconow / pliku ignorowanych z dysku
    g._own_t = 1e18                             # nie odswiezaj z dysku w trakcie testu
    g._ign_mtime = None
    return g


def step(g, now):
    with mock.patch.object(guard.os.path, "getmtime", side_effect=OSError):
        g._last_step = 0.0
        g.step(now)


class GuardDetectTest(unittest.TestCase):
    def test_player_in_radius_holds_and_resumes_after_delay(self):
        r = fake_radar([(1000, 0, "Obcy", True, 11)])
        g = make_guard(r, radius=3000, resume=30)
        step(g, 100.0)
        self.assertTrue(g.hold)
        self.assertEqual(g.threats[0]["name"], "Obcy")
        self.assertFalse(g.threats[0]["gm"])
        self.assertTrue(any(x["k"] == "guard" and x["ev"] == "hold" for x in r.trace.rows))
        r.actors = []
        step(g, 110.0)
        self.assertTrue(g.hold)                 # za wczesnie na wznowienie
        step(g, 131.0)
        self.assertFalse(g.hold)
        self.assertEqual(r._route, [])          # trasa planowana od nowa po wznowieniu

    def test_player_outside_radius_and_mobs_ignored(self):
        r = fake_radar([(4000, 0, "Daleki", True, 11), (100, 0, "Dzik", False, 12)])
        g = make_guard(r, radius=3000)
        step(g, 1.0)
        self.assertFalse(g.hold)
        self.assertEqual(g.threats, [])

    def test_gm_by_mark_detected_beyond_radius(self):
        r = fake_radar([(8000, 0, "GameCoreTop", True, 11)], marks=[(8010, 30)])
        g = make_guard(r, radius=3000, react="gm")
        step(g, 1.0)
        self.assertTrue(g.hold)
        self.assertTrue(g.threats[0]["gm"])
        self.assertIn("logo GM", g.threats[0]["via"])

    def test_gm_by_name_prefix(self):
        r = fake_radar([(500, 0, "[GM]Admin", True, 11)])
        g = make_guard(r, react="gm")
        step(g, 1.0)
        self.assertTrue(g.hold and g.threats[0]["gm"])

    def test_react_gm_only_ignores_plain_player(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        g = make_guard(r, react="gm")
        step(g, 1.0)
        self.assertFalse(g.hold)
        self.assertEqual(len(g.threats), 1)    # widoczny w stanie, ale bez reakcji

    def test_own_and_ignored_characters_skipped(self):
        r = fake_radar([(500, 0, "Bot2", True, 11), (600, 0, "Kumpel", True, 12)])
        g = make_guard(r)
        g._own = {"bot2"}
        g._ign = {"kumpel"}
        step(g, 1.0)
        self.assertFalse(g.hold)
        self.assertEqual(g.threats, [])

    def test_stop_disabled_only_reports(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        g = make_guard(r, stop=False)
        step(g, 1.0)
        self.assertFalse(g.hold)
        self.assertIn("zatrzymanie wylaczone", g.status)

    def test_resume_zero_needs_manual_release(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        g = make_guard(r, resume=0)
        step(g, 1.0)
        r.actors = []
        step(g, 5000.0)
        self.assertTrue(g.hold)
        g.release("recznie")
        self.assertFalse(g.hold)

    def test_hold_ends_running_loot(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        r._looting = True
        g = make_guard(r)
        step(g, 1.0)
        self.assertEqual(len(r.ended_loot), 1)

    def test_disabling_stop_releases_hold(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        g = make_guard(r)
        step(g, 1.0)
        g.apply(stop=False)
        self.assertFalse(g.hold)

    def test_sound_on_new_threat_then_throttled(self):
        r = fake_radar([(500, 0, "Obcy", True, 11)])
        g = make_guard(r, sound=True)
        with mock.patch.object(guard, "play") as play:
            step(g, 1.0)
            step(g, 2.0)
            self.assertEqual(play.call_count, 1)
            r.actors.append((700, 0, "[GM]X", True, 12))
            step(g, 3.0)
            self.assertEqual(play.call_count, 2)
            self.assertEqual(play.call_args[0][0], guard.SOUND_GM)


class GuardCombatTest(unittest.TestCase):
    """_combat_tick w trakcie wstrzymania: bez nowego celu z pick_fn, ale obrona przed napastnikiem."""

    def radar(self, hold, aggressor=None):
        r = types.SimpleNamespace()
        r.guard = types.SimpleNamespace(hold=hold, hold_why="gracz X d=500")
        r._engaged_vid = 0
        r._appr = None
        r._eng_route, r._combat_target, r._combat_status = [], None, ""
        r.cleared = []
        r._clear_autoattack = lambda: r.cleared.append("atk")
        r._blocker_target = lambda hx, hy: None
        r._aggressor_target = lambda hx, hy: aggressor
        r.picked = []
        r._end_target = lambda: None
        r._vid_class = lambda vid, ism: "mob"
        r._dbg = lambda *a: None
        r.engaged = []
        r._engage = lambda hx, hy, vid, tx, ty, ism: r.engaged.append(vid)
        r._combat_tick = types.MethodType(radar_map.RadarMap._combat_tick, r)
        return r

    def pick(self, r):
        def fn(hx, hy):
            r.picked.append(1)
            return (9, 100.0, 0.0, False, False)
        return fn

    def test_hold_skips_new_targets(self):
        r = self.radar(hold=True)
        self.assertFalse(r._combat_tick(0.0, 0.0, self.pick(r)))
        self.assertEqual(r.picked, [])
        self.assertIn("STOP", r._combat_status)

    def test_hold_still_fights_aggressor(self):
        r = self.radar(hold=True, aggressor=(4, 50.0, 0.0, False, False))
        self.assertTrue(r._combat_tick(0.0, 0.0, self.pick(r)))
        self.assertEqual(r.engaged, [4])
        self.assertEqual(r.picked, [])

    def test_no_hold_uses_pick(self):
        r = self.radar(hold=False)
        self.assertTrue(r._combat_tick(0.0, 0.0, self.pick(r)))
        self.assertEqual(r.engaged, [9])


class GuardApiTest(unittest.TestCase):
    def test_options_and_choices_registered(self):
        for name in ("guard", "guard_react", "guard_stop", "guard_sound", "guard_radius", "guard_resume"):
            self.assertIn(name, botapi.OPTIONS)
        self.assertEqual(botapi.CHOICES["guard_react"], guard.REACT_CHOICES)
        self.assertIn("guard_release", botapi.ACTIONS)


if __name__ == "__main__":
    unittest.main()
