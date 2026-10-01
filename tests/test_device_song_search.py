"""Search online on the Armonico Pi's Songs screen (device/ui/views_songnet.py): the device asks the lesson
engine's own search and download (/api/search, /songs/fetch) over HTTP, never on the drawing thread, and
shows progress and every failure in words. The engine here is a fake that answers like the real one."""
import io
import threading
import time
import unittest
import urllib.error

from test_device_ui import ROOT, load_screen, make_app, pygame

RESULTS = [{"id": 0, "name": "Bella Ciao - Traditional", "source": "Mutopia Project", "license": "Public Domain"},
           {"id": 1, "name": "Bella ciao", "source": "Wikimedia Commons", "license": "CC BY-SA 3.0"},
           {"id": 2, "name": "Ode to joy", "source": "Wikimedia Commons", "license": "Public domain"}]


class FakeEngine:
    """The lesson engine's HTTP API as Engine._req sees it: (path, body, timeout) -> JSON, or HTTPError."""

    def __init__(self, results=RESULTS, search_error=None, fetch_code=200, fetch_ok=True, tokens=("t1", "t2", "t3")):
        self.results, self.search_error, self.fetch_code, self.fetch_ok = results, search_error, fetch_code, fetch_ok
        self.tokens, self.calls, self.gate = list(tokens), [], None

    def __call__(self, path, body=None, timeout=3):
        self.calls.append((path, body))
        if self.gate is not None:
            self.gate.wait(5)
        if path.startswith("/api/search"):
            if self.search_error == "down":
                raise urllib.error.URLError("Connection refused")
            if self.search_error == "net":
                return {"error": "No archive answered"}
            return {"search": self.tokens.pop(0), "results": self.results, "missing": [], "failed": []}
        if path == "/songs/fetch":
            code = self.fetch_code if not callable(self.fetch_code) else self.fetch_code(body)
            if code != 200:
                raise urllib.error.HTTPError(path, code, "error", {}, io.BytesIO(b'{"ok": false, "why": "x"}'))
            name = RESULTS[body["id"]]["name"]
            return {"name": name, "ok": True, "notes": 40, "parts": 6} if self.fetch_ok else \
                {"name": name, "ok": False, "why": "No melody was found in it"}
        if path == "/api/overview":
            return {"songs": [{"name": "Bella Ciao - Traditional", "total": 6, "learned": 0}]}
        raise AssertionError(path)

    def paths(self):
        return [p.split("?")[0] for p, _ in self.calls]


@unittest.skipIf(pygame is None, "pygame is not installed")
class SongSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def app(self, engine=None, lang="en", songs=True):
        app = make_app(self.mod, (800, 480), lang)
        if not songs:
            app.screen.engine.overview["songs"] = []
        app.screen.engine._req = engine or FakeEngine()
        app.sn_spawn = lambda fn: fn()            # in place: the threads are tested on their own below
        return app

    def press(self, app, name):
        app.draw()
        app.press(name)

    def test_search_lists_results_with_their_credit_and_downloads_with_ok(self):
        eng = FakeEngine()
        app = self.app(eng)
        app.go("songnet")
        app.sn_search("  bella   ciao ")
        sn = app.sn_state()
        self.assertEqual(eng.calls[0][0], "/api/search?q=bella%20ciao")
        self.assertEqual([r["name"] for r in sn["results"]], [r["name"] for r in RESULTS])
        self.assertEqual(sn["sel"], 1, "the first result is framed")
        app.draw()
        self.assertEqual(app.bar_items[3][1], "Download")
        self.assertEqual(app.bar_items[0][1], "Back")
        app.press("ok")
        self.assertIn(("/songs/fetch", {"id": 0, "search": "t1"}), eng.calls)
        self.assertEqual(sn["rows"][0], "ok")
        self.assertEqual(app.screen.toast[0], "Added to songs")
        # the list is asked again at once, so the new song is in Songs now
        self.assertEqual(app.screen.engine.overview["songs"][0]["name"], "Bella Ciao - Traditional")
        app.draw()
        self.assertEqual(app.bar_items[3][2], None, "a song already added is not downloaded twice")
        app.press("ok")
        self.assertEqual(eng.paths().count("/songs/fetch"), 1)
        app.press("menu")
        self.assertEqual(app.view, "songs")

    def test_down_and_up_move_through_the_field_and_the_results(self):
        app = self.app()
        app.go("songnet")
        app.sn_search("bella")
        sn = app.sn_state()
        self.press(app, "down")
        self.assertEqual(sn["sel"], 2)
        self.press(app, "up")
        self.press(app, "up")
        self.assertEqual(sn["sel"], 0, "▲ from the first result frames the search field")
        app.draw()
        self.assertEqual(app.bar_items[3][1], "Type")
        app.press("ok")
        self.assertEqual(app.view, "edit", "✓ on the field opens the keyboard")
        app.edit["val"] = "ode"
        app.edit_done()
        self.assertEqual((app.view, sn["query"]), ("songnet", "ode"))

    def test_a_long_list_scrolls_with_the_selection(self):
        many = [dict(id=i, name=f"Song {i}", source="Mutopia Project", license="Public Domain") for i in range(12)]
        app = self.app(FakeEngine(results=many))
        app.go("songnet")
        app.sn_search("song")
        for _ in range(7):
            self.press(app, "down")
        sn = app.sn_state()
        self.assertEqual(sn["sel"], 8)
        self.assertEqual(sn["first"], 3, "the framed result stays on the screen")

    def test_errors_are_said_in_words(self):
        cases = (("net", FakeEngine(search_error="net")), ("engine", FakeEngine(search_error="down")),
                 ("none", FakeEngine(results=[])))
        for want, eng in cases:
            for lang in ("en", "he"):
                with self.subTest(error=want, lang=lang):
                    app = self.app(eng, lang)
                    app.go("songnet")
                    app.sn_search("bela chao")
                    self.assertEqual(app.sn_state()["error"], want)
                    self.assertFalse(app.sn_state()["busy"])
                    app.draw()
        app = self.app()
        app.ip = ""                                  # no network on the device at all: the engine is not even asked
        app.go("songnet")
        app.sn_search("bella")
        self.assertEqual(app.sn_state()["error"], "net")
        self.assertEqual(app.screen.engine._req.calls, [])

    def test_a_failed_download_is_marked_and_said(self):
        eng = FakeEngine(fetch_code=502)
        app = self.app(eng, "he")
        app.go("songnet")
        app.sn_search("bella")
        app.sn_download(1)
        self.assertEqual(app.sn_state()["rows"][1], "failed")
        self.assertEqual(app.screen.toast[0], "ההורדה נכשלה. אפשר לנסות שוב בעוד רגע.")
        app.draw()
        self.assertEqual(app.bar_items[3][1], "הורדה", "a failed download can be tried again")

    def test_a_file_with_no_melody_is_said(self):
        app = self.app(FakeEngine(fetch_ok=False))
        app.go("songnet")
        app.sn_search("bella")
        app.sn_download(1)
        self.assertEqual(app.sn_state()["rows"][1], "nolesson")
        self.assertEqual(app.screen.toast[0], "The file has no melody to learn, so it was not kept")

    def test_an_expired_search_is_made_again_before_the_download(self):
        eng = FakeEngine(fetch_code=lambda body: 404 if body["search"] == "t1" else 200)
        app = self.app(eng)
        app.go("songnet")
        app.sn_search("bella")
        app.sn_download(1)
        self.assertEqual(eng.paths(), ["/api/search", "/songs/fetch", "/api/search", "/songs/fetch", "/api/overview"])
        self.assertEqual(eng.calls[3][1], {"id": 1, "search": "t2"})
        self.assertEqual(app.sn_state()["rows"][1], "ok")

    def test_the_network_never_holds_the_screen(self):
        eng = FakeEngine()
        eng.gate = threading.Event()
        app = self.app(eng)
        del app.sn_spawn                              # the real threads
        app.go("songnet")
        t0 = time.monotonic()
        app.sn_search("bella")
        app.draw()
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertTrue(app.sn_state()["busy"])
        eng.gate.set()
        for _ in range(100):
            if not app.sn_state()["busy"]:
                break
            time.sleep(0.02)
        self.assertEqual(len(app.sn_state()["results"]), 3)

    def test_an_older_search_that_answers_late_is_dropped(self):
        app = self.app()
        app.go("songnet")
        jobs = []
        app.sn_spawn = jobs.append
        app.sn_search("first")
        app.sn_search("second")
        jobs[1]()
        jobs[0]()
        self.assertEqual(app.sn_state()["query"], "second")
        self.assertEqual(app.sn_state()["token"], "t1", "only the newer search's answer is kept")

    def test_with_no_songs_search_online_comes_first(self):
        for lang in ("en", "he"):
            app = self.app(lang=lang, songs=False)
            app.go("songs")
            app.draw()
            self.assertEqual(app.bar_items[3][1], app.tr("sn.find"))
            app.press("ok")
            self.assertEqual(app.view, "edit", "nothing searched yet: straight to the keyboard")
            self.assertEqual(app.edit["back"], "songnet")
            self.assertEqual(app.edit["kb"], lang)
            app.edit_cancel()
            self.assertEqual(app.view, "songnet")

    def test_with_songs_up_from_the_first_song_frames_search_online(self):
        app = self.app()
        app.go("songs")
        self.press(app, "up")
        self.assertTrue(app.sn_entry)
        app.draw()
        self.assertEqual(app.bar_items[3][1], "Search")
        app.button("ok", True)                        # ✓ held plays a song, never from Search online
        app.screen.engine._req.calls.clear()
        self.press(app, "down")
        self.assertFalse(app.sn_entry)
        self.assertEqual(app.screen.sel_song, 0)
        self.press(app, "down")
        self.press(app, "down")                       # past the last song: Songs from USB, then Search online again
        self.assertTrue(app.imp_entry)
        app.draw()
        self.assertEqual(app.bar_items[3][1], app.tr("c.open"))
        self.press(app, "down")
        self.assertTrue(app.sn_entry)
        app.draw()
        app.press("ok")
        self.assertIn(app.view, ("edit", "songnet"))

    def test_back_lands_on_the_song_just_added(self):
        app = self.app()
        app.go("songnet")
        app.sn_search("bella")
        app.sn_download(0)
        app.sn_back()
        self.assertEqual(app.view, "songs")
        self.assertEqual(app.screen.songs()[app.screen.sel_song]["name"], "Bella Ciao - Traditional")

    def test_every_state_draws_in_both_languages(self):
        for lang in ("en", "he"):
            app = self.app(lang=lang)
            app.go("songnet")
            app.draw()
            app.sn_state().update(query="bella", busy=True)
            app.draw()
            app.sn_state()["busy"] = False
            app.sn_search("bella")
            app.sn_state()["rows"].update({0: "busy", 1: "failed", 2: "nolesson"})
            app.draw()
            self.assertTrue(app.bar_items)

    def test_the_engine_endpoints_used_here_are_the_ones_the_lesson_screen_uses(self):
        src = (ROOT / "lessons" / "piano_game.py").read_text()
        self.assertIn('elif path == "/api/search":', src)
        self.assertIn('elif path == "/songs/fetch":', src)
        self.assertIn('FOUND[str(data.get("search", ""))]', src)
        self.assertIn('hits[int(data.get("id", -1))]', src)
        js = src[src.index("/api/search?q="):]
        self.assertIn("post('/songs/fetch', { id: h.id, search: lastSearch })", js)


if __name__ == "__main__":
    unittest.main()
