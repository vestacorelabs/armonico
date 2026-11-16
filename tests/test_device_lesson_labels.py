"""Regression tests for lesson key labels and help stages on the Pi screen."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_screen():
    spec = importlib.util.spec_from_file_location("armonico_screen_labels_test", ROOT / "device" / "screen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeRect:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.centerx = x + width // 2
        self.centery = y + height // 2
        self.bottom = y + height


class FakeDraw:
    def rect(self, *args, **kwargs):
        pass

    def circle(self, *args, **kwargs):
        pass


class FakePygame:
    Rect = FakeRect
    draw = FakeDraw()


class DeviceLessonLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def make_screen(self):
        screen = object.__new__(self.mod.Screen)
        screen.pg = FakePygame()
        screen.surf = object()
        screen.flash = {}
        screen.text = mock.Mock()
        return screen

    def test_numbered_round_shows_profile_labels(self):
        stage = self.mod.lesson_display_stage(True)
        self.assertEqual(stage, "numbers")
        self.assertTrue(self.mod.lesson_labels_visible(stage, "num"))
        self.assertTrue(self.mod.lesson_labels_visible(stage, "abc"))
        self.assertTrue(self.mod.lesson_labels_visible(stage, "do"))

    def test_keys_lit_round_hides_all_labels_and_colours(self):
        stage = self.mod.lesson_display_stage("keys")
        self.assertEqual(stage, "keys")
        for label_mode in ("num", "abc", "do", "pc", "none"):
            self.assertFalse(self.mod.lesson_labels_visible(stage, label_mode))

    def test_memory_round_hides_target_hints(self):
        stage = self.mod.lesson_display_stage(False)
        self.assertEqual(stage, "memory")
        self.assertFalse(self.mod.lesson_labels_visible(stage, "num"))

    def test_none_label_mode_never_draws_key_stickers(self):
        self.assertFalse(self.mod.lesson_labels_visible("numbers", "none"))

    def test_computer_key_labels_are_not_shown_on_device(self):
        self.assertFalse(self.mod.lesson_labels_visible("numbers", "pc"))
        screen = self.make_screen()
        computer_labels = ["Z", "S", "X", "D", "C", "V", "G", "B", "H", "N", "J", "M"]
        self.mod.Screen.keyboard(screen, 196, 0, "", key_labels=computer_labels,
                                 label_mode="pc", colors_mode="notes", stage="numbers")
        drawn_text = [call.args[0] for call in screen.text.call_args_list]
        self.assertFalse(set(computer_labels).intersection(drawn_text))

    def test_keyboard_uses_profile_labels_on_every_white_key(self):
        screen = self.make_screen()
        labels = ["15", "15#", "16", "16#", "17", "18", "18#", "19", "19#", "20", "20#", "21"]
        colours = [[217, 101, 107]] * 12
        self.mod.Screen.keyboard(screen, 196, 0, "3", key_labels=labels, key_colours=colours,
                                 label_mode="num", colors_mode="notes", stage="numbers")
        drawn_text = [call.args[0] for call in screen.text.call_args_list]
        for expected in ("15", "16", "17", "18", "19", "20", "21"):
            self.assertIn(expected, drawn_text)
        self.assertNotIn("C", drawn_text)
        self.assertNotIn("D", drawn_text)

    def test_keys_lit_and_memory_rounds_do_not_draw_any_key_labels(self):
        labels = ["15", "15#", "16", "16#", "17", "18", "18#", "19", "19#", "20", "20#", "21"]
        colours = [[217, 101, 107]] * 12
        for stage, next_pc in (("keys", 0), ("memory", None)):
            screen = self.make_screen()
            self.mod.Screen.keyboard(screen, 196, next_pc, "", key_labels=labels, key_colours=colours,
                                     label_mode="num", colors_mode="notes", stage=stage)
            self.assertEqual(screen.text.call_count, 0)


if __name__ == "__main__":
    unittest.main()
