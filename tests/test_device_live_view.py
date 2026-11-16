"""The live view of the device's screen in a phone's browser (device/ui/live.py and its card)."""
import os
import socket
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "device"))

try:
    import pygame
    pygame.display.init()
except Exception:                                   # pragma: no cover - no pygame here
    pygame = None

from test_device_ui import load_screen, make_app    # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@unittest.skipIf(pygame is None, "pygame is missing")
class LiveServerTests(unittest.TestCase):
    def setUp(self):
        from ui.live import LiveView
        self.lv = LiveView(pygame, port=free_port())
        self.assertTrue(self.lv.start())
        self.url = f"http://127.0.0.1:{self.lv.port}"
        self.surf = pygame.Surface((80, 48))
        self.surf.fill((200, 30, 30))

    def tearDown(self):
        self.lv.stop()

    def get(self, path, cookie=None):
        req = urllib.request.Request(self.url + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def login(self, code):
        op = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(self.url + "/", data=f"code={code}".encode(), method="POST")
        try:
            r = op.open(req, timeout=5)
            return r.status, r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.headers

    def test_no_frame_without_the_code(self):
        st, body, _ = self.get("/")
        self.assertEqual(st, 200)
        self.assertIn(b'name="code"', body)
        self.assertEqual(self.get("/frame.jpg")[0], 403)

    def test_code_then_frames_only_while_looking(self):
        self.lv.feed(self.surf)
        self.assertIsNone(self.lv.raw, "a frame was taken while no one looked")
        st, h = self.login(self.lv.code)
        self.assertEqual(st, 303)
        cookie = h["Set-Cookie"].split(";")[0]
        st, body, _ = self.get("/", cookie)
        self.assertIn(b"frame.jpg", body)
        self.assertEqual(self.get("/frame.jpg", cookie)[0], 503)       # asked: the next frame is taken
        self.lv.feed(self.surf)
        st, data, hd = self.get("/frame.jpg", cookie)
        self.assertEqual(st, 200)
        self.assertIn(hd["Content-Type"], ("image/jpeg", "image/png"))
        pic = pygame.image.load(__import__("io").BytesIO(data))
        self.assertEqual(pic.get_size(), (80, 48))
        self.lv.feed(self.surf)                                         # at most FPS a second
        self.assertEqual(self.lv.raw[0], 1)

    def test_wrong_codes_lock_out_and_off_forgets_the_phones(self):
        wrong = "0000" if self.lv.code != "0000" else "1111"
        for _ in range(5):
            self.assertEqual(self.login(wrong)[0], 403)
        self.assertEqual(self.login(self.lv.code)[0], 403)              # this address is refused now
        self.lv.wrong.clear()
        cookie = self.login(self.lv.code)[1]["Set-Cookie"].split(";")[0]
        port = self.lv.port
        self.lv.stop()
        self.assertFalse(self.lv.on)
        from ui.live import LiveView
        self.lv = LiveView(pygame, port=port)
        self.assertTrue(self.lv.start())
        self.assertEqual(self.get("/frame.jpg", cookie)[0], 403)


@unittest.skipIf(pygame is None, "pygame is missing")
class LiveCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_off_by_default_and_the_card_turns_it_on_and_off(self):
        app = make_app(self.mod)
        app.live.port = free_port()
        app.save = lambda **kw: app.st.update(kw)
        app.open_page("net")
        names = [c[0] for c in app.settings_cells()["net"]]
        self.assertIn(app.tr("c.live"), names)
        self.assertFalse(app.live.on)
        cell = app.live_cell()
        self.assertEqual(cell[1], app.tr("off"))
        cell[3]()
        try:
            self.assertTrue(app.live.on)
            self.assertTrue(app.st["live_view"])
            self.assertIn(app.live.code, app.live_cell()[1])
            app.draw()
        finally:
            app.live_cell()[3]()
        self.assertFalse(app.live.on)
        self.assertFalse(app.st["live_view"])


@unittest.skipIf(pygame is None, "pygame is missing")
class DeepCheckRateTests(unittest.TestCase):
    """While the deep check measures, its own screen is drawn MEASURE_FPS times a second (app.measuring)."""
    @classmethod
    def setUpClass(cls):
        cls.mod = load_screen()

    def test_only_the_measuring_step_is_slowed(self):
        app = make_app(self.mod)
        from ui import app as app_mod
        self.assertLessEqual(app_mod.MEASURE_FPS, 5)
        self.assertFalse(app.measuring())
        app.view, app.dc = "deep", {"phase": "run", "step": "live"}
        self.assertTrue(app.measuring())
        app.dc["step"] = "boot"
        self.assertFalse(app.measuring())
        app.dc = {"phase": "result"}
        self.assertFalse(app.measuring())


if __name__ == "__main__":
    unittest.main()
