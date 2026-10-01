"""Who may reach the lesson screen, and what the editing tools are allowed to save.

Everything here talks to a real server on a real socket, because the question is not
whether a function returns the right value but whether a request that should be refused
actually is. A check that reads the source and looks for the right words would pass on a
door that is painted on.

The server is started on a free port with its data in a folder made for the test, so
nothing here touches an installed Armonico.

Run from the project folder:

    python3 -m unittest discover -s tests
"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The engine reads these when it is imported, so they are set before the import
TMP = Path(tempfile.mkdtemp(prefix="armonico-test-"))
os.environ.setdefault("DATA_DIR", str(TMP / "data"))
os.environ.setdefault("SONGS_DIR", str(TMP / "songs"))
os.environ.setdefault("RUN_DIR", str(TMP / "run"))
os.environ["SCREEN_CODE"] = "on"
os.environ["MQTT_ADMIN_SECRET"] = "secret-for-the-test"

import sys                                      # noqa: E402
sys.path.insert(0, str(ROOT / "lessons"))
import piano_game as game                       # noqa: E402

from http.server import ThreadingHTTPServer     # noqa: E402


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Screen(unittest.TestCase):
    """A running lesson screen, asked the way a browser would ask."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), game.UIHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.code = game.screen_code()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        game.set_code_on(True)
        game.FAILS.clear()

    # --- asking ---
    def ask(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, data, headers)
        answer = conn.getresponse()
        return answer.status, answer.getheader("Set-Cookie"), answer.read()

    def unlock(self):
        status, set_cookie, _ = self.ask("POST", "/code", {"code": self.code})
        self.assertEqual(status, 200)
        token = set_cookie.split("armonico_pass=")[1].split(";")[0]
        return f"armonico_pass={token}"

    # --- reads ---
    def test_reads_are_refused_without_a_pass(self):
        for path in ("/state", "/api/overview", "/api/backup", "/api/update",
                     "/api/code", "/api/search?q=x"):
            with self.subTest(path=path):
                self.assertEqual(self.ask("GET", path)[0], 401)

    def test_the_backup_is_refused_without_a_pass(self):
        """It carries the profiles, and the profiles carry the hashed PINs."""
        self.assertEqual(self.ask("GET", "/api/backup")[0], 401)

    def test_the_page_carries_its_version_before_any_request(self):
        """The footer is right on the first visit: the version is in the page, not fetched
        after the code, which a screen that has not entered it yet cannot do."""
        status, _, raw = self.ask("GET", "/")
        self.assertEqual(status, 200)
        body = raw.decode()
        shown = game.shown_version()
        self.assertIn(f"let VER_TEXT = '{shown}'", body)
        self.assertNotIn("__VERSION__", body)

    def test_a_trailing_zero_patch_is_not_shown(self):
        self.assertEqual(game.shown_version("1.0.0"), "1.0")
        self.assertEqual(game.shown_version("1.0.1"), "1.0.1")
        self.assertEqual(game.shown_version("1.2"), "1.2")

    def test_a_screen_starts_in_english_until_it_chooses(self):
        """The piano's saved language may be Hebrew (it was, after the old setup screen), but a
        screen that never picked a language stays in English instead of flipping after the code."""
        page = game.render_page()
        self.assertIn("let LANG = store('pianoLang') === 'he' ? 'he' : 'en';", page)
        self.assertIn("o.settings.lang !== LANG && store('pianoLang')", page)

    def test_the_page_itself_is_served(self):
        """A screen that was never let in still has to draw enough to ask for the code."""
        status, _, body = self.ask("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!DOCTYPE html>", body)

    def test_the_broker_login_is_refused_without_a_pass_and_names_the_broker_with_one(self):
        self.assertEqual(self.ask("GET", "/api/status-login")[0], 401)
        status, _, body = self.ask("GET", "/api/status-login", cookie=self.unlock())
        self.assertEqual(status, 200)
        got = json.loads(body)
        self.assertEqual(set(got), {"user", "pass", "host"})

    def test_reads_answer_with_a_pass(self):
        cookie = self.unlock()
        for path in ("/state", "/api/overview", "/api/code"):
            with self.subTest(path=path):
                self.assertEqual(self.ask("GET", path, cookie=cookie)[0], 200)

    # --- actions ---
    def test_actions_are_refused_without_a_pass(self):
        for path in ("/lessonstop", "/replay", "/update", "/settings", "/songs/delete"):
            with self.subTest(path=path):
                self.assertEqual(self.ask("POST", path, {})[0], 401)

    def test_the_update_is_refused_without_a_pass(self):
        """It ends in an installer running as root, so it is the one that matters most."""
        self.assertEqual(self.ask("POST", "/update", {})[0], 401)

    # --- the code and the pass ---
    def test_a_wrong_code_is_refused(self):
        wrong = "000000" if self.code != "000000" else "111111"
        self.assertEqual(self.ask("POST", "/code", {"code": wrong})[0], 401)

    def test_the_right_code_hands_out_a_pass_the_browser_keeps_to_itself(self):
        status, set_cookie, _ = self.ask("POST", "/code", {"code": self.code})
        self.assertEqual(status, 200)
        self.assertIn("armonico_pass=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)

    def test_a_made_up_pass_is_refused(self):
        self.assertEqual(
            self.ask("GET", "/api/overview", cookie="armonico_pass=9999999999.deadbeef")[0], 401)

    def test_an_expired_pass_is_refused(self):
        self.assertFalse(game.pass_ok("1.0000"))

    def test_a_visit_pushes_the_end_date_out(self):
        cookie = self.unlock()
        self.assertIn("armonico_pass=", self.ask("GET", "/state", cookie=cookie)[1] or "")

    def test_five_wrong_codes_and_that_address_waits(self):
        wrong = "000000" if self.code != "000000" else "111111"
        for _ in range(6):
            status = self.ask("POST", "/code", {"code": wrong})[0]
        self.assertEqual(status, 429)

    def test_signing_every_screen_out_ends_a_pass_already_handed_out(self):
        cookie = self.unlock()
        self.assertEqual(self.ask("POST", "/code/signout", cookie=cookie)[0], 200)
        self.assertEqual(self.ask("GET", "/api/overview", cookie=cookie)[0], 401)

    def test_the_switch_opens_and_shuts_the_door(self):
        cookie = self.unlock()
        self.assertEqual(self.ask("POST", "/code/off", cookie=cookie)[0], 200)
        self.assertEqual(self.ask("GET", "/api/overview")[0], 200)
        self.assertEqual(self.ask("POST", "/code/on")[0], 200)
        self.assertEqual(self.ask("GET", "/api/overview")[0], 401)


class WhichNameAnswers(unittest.TestCase):
    """Which Host the lesson screen answers to.

    A page on the internet whose name points at this machine could otherwise drive the
    screen from any browser on the home network, so a name it was not told about is
    refused. The names the installer was given live in LESSON_HOSTS. Nothing here was
    covered before, and it is the one check a DNS record runs into first.
    """

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), game.UIHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.extra = game.EXTRA_HOSTS
        game.EXTRA_HOSTS = {"piano.vcl", "piano.home"}
        game.set_code_on(False)

    @classmethod
    def tearDownClass(cls):
        game.EXTRA_HOSTS = cls.extra
        game.set_code_on(True)
        cls.server.shutdown()

    def status_for(self, host):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/", None, {"Host": host})
        answer = conn.getresponse()
        answer.read()
        conn.close()
        return answer.status

    def test_an_address_and_the_machines_own_names_always_answer(self):
        import socket as sk
        me = sk.gethostname().lower()
        for host in (f"127.0.0.1:{self.port}", "localhost", me, f"{me}.local"):
            with self.subTest(host=host):
                self.assertEqual(self.status_for(host), 200)

    def test_a_name_in_lesson_hosts_answers_with_or_without_a_port(self):
        for host in ("piano.vcl", f"piano.vcl:{self.port}", "piano.home", "PIANO.VCL"):
            with self.subTest(host=host):
                self.assertEqual(self.status_for(host), 200)

    def test_a_name_nobody_listed_is_refused(self):
        """A DNS record on its own is not enough: this is what an unlisted name gets."""
        for host in ("piano.lan", "armonico.example.com", "evil.test"):
            with self.subTest(host=host):
                self.assertEqual(self.status_for(host), 403)


class GuardedCommands(unittest.TestCase):
    """The three MQTT commands that do not take the broker's word for who is asking."""

    def test_the_three_that_are_guarded(self):
        self.assertEqual(game.GUARDED_ACTIONS, ("code", "editmode", "profile"))

    def test_nothing_proves_itself_without_the_secret(self):
        self.assertFalse(game.admin_ok(""))
        self.assertFalse(game.admin_ok("on"))
        self.assertFalse(game.admin_ok("secret: on"))

    def test_a_wrong_secret_is_refused(self):
        self.assertFalse(game.admin_ok("secret:not-the-one on"))

    def test_the_right_secret_passes_and_is_taken_off(self):
        self.assertTrue(game.admin_ok("secret:secret-for-the-test on"))
        self.assertEqual(game.strip_admin("secret:secret-for-the-test on"), "on")
        self.assertEqual(game.strip_admin("secret:secret-for-the-test"), "")


class Replay(unittest.TestCase):
    """A recording is played by its name, and a name is not a path."""

    def test_a_path_is_not_a_name(self):
        player = game.Replayer()
        for attempt in ("../secret", "../../etc/passwd", "/etc/passwd",
                        "a/../../secret", "..%2Fsecret", ""):
            with self.subTest(attempt=attempt):
                self.assertFalse(player.start(attempt))


class Downloads(unittest.TestCase):
    """The address is checked before the fetch, so the fetch may not end somewhere else."""

    def test_an_address_outside_the_archives_is_refused(self):
        with self.assertRaises(ValueError):
            game._get("http://169.254.169.254/latest/meta-data/")

    def test_a_redirect_out_of_the_archives_is_refused(self):
        handler = game._NoRedirect()
        for target in ("http://127.0.0.1:8123/", "https://evil.example/x.mid",
                       "http://169.254.169.254/"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    handler.redirect_request(None, None, 302, "", {}, target)


class EditedWording(unittest.TestCase):
    """Editing keeps its HTML. What it saves may shape and style, and may not run."""

    def assertNothingRuns(self, html):
        cleaned = game.clean_html(html).lower()
        for sign in ("<script", "<iframe", "<svg", "javascript:", "data:text",
                     "onclick", "onload", "onerror", "onmouseover"):
            self.assertNotIn(sign, cleaned, f"{sign} survived in: {cleaned}")

    def test_nothing_that_executes_survives(self):
        for html in (
            "<script>alert(1)</script>",
            "<SCRIPT>alert(1)</SCRIPT>",
            "<p onclick='steal()'>x</p>",
            "<img src=x onerror=alert(1)>",
            "<a href='javascript:alert(1)'>x</a>",
            "<img src='data:text/html;base64,PHNjcmlwdD4='>",
            "<iframe src='http://elsewhere'></iframe>",
            "<svg onload=alert(1)></svg>",
            "<!-- <script>x</script> -->",
        ):
            with self.subTest(html=html):
                self.assertNothingRuns(html)

    def test_everything_that_shapes_and_styles_survives(self):
        kept = game.clean_html(
            '<h2 class="t">Title</h2><p style="color:red">Text <b>bold</b><br></p>'
            '<ul><li>one</li></ul><a href="https://example.com" target="_blank">link</a>'
            '<img src="/media/a.png" alt="a" width="40">'
            '<table><tr><td colspan="2">c</td></tr></table>')
        for wanted in ('<h2 class="t">', 'style="color:red"', "<b>bold</b>", "<br>",
                       "<ul>", "<li>one</li>", 'href="https://example.com"',
                       'src="/media/a.png"', 'colspan="2"'):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, kept)


class Recordings(unittest.TestCase):
    """Two recordings a second apart used to collide, and every name told the hour."""

    def test_names_do_not_repeat(self):
        import secrets
        import time
        made = {time.strftime("%Y%m%d-%H%M%S", time.localtime(0)) + "-" + secrets.token_hex(4)
                for _ in range(500)}
        self.assertEqual(len(made), 500)


if __name__ == "__main__":
    unittest.main()
