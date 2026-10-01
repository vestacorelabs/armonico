"""The on-screen keyboard has Hebrew letters in every place that takes typing, and a key to change
between them and the English ones. A name opens in Hebrew on a Hebrew screen; a secret or a technical field in English.

A USB keyboard types on a screen that gets key presses but no TEXTINPUT (straight on KMS).
"""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:                      # no pygame
    pygame = None


def hit_at(app, x, y):
    for hx, hy, hw, hh, fn in reversed(app.hits):
        if hx <= x <= hx + hw and hy <= y <= hy + hh:
            return fn
    return None


@unittest.skipIf(pygame is None, "no pygame")
class HebrewKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_the_key_changes_the_letters_and_back(self):
        app = make_app(self.mod, lang="en")
        app.edit_field("x", lambda: "", lambda v: None)
        self.assertEqual(app.edit["kb"], "en")
        app.draw()
        fn = hit_at(app, 680, 142 + 4 * 43 + 10)
        self.assertIsNotNone(fn, "the key sits beside the backspace")
        fn()
        self.assertEqual(app.edit["kb"], "he")
        app.draw()
        first = hit_at(app, 400 + 1, 142 + 43 + 10)       # a letter of the second row
        self.assertIsNotNone(first)
        first()
        self.assertTrue(any("א" <= c <= "ת" for c in app.edit["val"]), app.edit["val"])
        hit_at(app, 680, 142 + 4 * 43 + 10)()
        self.assertEqual(app.edit["kb"], "en")

    def test_hebrew_has_all_22_letters_and_the_final_forms(self):
        rows = "".join(self.mod_rows())
        for ch in "אבגדהוזחטיכלמנסעפצקרשתךםןףץ":
            self.assertIn(ch, rows)

    def mod_rows(self):
        import ui.views_settings as vs
        return vs.HEROWS

    def test_a_name_opens_in_hebrew_on_a_hebrew_screen_and_a_secret_in_english(self):
        he = make_app(self.mod, lang="he")
        he.open_page("profiles")
        he.settings_cells()["profiles"][0][3]()               # add a profile: its name
        self.assertEqual(he.edit["kb"], "he")
        en = make_app(self.mod, lang="en")
        en.open_page("profiles")
        en.settings_cells()["profiles"][0][3]()
        self.assertEqual(en.edit["kb"], "en")
        he.edit_field("PIN", lambda: "", lambda v: None, secret=True, keep=True)
        self.assertEqual(he.edit["kb"], "en")

    def test_shift_does_nothing_in_hebrew_and_the_symbols_page_leaves_it(self):
        app = make_app(self.mod, lang="he")
        app.edit_field("x", lambda: "", lambda v: None, kb="he")
        app.draw()
        hit_at(app, 100, 142 + 4 * 43 + 10)()
        self.assertFalse(app.caps)
        hit_at(app, 220, 142 + 4 * 43 + 10)()                  # !#$
        self.assertTrue(app.sym)
        app.draw()
        hit_at(app, 680, 142 + 4 * 43 + 10)()                  # the language key: letters again, not symbols
        self.assertFalse(app.sym)
        self.assertEqual(app.edit["kb"], "en")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(pygame is None, "no pygame")
class UsbKeyboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def key(self, app, k, mod=0, text=None):
        pg = pygame
        app.event(pg.event.Event(pg.KEYDOWN, key=k, mod=mod, unicode=""))
        if text is not None:
            app.event(pg.event.Event(pg.TEXTINPUT, text=text))
        app.flush_typed()

    def app(self):
        app = make_app(self.mod, lang="en")
        app.edit_field("x", lambda: "", lambda v: None)
        return app

    def test_letters_are_typed_without_textinput(self):
        app = self.app()
        self.key(app, pygame.K_a)
        self.key(app, pygame.K_b, mod=pygame.KMOD_LSHIFT)
        self.key(app, pygame.K_1)
        self.key(app, pygame.K_1, mod=pygame.KMOD_LSHIFT)
        self.key(app, pygame.K_SPACE)
        self.key(app, pygame.K_KP5)
        self.assertEqual(app.edit["val"], "aB1! 5")

    def test_no_double_letter_when_textinput_comes(self):
        app = self.app()
        self.key(app, pygame.K_a, text="a")
        self.assertEqual(app.edit["val"], "a")

    def test_control_keys_type_nothing(self):
        app = self.app()
        self.key(app, pygame.K_c, mod=pygame.KMOD_LCTRL)
        self.assertEqual(app.edit["val"], "")

    def test_left_shift_alt_changes_language_and_types_hebrew(self):
        pg = pygame
        app = self.app()
        self.assertEqual(app.edit["kb"], "en")
        app.event(pg.event.Event(pg.KEYDOWN, key=pg.K_LALT, mod=pg.KMOD_LSHIFT | pg.KMOD_LALT, unicode=""))
        self.assertEqual(app.edit["kb"], "he")
        self.key(app, pg.K_a, text="a")            # a TEXTINPUT with it is not typed twice
        self.key(app, pg.K_e)
        self.key(app, pg.K_SPACE)
        self.assertEqual(app.edit["val"], "שק ")
        app.event(pg.event.Event(pg.KEYDOWN, key=pg.K_LSHIFT, mod=pg.KMOD_LSHIFT | pg.KMOD_LALT, unicode=""))
        self.assertEqual(app.edit["kb"], "en")


if __name__ == "__main__":
    unittest.main()
