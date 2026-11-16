"""The anime theme of the Armonico Pi device (device/ui/anime.py): the hour's pictures, the colors, the episode,
the girl's feeling sounds, and the engine's phase that the theme follows."""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import test_device_ui as T  # noqa: E402

DATA = ROOT / "device" / "ui" / "data" / "anime"


def anime_mod():
    sys.path.insert(0, str(ROOT / "device"))
    import ui.anime as A
    return A


class Hours(unittest.TestCase):
    def test_every_altitude_gives_one_picture_or_a_blend_of_two(self):
        A = anime_mod()
        for morning in (True, False):
            for alt10 in range(-900, 901, 5):
                mix = A.sky_mix(alt10 / 10, morning)
                self.assertIn(len(mix), (1, 2))
                self.assertAlmostEqual(sum(w for _, w in mix), 1.0, places=6)
                for name, _ in mix:
                    self.assertTrue((DATA / f"{name}.jpg").exists(), name)

    def test_the_blend_moves_slowly_no_jump_between_two_minutes(self):
        A = anime_mod()
        for morning in (True, False):
            prev = None
            for alt100 in range(-1500, 4000, 6):           # 0.06 degrees: about 15 seconds of the Sun
                mix = dict(A.sky_mix(alt100 / 100, morning))
                if prev is not None:
                    names = set(mix) | set(prev)
                    jump = sum(abs(mix.get(n, 0) - prev.get(n, 0)) for n in names)
                    self.assertLess(jump, 0.05, (alt100, morning))
                prev = mix

    def test_night_and_day_and_the_evening_room(self):
        A = anime_mod()
        self.assertEqual(A.sky_mix(-30, False), [("bg_08", 1.0)])
        self.assertEqual(A.sky_mix(50, True), [("bg_03", 1.0)])
        self.assertEqual(A.sky_mix(9, True), [("bg_02", 1.0)])
        self.assertEqual(A.sky_mix(7, False), [("bg_04", 1.0)])
        self.assertEqual(A.sky_mix(30, False), [("bg_03", 1.0)])     # a November afternoon is not golden yet
        self.assertNotIn("bg_01", [n for _a, n in A.MORNING + A.EVENING])   # the sunrise with the lens flare
        self.assertEqual(A.period(-20, False), "night")
        self.assertEqual(A.period(5, False), "evening")
        self.assertEqual(A.period(5, True), "day")

    def test_the_windows_light_one_at_a_time(self):
        A = anime_mod()
        self.assertTrue((DATA / "windows.json").exists() and (DATA / "windows_lit.png").exists())
        n = len(json.loads((DATA / "windows.json").read_text())["windows"])
        self.assertGreater(n, 50)
        self.assertGreater(A.Anime.lit_share(20.5), A.Anime.lit_share(3))
        anime = object.__new__(A.Anime)
        day0 = 1763164800                                    # a midnight
        for hour in (3, 20):
            lit = [anime.window_level(i, day0 + hour * 3600) for i in range(n)]
            self.assertTrue(all(0.0 <= v <= 1.0 for v in lit))
        before = [anime.window_level(i, day0 + 20 * 3600) for i in range(n)]
        after = [anime.window_level(i, day0 + 20 * 3600 + 60) for i in range(n)]
        changed = sum(1 for a, b in zip(before, after) if a != b)
        self.assertLess(changed, n // 4)                    # a minute later only a few changed, never all at once

    def test_the_palette_has_every_key_of_the_others(self):
        A = anime_mod()
        sys.path.insert(0, str(ROOT / "device"))
        from ui import sky
        keys = set(sky.palette("classic", True, None))
        for alt in (-20, 30, None):
            self.assertEqual(set(A.palette(alt)), keys)
        self.assertEqual(sky.palette("anime", True, 30), A.palette(30))


class Files(unittest.TestCase):
    def test_the_prepared_pictures_are_in_the_project(self):
        names = ["bg_02", "bg_03", "bg_04", "bg_05", "bg_06", "bg_08", "room_07_day", "room_07_evening",
                 "room_07_night", "room_08", "room_09", "room_10", "room_11", "eyecatch", "fx_lines", "fx_golden"]
        names += [f"close_{k}_{w}" for k in ("face", "hands") for w in ("day", "evening", "night")]
        names += [f"face_{n:02d}" for n in (8, 9, 10, 11)]
        for n in names:
            self.assertTrue((DATA / f"{n}.jpg").exists(), n)
        pngs = [f"cat_{i}" for i in range(4)] + [f"curtain_{i}" for i in range(3)] + ["logo_light"]
        pngs += ["manpu_" + m for m in ("drop", "sparkle", "question", "bang", "anger", "bubble", "surprise", "dots")]
        for n in pngs:
            self.assertTrue((DATA / f"{n}.png").exists(), n)

    def test_no_bg_07_the_moon_is_drawn(self):
        self.assertFalse((DATA / "bg_07.jpg").exists())


@unittest.skipIf(T.pygame is None, "pygame is not installed")
class Screens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = T.load_screen()

    def app(self, size=(800, 480), lang="en"):
        app = T.make_app(self.mod, size, lang)
        app.st["theme"] = "anime"
        app.screen.pub = mock.Mock()
        return app

    def test_every_view_draws_with_the_anime_theme(self):
        A = anime_mod()
        for size in ((800, 480), (1920, 1080)):
            for lang in ("en", "he"):
                for alt, morning in ((40, True), (4, False), (-20, False)):
                    with mock.patch.object(A, "sun_now", return_value=(alt, morning)):
                        app = self.app(size, lang)
                        for name, open_it in T.Views.views(None, app):
                            app.screen.engine.state = {"mode": "idle", "volume": 70}
                            open_it()
                            with self.subTest(size=size, lang=lang, alt=alt, view=name):
                                app.draw()
                                self.assertTrue(app.bar_items)

    def test_a_theme_among_the_others_in_the_settings(self):
        app = self.app()
        self.assertEqual(app.theme, "anime")
        self.assertEqual(app.tr("th.anime"), "Anime")

    def test_the_opening_plays_once_per_episode_and_then_starts_the_lesson(self):
        app = self.app()
        started = []
        with mock.patch.object(app.screen.engine, "post", side_effect=lambda *a, **k: started.append(a)):
            app.screen.start_lesson("ode_to_joy")
            self.assertTrue(app.anime.scene_on(), "episode 6 of ode_to_joy: the opening first")
            self.assertFalse(started)
            app.draw()
            app.button("ok", False)                   # any button skips it, and the lesson starts
            self.assertFalse(app.anime.scene_on())
        self.assertTrue(app.screen.lesson_request_pending)
        app.screen.lesson_request_pending = False
        self.assertFalse(app.anime_preroll("ode_to_joy"), "the same episode again: the lesson starts at once")

    def test_a_learned_song_has_no_new_episode(self):
        app = self.app()
        app.screen.engine.overview["songs"][0].update(learned=8, total=8)
        self.assertFalse(app.anime_preroll("ode_to_joy"))

    def test_the_summary_becomes_the_credits(self):
        app = self.app()
        app.screen.engine.state = {"mode": "idle", "summary": {"id": 7, "song": "ode_to_joy", "learned": 6,
                                                               "total": 8, "score": 90, "secs": 300, "new": 1}}
        app.draw()
        self.assertTrue(app.anime.scene_on())
        kinds = [s["kind"] for s in app.anime.scene["shots"]]
        self.assertEqual(kinds, ["credits", "tag", "next"])
        rows = {k: v for k, v, _ in app.anime.credit_lines()}
        self.assertEqual(rows["Last run"], "90%")
        self.assertEqual(rows["Time"], "5:00")
        self.assertEqual(rows["Parts you know"], "6 of 8")
        self.assertEqual(rows["This lesson"], "Part 6, new")
        flat = " ".join(" ".join(r) for r in app.anime.credit_lines())
        self.assertNotIn("Vesta Core Labs", flat, "the credits are Armonico's; Vesta Core Labs is on About only")
        self.assertNotIn("Starring", flat)
        for shot in app.anime.scene["shots"]:
            at = shot["at"] + shot["dur"] / 2
            with mock.patch.object(app, "t", return_value=at):
                app.draw()
        app.pointer_down(10, 10)
        self.assertFalse(app.anime.scene_on(), "a touch ends it")

    def test_the_eyecatch_fills_the_breath_before_the_duet(self):
        app = self.app()
        st = {"mode": "wait", "midi": [60, 62], "expect": 0, "total": 2, "pos": 1, "phase": "final", "band": True}
        app.screen.engine.state = dict(st)
        app.tick()
        self.assertFalse(app.anime.eyecatch_on())
        app.screen.engine.state = dict(st, phase="final_breath")
        app.tick()
        self.assertTrue(app.anime.eyecatch_on())
        app.draw()
        self.assertEqual([b[0] for b in app.bar_items], ["■"], "the lesson can still be left")
        app.screen.engine.state = dict(st, phase="final_band", counting=True)
        app.tick()
        self.assertTrue(app.anime.eyecatch_on(), "through the count-in")
        app.anime.eyecatch_at -= 5
        app.screen.engine.state = dict(st, phase="final_band", counting=False)
        app.tick()
        self.assertFalse(app.anime.eyecatch_on(), "gone for the first note")
        app.screen.engine.state = dict(st, phase="final_breath")
        app.tick()
        self.assertFalse(app.anime.eyecatch_on(), "once a lesson")

    def test_a_feeling_is_a_sound_sent_as_fx_never_as_a_key(self):
        app = self.app()
        app.anime.sound("happy")
        for _ in range(40):
            app.anime.tick()
            time.sleep(0.02)
        msgs = [c.args[1] for c in app.screen.pub.call_args_list if c.args[0] == "piano/device/note"]
        self.assertTrue(msgs)
        self.assertTrue(all(re.fullmatch(r"fx(on|off) \d+", m) for m in msgs), msgs)

    def test_a_wrong_key_and_a_right_round_make_her_react(self):
        app = self.app()
        st = {"mode": "wait", "midi": [60, 62, 64], "expect": 1, "total": 3, "pos": 0, "phase": "new", "live": []}
        app.screen.engine.state = dict(st)
        app.tick()
        app.screen.engine.state = dict(st, live=[[1, 70]])
        app.tick()
        self.assertEqual(app.anime.cutin["face"], "girl", "the oops drawn frame by frame")
        self.assertEqual(app.anime.girl.drawing(time.monotonic() + 0.3), "oops_peak")     # after its 3-frame in-between
        app.screen.engine.state = dict(st, live=[[1, 70]], mode="ok")
        app.tick()
        self.assertEqual(app.anime.cutin["face"], "girl", "the laugh drawn frame by frame")
        app.draw()


@unittest.skipIf(T.pygame is None, "pygame is not installed")
class MangaPanel(unittest.TestCase):
    def test_the_lesson_has_the_panel_on_top_and_the_keys_under_it(self):
        self.assertTrue((DATA / "manga_panel.png").exists(), "tools/manga_panel.py's page is in the project")
        mod = T.load_screen()
        app = T.make_app(mod, (800, 480), "en")
        app.st["theme"] = "anime"
        app.screen.engine.state.update(mode="lesson", song="ode_to_joy", pos=1, total=5, midi=[64, 65], expect=0,
                                       show=True, device_labels="num")
        app.view = "lesson"
        said = []
        real = app.cv.text
        app.cv.text = lambda s, *k, **kw: said.append(str(s)) or real(s, *k, **kw)
        app.draw()
        self.assertIn(app.tr("lp.part", n=2, m=5), said)
        self.assertGreaterEqual(min(r[2] for r in app.key_rects), 250, "the keys below the panel")


class GirlDrawn(unittest.TestCase):
    """Pose 8 drawn frame by frame: blinks, looks and the happy reaction."""

    def test_the_drawings_are_in_the_project_on_room_08s_lines(self):
        for n in ("base", "relaxed", "wide", "down", "side", "half", "closed", "in", "peak", "s1", "s2", "s3",
                  "oops_in", "oops_peak", "proud_in", "proud", "think_in", "think"):
            self.assertTrue((DATA / f"g08_{n}.jpg").exists(), n)
            self.assertTrue((DATA / f"g08face_{n}.jpg").exists(), n)

    def test_blinks_vary_and_looks_change(self):
        A = anime_mod()
        g = A.Girl(seed=3)
        seen, blinks, t = [], [], 0.0
        while t < 120:
            seen.append(g.drawing(t))
            t += 1 / 30
        self.assertTrue({"base", "relaxed", "down", "half", "closed"} <= set(seen))
        self.assertFalse({"wide", "side"} & set(seen), "no wide eyes or look aside for no reason")
        # the length of each closed-eye run: never all the same
        run = 0
        for d in seen:
            if d == "closed":
                run += 1
            elif run:
                blinks.append(run)
                run = 0
        self.assertGreater(len(set(blinks)), 2, blinks)
        self.assertGreater(len(blinks), 15)

    def test_the_happy_reaction_plays_its_drawings_in_order(self):
        A = anime_mod()
        g = A.Girl(seed=1)
        secs = g.happy(10.0)
        frames = [g.drawing(10.0 + i / 240) for i in range(int(secs * 240))]
        order = [d for i, d in enumerate(frames) if i == 0 or frames[i - 1] != d]
        self.assertEqual(order[:12], ["in", "peak", "s1", "s2", "s3", "s2", "s3", "s2", "s3", "s1", "peak", "in"])
        self.assertLess(secs, 2.4, "long enough to be seen, not slow")
        self.assertIn("closed", order[10:], "a blink after the laugh")
        self.assertEqual(g.drawing(10.0 + secs + 0.5), "relaxed")

    def test_a_part_learned_is_joy_with_the_hands_together(self):
        A = anime_mod()
        g = A.Girl(seed=2)
        secs = g.surprise(5.0)
        frames = [g.drawing(5.0 + i / 240) for i in range(int(secs * 240))]
        order = [d for i, d in enumerate(frames) if i == 0 or frames[i - 1] != d]
        self.assertEqual(order[:3], ["proud_in", "proud", "proud_in"])

    def test_thinking_puts_a_finger_to_the_chin_and_holds(self):
        A = anime_mod()
        g = A.Girl(seed=4)
        secs = g.think(0.0, hold=2.0)
        frames = [g.drawing(i / 240) for i in range(int(secs * 240))]
        order = [d for i, d in enumerate(frames) if i == 0 or frames[i - 1] != d]
        self.assertEqual(order[:2], ["think_in", "think"])
        self.assertEqual(frames[-1], "think")

    @unittest.skipIf(T.pygame is None, "pygame is not installed")
    def test_a_right_round_plays_the_drawn_laugh_in_the_corner(self):
        mod = T.load_screen()
        app = T.make_app(mod, (800, 480), "en")
        app.st["theme"] = "anime"
        app.screen.pub = mock.Mock()
        st = {"mode": "wait", "midi": [60, 62, 64], "expect": 1, "total": 3, "pos": 0, "phase": "new", "live": []}
        app.screen.engine.state = dict(st)
        app.tick()
        app.screen.engine.state = dict(st, mode="ok")
        app.tick()
        self.assertEqual(app.anime.cutin["face"], "girl")
        app.draw()


class Engine(unittest.TestCase):
    def test_the_engine_tells_the_phase_and_the_count_in(self):
        src = (ROOT / "lessons" / "piano_game.py").read_text()
        for key in ('"phase": ""', '"band": False', '"counting": False'):
            self.assertIn(key, src)
        for phase in ('stage + "_breath"', 'stage + "_band"', 'phase="new"', 'phase="drill"',
                      '"fading" if end else "warmup"'):
            self.assertIn(phase, src)
        self.assertIn("counting=True", src)
        self.assertIn('phase="", band=False, counting=False', src, "cleared when the lesson ends")


class Daemon(unittest.TestCase):
    def test_fx_is_heard_and_never_sent_on(self):
        src = (ROOT / "device" / "armonico_pi.py").read_text()
        body = src[src.index("    def fx_note(self"):src.index("    def listen_touch")]
        self.assertIn("self.sampler.note_on(FX_CH", body)
        self.assertNotIn("self.port.send", body)
        self.assertNotIn("to_keyboard", body)
        self.assertNotIn("self.pub(", body)


if __name__ == "__main__":
    unittest.main()


class Smoother(unittest.TestCase):
    """15.11.2026: drawings blend into each other, blinks are timed as people blink, the room is relit for the hour."""

    def test_a_change_of_drawing_is_blended_not_cut(self):
        A = anime_mod()
        g = A.Girl(seed=3)
        g.oops(10.0)
        g.frame(10.05)                                        # the in-between is on the screen
        p0, p1, a = g.frame(10.0 + 3 / 24 + 0.01)              # just after the in-between gave way to the peak
        self.assertEqual((p0, p1), ("oops_in", "oops_peak"))
        self.assertLess(a, 1.0)
        self.assertEqual(g.frame(10.0 + 3 / 24 + 0.3)[2], 1.0)

    def test_blinks_come_at_uneven_human_times(self):
        A = anime_mod()
        g = A.Girl(seed=5)
        gaps = [g.gap() for _ in range(300)]
        self.assertTrue(all(1.2 <= x <= 9.0 for x in gaps))
        self.assertGreater(len({round(x, 2) for x in gaps}), 200)
        self.assertTrue(2.5 < sorted(gaps)[150] < 4.5)          # about every 3 to 4 seconds

    def test_the_room_and_her_face_are_relit_in_the_evening_and_at_night(self):
        try:
            import numpy  # noqa: F401
            import pygame
        except ImportError:
            self.skipTest("numpy and pygame")
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.display.set_mode((800, 480))
        A = anime_mod()
        self.assertTrue((DATA / "relight.npz").exists(), "tools/relight_fields.py")
        pics = A.Pics(pygame)
        day = pics.cover("room_08", 400, 225)
        night = pics.cover("room_08", 400, 225, tint="night")
        dark = lambda s: sum(sum(s.get_at((x, y))[:3]) for x in range(0, 400, 20) for y in range(0, 225, 20))
        self.assertLess(dark(night), 0.8 * dark(day))
        face = pics.sized_layer("g08face_base", 192, 120, "night", A.FACE_BOX["g08face"])
        self.assertLess(dark_face := sum(sum(face.get_at((x, y))[:3]) for x in range(0, 192, 12) for y in range(0, 120, 12)),
                        sum(sum(pics.sized_layer("g08face_base", 192, 120).get_at((x, y))[:3])
                            for x in range(0, 192, 12) for y in range(0, 120, 12)))
        self.assertGreater(dark_face, 0)
