"""live.py - the Armonico Pi's screen, live in a phone's browser on the same network.

Off unless turned on (Settings › Network and links › Live view on a phone). While on, the screen serves a page on
port 8096: the phone opens armonico-pi.local:8096, types the 4-digit code shown on the card once, and sees the
screen as it is drawn. It only shows the screen: a touch on the phone does nothing on the device.

Nothing is spent while no one looks: a frame is taken only while a phone asked for one in the last 2 seconds,
at most FPS a second, and only as raw bytes (pg.image.tobytes, as the recording does); the JPEG is made in the
server's thread, once for each new frame, so drawing never waits for it. A video on the rest screen is drawn by the
GPU, not by the screen program, so the phone keeps the last frame drawn until the video ends.

The code is the same kind as the token page's (phone.py): an address that sends 5 wrong codes is refused, every
25 wrong codes in all draw a new code, and a phone that typed it gets a key of its own in a cookie, good until the
view is turned off or the screen program starts again.
"""
import html
import io
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import lowpower                # device/lowpower.py

PORT = 8096
FPS = 3 if lowpower.is_low() else 5     # frames a second at most, while someone looks (a Pi 3: each JPEG is slower to make)
LOOKING = 2.0                 # seconds after the last frame asked for: no one looks
MAX_BODY = 1024
MAX_WRONG = 5
NEW_CODE_AFTER = 25
MAX_KEYS = 20                 # phones let in at once; the oldest goes first

ASK = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Armonico Pi</title><style>body{{font:17px system-ui,sans-serif;margin:24px;background:#0d1424;color:#e9edf5}}
input,button{{font:inherit;width:100%;box-sizing:border-box;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2c3549;background:#161f33;color:#e9edf5}}
button{{background:#e8b64f;color:#0d1424;border:0;font-weight:700}}</style></head><body>
<h2>Armonico Pi</h2><p>{msg}</p><form method="post"><input name="code" inputmode="numeric" maxlength="4" placeholder="code / קוד" autocomplete="off" autofocus>
<button>Open / פתיחה</button></form></body></html>"""

VIEW = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Armonico Pi</title><style>html,body{margin:0;height:100%;background:#000}
body{display:flex;align-items:center;justify-content:center}
img{max-width:100vw;max-height:100vh;width:auto;height:auto;display:block;image-rendering:auto}
#off{position:fixed;left:0;right:0;bottom:12px;text-align:center;font:14px system-ui,sans-serif;color:#9aa3bd;display:none}</style></head>
<body><img id="s" alt="Armonico Pi"><div id="off">Not connected · אין חיבור</div><script>
const s=document.getElementById('s'),off=document.getElementById('off');let n=0,busy=false;
function next(){if(busy||document.hidden)return;busy=true;const i=new Image();
i.onload=()=>{s.src=i.src;off.style.display='none';busy=false;setTimeout(next,@MS@)};
i.onerror=()=>{off.style.display='block';busy=false;setTimeout(next,1500)};
i.src='/frame.jpg?'+(n++)}
document.addEventListener('visibilitychange',next);next();
</script></body></html>""".replace("@MS@", str(1000 // FPS))


class LiveView:
    def __init__(self, pg, port=PORT):
        self.pg, self.port = pg, port
        self.code = self.new_code()
        self.keys = []                # the keys of the phones let in, oldest first
        self.wrong, self.wrong_all = {}, 0
        self.lock = threading.Lock()
        self.srv = None
        self.asked_at = -99.0         # when a phone last asked for a frame
        self.next_at = 0.0
        self.raw = None               # (seq, bytes, size): the latest frame taken
        self.jpeg = (-1, b"")         # (seq, data): the latest frame made into a picture
        self.seq = 0

    @staticmethod
    def new_code():
        return f"{secrets.randbelow(10000):04d}"

    @property
    def on(self):
        return self.srv is not None

    def looking(self):
        return self.on and time.monotonic() - self.asked_at < LOOKING

    def feed(self, surface):
        """The frame just shown: kept as raw bytes, at most FPS a second, and only while a phone looks."""
        if not self.looking():
            return
        now = time.monotonic()
        if now < self.next_at:
            return
        self.next_at = now + 1 / FPS
        data = self.pg.image.tobytes(surface, "RGB")
        with self.lock:
            self.seq += 1
            self.raw = (self.seq, data, surface.get_size())

    def picture(self):
        """The latest frame as a JPEG, made once for each new frame (b"" before the first)."""
        with self.lock:
            raw, (done_seq, done) = self.raw, self.jpeg
        if raw is None or raw[0] == done_seq:
            return done
        seq, data, size = raw
        surf = self.pg.image.frombytes(data, size, "RGB")
        buf = io.BytesIO()
        try:
            self.pg.image.save(surf, buf, "frame.jpg")
        except Exception:                 # a pygame without JPEG: a PNG, larger but still a picture
            buf = io.BytesIO()
            self.pg.image.save(surf, buf, "frame.png")
        out = buf.getvalue()
        with self.lock:
            if seq > self.jpeg[0]:
                self.jpeg = (seq, out)
        return out

    def start(self):
        if self.srv is not None:
            return True
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, kind, extra=()):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def ask(self, msg, code=200):
                self.send(code, ASK.format(msg=html.escape(msg)).encode(), "text/html; charset=utf-8")

            def key(self):
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "armonico_live" and v:
                        with owner.lock:
                            return v in owner.keys
                return False

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if not self.key():
                    if path == "/frame.jpg":
                        return self.send(403, b"", "text/plain")
                    return self.ask("The code is on the Armonico Pi screen · הקוד מופיע על המסך")
                if path == "/frame.jpg":
                    owner.asked_at = time.monotonic()
                    data = owner.picture()
                    if not data:                  # the first frame is taken after this ask
                        return self.send(503, b"", "text/plain", [("Retry-After", "1")])
                    kind = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
                    return self.send(200, data, kind)
                self.send(200, VIEW.encode(), "text/html; charset=utf-8")

            def do_POST(self):
                who = self.client_address[0]
                try:
                    n = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    n = -1
                if not 0 <= n <= MAX_BODY:
                    self.close_connection = True
                    return self.ask("Refused · נדחה", 413 if n > MAX_BODY else 411)
                if owner.wrong.get(who, 0) >= MAX_WRONG:
                    self.close_connection = True
                    return self.ask("Refused · נדחה", 403)
                form = parse_qs(self.rfile.read(n).decode(errors="replace"))
                if (form.get("code") or [""])[0].strip() != owner.code:
                    with owner.lock:
                        owner.wrong[who] = owner.wrong.get(who, 0) + 1
                        owner.wrong_all += 1
                        if owner.wrong_all % NEW_CODE_AFTER == 0:
                            owner.code = owner.new_code()
                    return self.ask("Wrong code · קוד שגוי", 403)
                k = secrets.token_hex(16)
                with owner.lock:
                    owner.keys = (owner.keys + [k])[-MAX_KEYS:]
                self.send(303, b"", "text/plain", [("Location", "/"),
                                                  ("Set-Cookie", f"armonico_live={k}; Path=/; HttpOnly; SameSite=Strict")])

        try:
            srv = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        except OSError:
            return False
        srv.daemon_threads = True
        self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return True

    def stop(self):
        """Off: the server closes and every phone let in must type a new code next time."""
        srv, self.srv = self.srv, None
        with self.lock:
            self.keys, self.raw, self.jpeg = [], None, (-1, b"")
            self.wrong, self.wrong_all = {}, 0
        self.code = self.new_code()
        if srv is not None:
            srv.shutdown()
            srv.server_close()
