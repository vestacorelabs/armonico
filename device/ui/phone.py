"""phone.py - pasting a long token from a phone into the field open on the screen.

An access token is too long to type on the device's keys and a touch keyboard. While such a field is open,
the screen serves one tiny page on port 8097: the phone opens armonico-pi.local:8097, types the
4-digit code shown on the screen and pastes the token. The server runs only while the field is
open. An address that sends 5 wrong codes is refused from then on (one address stopped the server
for everyone before), and every 25 wrong codes in all draw a new code, shown on the screen.
"""
import html
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PORT = 8097
MAX_BODY = 8192
MAX_WRONG = 5                 # wrong codes from one address, then that address is refused
NEW_CODE_AFTER = 25           # wrong codes from all addresses together, then a new code is drawn

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Armonico Pi</title><style>body{{font:17px system-ui,sans-serif;margin:24px;background:#0d1424;color:#e9edf5}}
input,textarea,button{{font:inherit;width:100%;box-sizing:border-box;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2c3549;background:#161f33;color:#e9edf5}}
button{{background:#e8b64f;color:#0d1424;border:0;font-weight:700}}</style></head><body>
<h2>{label}</h2><p>{msg}</p><form method="post"><input name="code" inputmode="numeric" maxlength="4" placeholder="code / קוד" autocomplete="off">
<textarea name="value" rows="5" placeholder="paste here / הדבק כאן" autocomplete="off"></textarea><button>Send / שליחה</button></form></body></html>"""


class PasteServer:
    def __init__(self, label, on_value, port=PORT):
        self.label, self.on_value, self.port = label, on_value, port
        self.code = f"{secrets.randbelow(10000):04d}"
        self.wrong = {}               # address -> wrong codes from it
        self.wrong_all = 0
        self.lock = threading.Lock()
        self.srv = None

    def start(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def page(self, msg, code=200):
                body = PAGE.format(label=html.escape(owner.label), msg=html.escape(msg)).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.page("The code is on the Armonico Pi screen · הקוד מופיע על המסך")

            def do_POST(self):
                who = self.client_address[0]
                try:
                    n = int(self.headers.get("Content-Length") or "")
                except ValueError:
                    n = -1
                # a missing or negative length read the whole stream (rfile.read(-1)), past MAX_BODY
                if not 0 <= n <= MAX_BODY:
                    self.close_connection = True
                    return self.page("Refused · נדחה", 413 if n > MAX_BODY else 411)
                if owner.wrong.get(who, 0) >= MAX_WRONG:
                    self.close_connection = True
                    return self.page("Refused · נדחה", 403)
                form = parse_qs(self.rfile.read(n).decode(errors="replace"))
                if (form.get("code") or [""])[0].strip() != owner.code:
                    with owner.lock:
                        owner.wrong[who] = owner.wrong.get(who, 0) + 1
                        owner.wrong_all += 1
                        if owner.wrong_all % NEW_CODE_AFTER == 0:
                            owner.code = f"{secrets.randbelow(10000):04d}"
                    return self.page("Wrong code · קוד שגוי", 403)
                value = "".join((form.get("value") or [""])[0].split())
                owner.on_value(value)
                self.page("Sent to the screen · נשלח למסך")

        try:
            self.srv = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        except OSError:
            self.srv = None
            return False
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return True

    def stop(self):
        srv, self.srv = self.srv, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
