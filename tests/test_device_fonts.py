"""No squares (missing-glyph boxes) on the Armonico Pi screen: the one character -> font rule of
device/ui/canvas.py (Fonts.route), the static scan of every UI string, and the fonts check."""
import ast
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_device_ui as T                           # noqa: E402  (sets the dummy SDL drivers and DATA_DIR)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
from ui import canvas as C                           # noqa: E402

pygame = T.pygame
WOFF = [ROOT / "lessons" / "fonts" / n for n in ("heebo-hebrew.woff2", "heebo-latin.woff2", "heebo-latin-ext.woff2")]
DEJAVU = C.dejavu_path()


def built_fonts():
    """The TrueType Heebo files the device draws with, made here from the lesson screen's woff2 files
    the way armonico_pi.py prepare makes them on the device (one fixed weight each, Latin and Hebrew
    merged), so the routing is checked against the same fonts. Needs fontTools with brotli."""
    have = T.font_dir()
    if (have / "Heebo-Medium.ttf").exists() and (have / "Heebo-Hebrew-Medium.ttf").exists():
        return have
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    from fontTools.merge import Merger
    out = Path(tempfile.mkdtemp(prefix="armonico-fonts-"))
    for weight, label in ((400, "Regular"), (800, "ExtraBold"), (500, "Medium"), (700, "Bold")):
        parts = []
        for f in ("heebo-latin.woff2", "heebo-hebrew.woff2"):
            font = instantiateVariableFont(TTFont(ROOT / "lessons" / "fonts" / f), {"wght": weight})
            font.flavor = None
            part = out / f".{f}.{weight}.ttf"
            font.save(part)
            parts.append(str(part))
        if weight in (500, 700):
            shutil.copyfile(parts[1], out / f"Heebo-Hebrew-{label}.ttf")
        try:
            Merger().merge(parts).save(out / f"Heebo-{label}.ttf")
        except Exception:
            Path(parts[0]).replace(out / f"Heebo-{label}.ttf")
        for x in parts:
            Path(x).unlink(missing_ok=True)
    return out


FONT_ERROR = ""
try:
    FONT_DIR = built_fonts()
except Exception as e:                 # the test that needs them says why, instead of failing on a glyph
    FONT_DIR, FONT_ERROR = T.font_dir(), f"{type(e).__name__}: {e}"


def cmap(path):
    from fontTools.ttLib import TTFont
    return set(TTFont(str(path)).getBestCmap())


def heebo_chars():
    out = set()
    for p in WOFF:
        out |= cmap(p)
    return out


def ui_strings():
    """Every string constant of the texts files and the drawing code (docstrings left out)."""
    files = [ROOT / "device" / "ui" / n for n in sorted(os.listdir(ROOT / "device" / "ui")) if n.endswith(".py")]
    files.append(ROOT / "device" / "screen.py")
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        doc = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
               and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc:
                yield f.name, n.value


def fonts(font_dir=None):
    pygame.font.init()
    return C.Fonts(pygame, font_dir or FONT_DIR)


def canvas(font_dir=None):
    pygame.display.set_mode((800, 480))
    return C.Canvas(pygame, pygame.display.get_surface(), fonts(font_dir))


def old_runs(s):
    """The routing before the rule: SYMBOLS, else Hebrew, else Latin."""
    old = set("○✓✔✕▲▼☰♪▶◀→↑↓■⚠⬆⬑●◐◓◑◒·…😎🥱📁⇧⌫▸›✔")
    out, cur, kind = [], "", None
    for ch in s:
        k = "sym" if ch in old else "heb" if C.HEB.match(ch) else "lat"
        if k != kind and cur:
            out.append((cur, kind))
            cur = ""
        cur, kind = cur + ch, k
    return out + ([(cur, kind)] if cur else [])


@unittest.skipUnless(pygame is not None and importlib.util.find_spec("fontTools") is not None, "needs pygame and fontTools")
@unittest.skipUnless(DEJAVU and all(p.exists() for p in WOFF), "needs DejaVu and the Heebo woff2 files")
class Static(unittest.TestCase):
    def test_every_ui_string_is_drawable(self):
        have = heebo_chars() | cmap(DEJAVU) | set(C.SYMBOLS) - set()
        # drawn from Telegram texts and logs only, never on the device screen
        elsewhere = set("⏰")
        bad = {}
        for name, s in ui_strings():
            for ch in s:
                if ch.isprintable() and ord(ch) not in have and ch not in elsewhere and ch not in "\n\t":
                    bad.setdefault(ch, set()).add(name)
        self.assertEqual({}, {f"U+{ord(c):04X} {c}": sorted(v) for c, v in bad.items()})

    def test_symbols_are_all_in_dejavu(self):
        dv = cmap(DEJAVU)
        self.assertEqual([], sorted(c for c in C.SYMBOLS if ord(c) not in dv))
        for c in "◂▸‖":
            self.assertIn(c, C.SYMBOLS)

    def test_ui_strings_that_were_fine_render_the_same(self):
        if FONT_ERROR:
            self.fail(f"The TrueType Heebo files could not be made from lessons/fonts ({FONT_ERROR}). "
                      "They need fontTools and brotli: pip install fonttools brotli")
        cv = canvas()
        heebo = heebo_chars()
        ok_old = heebo | set(map(ord, "○✓✔✕▲▼☰♪▶◀→↑↓■⚠⬆⬑●◐◓◑◒·…⇧⌫▸›"))
        seen, n = set(), 0
        for _, s in ui_strings():
            if not s or s in seen or len(s) > 60 or not all(ord(c) in ok_old or c == " " for c in s):
                continue
            seen.add(s)
            self.assertEqual([(t, k) for t, k in cv._runs(s)], old_runs(s), s)
            n += 1
        self.assertGreater(n, 500)


@unittest.skipIf(pygame is None, "pygame is not installed")
@unittest.skipUnless(DEJAVU, "needs DejaVu")
class Routing(unittest.TestCase):
    def setUp(self):
        self.cv = canvas()

    def kinds(self, s):
        return [(t, k) for t, k in self.cv._runs(s)]

    def test_small_arrows_use_the_symbol_font(self):
        self.assertEqual(self.kinds("◂ 80% ▸"), [("◂", "sym"), (" 80% ", "lat"), ("▸", "sym")])

    def test_emoji_dejavu_has_are_drawn_as_before(self):
        self.assertEqual(self.kinds("Sony 😎 XM4"), [("Sony ", "lat"), ("😎", "sym"), (" XM4", "lat")])

    def test_emoji_no_font_has_is_a_question_mark_and_joiners_are_dropped(self):
        self.assertEqual(self.kinds("A🥱B"), [("A?B", "lat")])
        self.assertEqual(self.kinds("A\u200d\ufe0fB"), [("AB", "lat")])

    def test_without_dejavu_an_emoji_is_a_question_mark(self):
        C._warned.clear()
        with mock.patch.object(C, "DEJAVU_DIR", "/nonexistent"), mock.patch("sys.stderr"):
            self.assertEqual(fonts().route("😎"), ("lat", "?"))

    def test_cjk_becomes_a_question_mark(self):
        self.assertEqual(self.kinds("耳机A"), [("??A", "lat")])

    def test_cyrillic_and_greek_use_dejavu(self):
        self.assertEqual(self.kinds("Маша"), [("Маша", "sym")])
        self.assertEqual(self.kinds("Ωx"), [("Ω", "sym"), ("x", "lat")])

    def test_hebrew_and_latin_keep_their_fonts(self):
        k = [x[1] for x in self.cv._runs("שלום Bose")]
        self.assertIn("heb", k)
        self.assertIn("lat", k)

    def test_the_image_has_no_missing_box(self):
        for s in ("Sony 😎", "耳机", "Колонка", "x ‖ y"):
            self.assertGreater(self.cv.render(s, 16, (255, 255, 255)).get_width(), 0)

    def test_without_dejavu_one_line_and_no_crash(self):
        C._warned.clear()
        with mock.patch.object(C, "DEJAVU_DIR", "/nonexistent"), mock.patch("sys.stderr") as err:
            f = fonts()
            self.assertEqual(f.route("◂")[1] if isinstance(f.route("◂"), tuple) else f.route("◂"), "lat" if not isinstance(f.route("◂"), tuple) else "?")
        text = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertEqual(text.count("DejaVuSans-Bold.ttf not found"), 1)

    def test_missing_heebo_is_logged_once(self):
        C._warned.clear()
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stderr") as err:
            fonts(d)
            fonts(d)
        text = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertEqual(text.count("Heebo-Hebrew-Medium.ttf"), 1)


class FontsCheck(unittest.TestCase):
    def test_warning_when_fonts_are_missing(self):
        import fullcheck as F
        with tempfile.TemporaryDirectory() as d:
            items = F.a_fonts({"DATA_DIR": d}, {})
            self.assertTrue(items and all(i.level == F.NOTE for i in items[:1]) or DEJAVU is None)
            self.assertEqual(items[0].level, F.NOTE)
            self.assertTrue(items[0].todo[0] and items[0].todo[1])
            fd = Path(d) / "device" / "fonts"
            fd.mkdir(parents=True)
            for n in ("Heebo-Hebrew-Medium.ttf", "Heebo-Hebrew-Bold.ttf", "Heebo-Medium.ttf", "Heebo-Bold.ttf"):
                (fd / n).write_bytes(b"x")
            ok = F.a_fonts({"DATA_DIR": d}, {})
            self.assertEqual([i.level for i in ok], [F.OK] if DEJAVU else [F.NOTE])
            with mock.patch.object(F.Path, "exists", return_value=False):
                self.assertEqual(F.a_fonts({"DATA_DIR": d}, {})[-1].level, F.NOTE)


if __name__ == "__main__":
    unittest.main()
