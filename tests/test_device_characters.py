"""Tests for the rest-screen characters of the Armonico Pi device (device/ui/characters.py)."""
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "device" / "ui"


def load(name):
    spec = importlib.util.spec_from_file_location(f"armonico_ui_{name}_test", UI / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ch = load("characters")


class Reactions(unittest.TestCase):
    def setUp(self):
        self.c = ch.Character(seed=1)
        self.c.last = (400, 300, 1.0)

    def test_tap_on_character_laughs_and_extends(self):
        c = self.c
        self.assertTrue(c.touch(400, 300, 10.0))
        self.assertEqual(c.reaction(10.5), "happy")
        self.assertIsNone(c.reaction(11.4))
        c.touch(400, 300, 11.0)
        self.assertEqual(c.reaction(12.2), "happy")      # 1.3 s after the last touch, not the first
        self.assertIsNone(c.reaction(12.4))

    def test_five_quick_taps_make_it_dizzy(self):
        c = self.c
        for i in range(4):
            c.touch(400, 300, 10.0 + i * 0.3)
        self.assertEqual(c.reaction(11.0), "happy")
        c.touch(400, 300, 11.2)
        self.assertEqual(c.reaction(11.3), "dizzy")
        c.touch(400, 300, 13.0)                          # a further touch keeps it dizzy
        self.assertEqual(c.reaction(15.5), "dizzy")
        self.assertIsNone(c.reaction(15.7))

    def test_slow_taps_stay_happy(self):
        c = self.c
        for i in range(6):
            c.touch(400, 300, 10.0 + i * 1.0)
        self.assertEqual(c.reaction(15.1), "happy")

    def test_touch_beside_it_only_looks(self):
        c = self.c
        self.assertFalse(c.touch(700, 300, 5.0))
        self.assertIsNone(c.reaction(5.1))
        self.assertEqual(c.look_t[0], 1.0)              # clamped
        self.assertEqual(c.look_t[1], 0)
        self.assertEqual(c.hop_t, -9.0)                # no hop: only a touch on the figure hops
        # it keeps looking there for 2.8 s, eased toward it
        t = 5.0
        while t < 7.7:
            t += 1 / 30
            c.update(t)
        self.assertGreater(c.look[0], 0.95)

    def test_box_follows_scale(self):
        c = self.c
        c.last = (400, 300, 2.0)
        self.assertTrue(c.box_hit(400 + 150, 300))
        self.assertFalse(c.box_hit(400 + 175, 300))
        self.assertTrue(c.box_hit(400, 300 - 180))
        self.assertFalse(c.box_hit(400, 300 + 131))

    def test_key_glance(self):
        c = self.c
        c.key(0, 3.0)
        self.assertLess(c.look_t[0], -0.9)
        self.assertGreater(c.look_t[1], 0)
        c.key(11, 3.1)
        self.assertGreater(c.look_t[0], 0.9)
        self.assertAlmostEqual(c.look_hold, 4.1)

    def test_glances_by_itself(self):
        c = self.c
        targets, t = set(), 0.0
        while t < 60:
            t += 0.1
            c.update(t)
            targets.add(tuple(round(v, 3) for v in c.look_t))
        self.assertGreater(len(targets), 5)
        self.assertIn((0.0, 0.0), targets)

    def test_owl_lifecycle(self):
        c = self.c
        c.start_owl(100.0)
        self.assertFalse(c.asleep(101.0))
        self.assertTrue(c.asleep(102.5))
        self.assertTrue(c.asleep(500.0))                 # asleep until a touch
        c.touch(10, 10, 500.1)
        self.assertIsNone(c.owl)
        self.assertFalse(c.asleep(500.2))
        c.start_owl(600.0)
        c.stop_owl()
        self.assertIsNone(c.owl)

    def test_low_for_every_kind(self):
        self.assertEqual(set(ch.LOW), set(ch.KINDS))


@unittest.skipUnless(importlib.util.find_spec("pygame"), "pygame is not installed")
class Drawing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        import pygame.gfxdraw  # noqa: F401
        pygame.init()
        cls.pg = pygame
        cls.cvm = load("canvas")
        cls.fonts = cls.cvm.Fonts(pygame, ROOT / "device" / "media" / "fonts")

    def canvas(self, w, h, rtl=False):
        cv = self.cvm.Canvas(self.pg, self.pg.Surface((w, h)), self.fonts)
        cv.rtl = rtl
        return cv

    def test_every_kind_and_state_draws(self):
        for size in ((800, 480), (1920, 1080)):
            for rtl in (False, True):
                cv = self.canvas(*size, rtl)
                for kind in ch.KINDS:
                    for state in ("normal", "happy", "dizzy", "cool", "owl", "asleep"):
                        c = ch.Character(seed=2)
                        if state in ("happy", "dizzy"):
                            c.react = ch.Reaction(state, 99)
                        if state == "cool":
                            c.cool_until = 99
                        if state in ("owl", "asleep"):
                            c.start_owl(0.0 if state == "owl" else -10.0)
                        for t in (0.0, 0.05, 1.37, 4.2, 9.9):
                            c.update(t)
                            c.draw(cv, kind, 400, 280, t, s=1.3)
                            c.draw_owl(cv, "he" if rtl else "en", t)
                        self.assertEqual(c.last, (400, 280, 1.3))

    def test_character_is_on_the_surface(self):
        cv = self.canvas(800, 480)
        c = ch.Character(seed=3)
        cv.fill((0, 0, 0))
        c.draw(cv, "house", 400, 240, 1.0)
        self.assertNotEqual(tuple(cv.surf.get_at((400, 240)))[:3], (0, 0, 0))
        self.assertEqual(tuple(cv.surf.get_at((100, 240)))[:3], (0, 0, 0))

    def test_physical_in_hebrew(self):
        """The figure never mirrors: drawn at the left in Hebrew it stays at the left."""
        cv = self.canvas(800, 480, rtl=True)
        cv.fill((0, 0, 0))
        ch.Character(seed=4).draw(cv, "grand", 150, 240, 1.0)
        self.assertNotEqual(tuple(cv.surf.get_at((150, 250)))[:3], (0, 0, 0))
        self.assertEqual(tuple(cv.surf.get_at((650, 250)))[:3], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
