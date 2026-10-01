"""The touch status screen speaks English first and Hebrew on its own button. Nothing may be left
in one language only: every text of the page carries both, so the button changes all of it."""
import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "screen" / "piano-status.html").read_text(encoding="utf-8")
HEBREW = re.compile("[א-ת]")
BODY = PAGE[PAGE.index("<body>"):PAGE.index("<script>", PAGE.index("<body>"))]


class StatusPageLanguage(unittest.TestCase):
    def test_the_button_is_on_the_screen_and_english_comes_first(self):
        self.assertIn('id="bLang"', BODY)
        self.assertIn("let LANG='en';", PAGE)
        self.assertIn("localStorage.setItem('armonico_status_lang'", PAGE)

    def test_no_text_of_the_page_is_in_hebrew_only(self):
        """Every Hebrew text node of the page sits in an element that also carries its English."""
        bare = [m.group(1).strip() for m in re.finditer(r"<(?![^>]*data-en)[^>]*>([^<>]*[א-ת][^<>]*)<", BODY)
                if m.group(1).strip() not in ("עברית",)]          # the button names the other language
        self.assertEqual(bare, [])

    def test_every_pair_of_attributes_is_complete(self):
        pairs = re.findall(r'data-he="([^"]*)"\s+data-en="([^"]*)"', BODY)
        self.assertGreater(len(pairs), 20)
        for he, en in pairs:
            self.assertTrue(HEBREW.search(he), he)
            self.assertFalse(HEBREW.search(en), en)
            self.assertTrue(en.strip(), he)

    def test_every_translated_text_in_the_script_has_both_languages(self):
        script = PAGE[PAGE.rindex("<script>"):]
        for he, en in re.findall(r"\bL\('([^']*)','([^']*)'\)", script):
            self.assertTrue(HEBREW.search(he) or he == "", he)
            self.assertFalse(HEBREW.search(en), en)

    def test_no_hebrew_text_is_left_in_a_plain_string_of_the_script(self):
        script = PAGE[PAGE.rindex("<script>"):]
        code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)        # comments are for the author
        code = re.sub(r"\bL\('[^']*','[^']*'\)", "", code)
        left = [m for m in re.findall(r"'([^'\n]*[א-ת][^'\n]*)'", code) if m not in ("התקווה", "עברית") and "data-he=" not in m]   # the log placeholder carries both
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
