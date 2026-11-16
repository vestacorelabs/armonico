"""A profile deleted from the device leaves a backup first (the lesson screen's own format,
so its Restore makes the profile again), and a failed backup deletes nothing.

The profile card is a list to choose from, whichever profile is at the piano; a PIN is asked first.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))
try:
    import piano_game as game          # noqa: E402
except ImportError:                    # mido missing
    game = None
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:                      # no pygame
    pygame = None
sys.path.insert(0, str(ROOT / "device"))
try:
    from test_device_ui import load_screen, make_app, pygame
except Exception:
    pygame = None


class Fake:
    """Just enough of UIHandler for profiles_action."""
    client_address = ("127.0.0.1", 0)

    def __init__(self):
        self.code, self.body = None, None

    def json(self, obj, code=200):
        self.code, self.body = code, obj

    def answer(self, code):
        self.code = code


@unittest.skipIf(game is None, "no mido")
class DeleteKeepsACopy(unittest.TestCase):
    def piano(self, d):
        (d / "profiles" / "pabcdef01").mkdir(parents=True)
        (d / "songs").mkdir()
        (d / "lessons.json").write_text('{"main": 1}')
        (d / "profiles" / "pabcdef01" / "lessons.json").write_text('{"dan": 2}')
        game.save_profiles({"active": "main", "list": [{"id": "main", "name": ""}, {"id": "pabcdef01", "name": "דן", "pin": None}]})

    def patched(self, d):
        return mock.patch.multiple(game, DATA_DIR=d, PROFILES_FILE=d / "profiles.json", MIDI_DIR=d / "songs",
                                   DELETED_DIR=d / "deleted-profiles", RESTORE_FILE=d / ".restore.zip")

    def delete(self, pid):
        h = Fake()
        with mock.patch.object(game, "lesson_busy", lambda: False):
            game.UIHandler.profiles_action(h, "/profiles/delete", {"id": pid, "pin": ""})
        return h

    def test_a_deleted_profile_leaves_a_backup_that_restores_it(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.patched(d):
                self.piano(d)
                h = self.delete("pabcdef01")
                self.assertEqual(h.code, 200)
                self.assertFalse((d / "profiles" / "pabcdef01").exists())
                kept = list((d / "deleted-profiles").glob("*.zip"))
                self.assertEqual(len(kept), 1)
                self.assertIn("דן", kept[0].name)
                self.assertEqual(oct(kept[0].stat().st_mode & 0o777), "0o600")
                raw = kept[0].read_bytes()
                info = game.restore_inspect(raw)
                self.assertFalse(info["profiles"][0]["exists"])
                game.restore_into("pabcdef01", True)
                self.assertEqual(json.loads((d / "profiles" / "pabcdef01" / "lessons.json").read_text()), {"dan": 2})

    def test_when_the_backup_fails_nothing_is_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.patched(d):
                self.piano(d)
                with mock.patch.object(game, "write_atomic_bytes", side_effect=OSError("disk full")):
                    h = self.delete("pabcdef01")
                self.assertEqual(h.code, 500)
                self.assertEqual(h.body["why"], "backup")
                self.assertTrue((d / "profiles" / "pabcdef01" / "lessons.json").exists())
                self.assertEqual([p["id"] for p in game.load_profiles()["list"]], ["main", "pabcdef01"])

    def test_the_main_and_the_active_profile_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.patched(d):
                self.piano(d)
                self.assertEqual(self.delete("main").code, 400)
                self.assertFalse((d / "deleted-profiles").exists())


@unittest.skipIf(pygame is None, "no pygame")
class DevicePage(unittest.TestCase):
    """Settings › Playing › Profiles: Add, then a card for each profile that opens its own page. The one at the piano
    has Rename, PIN and a Delete that waits; another has Switch and Delete; the main one is never deleted."""
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, lang="he"):
        app = make_app(self.mod, lang=lang)
        app.screen.engine.overview["profiles"] = {"active": "main", "list": [
            {"id": "main", "name": "", "locked": False}, {"id": "p00000001", "name": "דנה", "locked": True},
            {"id": "p00000002", "name": "יוסי", "locked": False}]}
        return app

    def page(self, app, pid):
        app.prof_id = pid
        app.open_page("prof")
        return app.settings_cells()["prof"]

    def test_the_page_lists_every_profile_and_each_opens_its_own_page(self):
        app = self.app()
        cells = app.settings_cells()["profiles"]
        self.assertEqual(len(cells), 4, "Add, then the 3 profiles")
        self.assertEqual(cells[0][0], app.tr("pr.add"))
        self.assertEqual([c[1] for c in cells[1:]], [app.tr("mainProfile"), "דנה", "יוסי"])
        self.assertEqual(cells[1][0], app.tr("pr.active"))
        app.open_page("profiles")
        cells[2][3]()
        self.assertEqual((app.page, app.prof_id), ("prof", "p00000001"))
        app.draw()
        self.assertEqual(app.screen_path("דנה").split(" • ")[-1], app.tr("g.profiles"))
        mine = self.page(app, "main")
        self.assertEqual([c[0] for c in mine], [app.tr("pr.ren"), app.tr("pr.pinCh"), app.tr("pr.delete")])
        self.assertIsNone(mine[2][3], "the main profile is never deleted")
        other = self.page(app, "p00000002")
        self.assertEqual([c[0] for c in other], [app.tr("pr.switchTo"), app.tr("pr.delete")])
        self.assertIsNotNone(other[1][3])
        app.go("settings")
        app.draw()
        app.press("menu")                               # back: on the profile's own card
        self.assertEqual((app.page, app.sel), ("profiles", 3))

    def test_the_main_profile_is_never_deleted(self):
        app = self.app()
        app.screen.engine.overview["profiles"]["active"] = "p00000002"
        cells = self.page(app, "main")
        self.assertEqual(cells[0][0], app.tr("pr.switchTo"))
        self.assertIsNone(cells[-1][3])
        self.assertEqual(cells[-1][2], app.tr("pr.mainD"))

    def test_a_profile_with_a_pin_asks_for_it_and_the_answer_is_shown(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        self.page(app, "p00000001")[1][3]()
        self.assertIsNotNone(app.confirm)
        app.answer(True)
        self.assertEqual(app.view, "edit", "the PIN")
        app.edit["val"] = "1234"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/delete", {"id": "p00000001", "pin": "1234"}))
        posted[0][2](False, 403)
        self.assertIn("4", app.screen.toast[0])
        posted[0][2](True, None)
        self.assertEqual([p["id"] for p in app.profiles()["list"]], ["main", "p00000002"])
        self.assertEqual(app.page, "profiles", "the deleted profile's page closes")

    def test_a_profile_with_no_pin_goes_straight_after_the_question(self):
        app = self.app("en")
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body))
        self.page(app, "p00000002")[1][3]()
        app.answer(True)
        self.assertEqual(posted, [("/profiles/delete", {"id": "p00000002", "pin": ""})])
        for page in ("play", "profiles", "prof"):
            app.page, app.sel = page, 0
            app.go("settings")
            app.draw()

    def test_adding_a_profile_takes_a_name_then_a_pin_or_none(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        app.open_page("profiles")
        add = app.settings_cells()["profiles"][0][3]
        add()
        self.assertEqual(app.view, "edit")
        app.edit["val"] = "  נועה "
        app.edit_done()
        self.assertEqual(app.view, "edit", "the PIN next")
        app.edit["val"] = "12"
        app.edit_done()
        self.assertEqual(app.view, "edit", "too short: asked again")
        self.assertEqual(posted, [])
        app.edit["val"] = "1234"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/add", {"name": "נועה", "pin": "1234"}))
        posted[0][2](True, None)
        self.assertIn("נועה", app.screen.toast[0])
        add()
        app.edit["val"] = "עומר"
        app.edit_done()
        app.edit["val"] = ""
        app.edit_done()
        self.assertEqual(posted[1][:2], ("/profiles/add", {"name": "עומר", "pin": ""}), "empty: no PIN")
        add()
        app.edit["val"] = "דנה"
        app.edit_done()
        self.assertEqual(len(posted), 2, "a name that is taken is not sent")

    def test_renaming_the_profile_at_the_piano(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        ren = self.page(app, "main")[0]
        self.assertEqual(ren[0], app.tr("pr.ren"))
        ren[3]()
        self.assertEqual(app.edit["val"], "", "the main profile has no name of its own")
        app.edit["val"] = "אבא"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/edit", {"name": "אבא", "pin": ""}))
        posted[0][2](True, None)
        self.assertEqual(app.profile_name(), "אבא")
        self.page(app, "main")[0][3]()
        app.edit["val"] = "דנה"
        app.edit_done()
        self.assertEqual(len(posted), 1, "a name that is taken is not sent")
        self.page(app, "main")[0][3]()
        app.edit["val"] = ""
        app.edit_done()
        self.assertEqual(posted[1][:2], ("/profiles/edit", {"name": "", "pin": ""}), "the main one may go back to its usual name")

    def test_a_locked_profile_asks_its_pin_to_rename_and_to_change_it(self):
        app = self.app()
        app.screen.engine.overview["profiles"]["active"] = "p00000001"
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        self.page(app, "p00000001")[0][3]()
        app.edit["val"] = "דנית"
        app.edit_done()
        self.assertEqual(app.view, "edit", "its PIN")
        app.edit["val"] = "1234"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/edit", {"name": "דנית", "pin": "1234"}))
        posted[0][2](False, 403)
        self.assertIn("4", app.screen.toast[0])
        self.page(app, "p00000001")[1][3]()
        app.edit["val"] = "1234"
        app.edit_done()
        self.assertEqual(app.view, "edit", "the new PIN")
        app.edit["val"] = "12"
        app.edit_done()
        self.assertEqual(app.view, "edit", "too short: asked again")
        self.assertEqual(len(posted), 1)
        app.edit["val"] = ""
        app.edit_done()
        self.assertEqual(posted[1][:2], ("/profiles/edit", {"pin": "1234", "new_pin": ""}), "empty: the PIN goes")
        posted[1][2](True, None)
        self.assertFalse(app.profiles()["list"][1]["locked"])

    def test_a_profile_with_no_pin_gets_one(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        self.page(app, "main")[1][3]()
        app.edit["val"] = "4321"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/edit", {"pin": "", "new_pin": "4321"}))
        posted[0][2](True, None)
        self.assertTrue(app.profiles()["list"][0]["locked"])

    def test_a_full_piano_cannot_add(self):
        app = self.app()
        app.screen.engine.overview["profiles"]["list"] += [{"id": f"p{i:08x}", "name": str(i), "locked": False} for i in range(9)]
        self.assertIsNone(app.settings_cells()["profiles"][0][3])

    def test_the_profile_at_the_piano_is_deleted_after_a_move_to_the_main_one(self):
        app = self.app()
        app.screen.engine.overview["profiles"]["active"] = "p00000002"
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result, timeout))
        cells = self.page(app, "p00000002")
        self.assertEqual(cells[-1][0], app.tr("pr.delete"))
        cells[-1][3]()
        app.answer(True)
        self.assertEqual(posted[0][:2], ("/profiles/switch", {"id": "main", "pin": ""}))
        posted[0][2](True, None)
        self.assertEqual(posted[1][:2], ("/profiles/delete", {"id": "p00000002", "pin": ""}))
        self.assertEqual(posted[1][3], 60, "the backup before a delete can take more than 3 seconds")
        posted[1][2](True, None)
        self.assertEqual([p["id"] for p in app.profiles()["list"]], ["main", "p00000001"])
        self.assertEqual(app.profiles()["active"], "main")

    def test_a_delete_that_does_not_go_through_says_why(self):
        app = self.app()
        for code, key in ((409, "pr.busy"), (500, "pr.noBackup"), ("connection", "pr.noEngine")):
            app.profile_fail(code)
            self.assertEqual(app.screen.toast[0], app.tr(key))

    def test_a_profile_deleted_elsewhere_leaves_a_page_that_says_so(self):
        app = self.app()
        cells = self.page(app, "p00000009")
        self.assertEqual(cells[0][0], app.tr("pr.gone"))
        app.draw()


@unittest.skipIf(pygame is None, "no pygame")
class Pick(unittest.TestCase):
    """Switching: from the profile's own page, its PIN first when it has one."""
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self):
        app = make_app(self.mod, lang="en")
        app.screen.engine.overview["profiles"] = {"active": "p1", "list": [
            {"id": "main", "name": "", "locked": False}, {"id": "p1", "name": "Dana", "locked": False},
            {"id": "p2", "name": "Noa", "locked": True}]}
        return app

    def switch(self, app, pid):
        app.prof_id = pid
        app.open_page("prof")
        return app.settings_cells()["prof"][0]

    def test_the_active_one_has_no_switch(self):
        app = self.app()
        self.assertEqual(self.switch(app, "p1")[0], app.tr("pr.ren"))
        self.assertEqual(self.switch(app, "p2")[0], app.tr("pr.switchTo"))

    def test_a_locked_profile_asks_its_pin_then_switches(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body, on_result))
        self.switch(app, "p2")[3]()
        self.assertEqual(app.view, "edit")
        app.edit["val"] = "1234"
        app.edit_done()
        self.assertEqual(posted[0][:2], ("/profiles/switch", {"id": "p2", "pin": "1234"}))
        posted[0][2](True, 200)
        self.assertEqual(app.profiles()["active"], "p2")

    def test_an_open_profile_switches_at_once(self):
        app = self.app()
        posted = []
        app.screen.engine.post = lambda path, body, on_result=None, timeout=3: posted.append((path, body))
        self.switch(app, "main")[3]()
        self.assertEqual(posted, [("/profiles/switch", {"id": "main", "pin": ""})])


if __name__ == "__main__":
    unittest.main()
