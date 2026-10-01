"""The Telegram setup: the texts fit Telegram's limits, the lists match the docs, and a refused call gives a reason."""
import json
import re
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "setup"))
import telegram_setup as t  # noqa: E402


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(n)
        ok = "GOOD" in self.path
        body = json.dumps({"ok": True, "result": {"username": "x_bot"}} if ok
                          else {"ok": False, "description": "Unauthorized"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TelegramSetup(unittest.TestCase):
    def test_limits(self):
        self.assertLessEqual(len(t.DESCRIPTION), 512)
        self.assertLessEqual(len(t.DESCRIPTION_HE), 512)
        self.assertLessEqual(len(t.SHORT), 120)
        self.assertLessEqual(len(t.SHORT_HE), 120)
        for name, text in t.COMMANDS:
            self.assertRegex(name, r"^[a-z0-9_]{1,32}$")
            self.assertTrue(1 <= len(text) <= 256, name)
            self.assertIn(name, t.COMMANDS_HE)
            self.assertTrue(1 <= len(t.COMMANDS_HE[name]) <= 256, name)
        self.assertEqual(len(t.COMMANDS), len(t.COMMANDS_HE))

    def test_commands_match_the_docs(self):
        doc = (ROOT / "docs" / "telegram.md").read_text(encoding="utf-8")
        block = re.search(r"```\n(pianostatus - .*?)```", doc, re.S).group(1)
        listed = [tuple(x.split(" - ", 1)) for x in block.strip().splitlines()]
        self.assertEqual(listed, t.COMMANDS)

    def test_the_picture_is_square_and_small(self):
        data = (ROOT / "setup" / "bot-photo.jpg").read_bytes()
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertLess(len(data), 10 * 1024 * 1024)

    def test_api_answers(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        t.API = f"http://127.0.0.1:{srv.server_port}"
        try:
            self.assertEqual(t.tg("1:GOOD", "getMe")["username"], "x_bot")
            with self.assertRaises(t.TgError) as e:
                t.tg("1:BAD", "getMe")
            self.assertIn("Unauthorized", str(e.exception))
        finally:
            srv.shutdown()
        t.API = "http://127.0.0.1:1"
        with self.assertRaises(t.TgError):
            t.tg("1:GOOD", "getMe", timeout=2)


if __name__ == "__main__":
    unittest.main()
