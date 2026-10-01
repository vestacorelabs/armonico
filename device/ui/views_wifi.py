"""views_wifi.py - Wi-Fi on the device: the networks around, the password on the touch keyboard, the
connection, and the saved networks. Settings › Network and links › Wi-Fi, and a step of the setup
(after the language, only with no network cable that has an address).

Everything that asks NetworkManager goes to the root unit (system.wifi, root_actions wifi-*) on a
thread of its own, one at a time; the drawing only reads what came back. The list is asked for again
every RESCAN seconds while the page (or the setup's step) is in front, and never while it is closed.
The password is held in memory only during the try; NetworkManager keeps it, Armonico does not.
"""
import math
import os
import threading
import time

from .canvas import W, H
from .widgets import RED, BAR
from . import system

RESCAN = 15                 # seconds between two looks at the networks around, while the page is open
CONNECT_WAIT = 20           # seconds NetworkManager is given to connect (root_actions.WIFI_WAIT)
NET_CELL = 1                # the Wi-Fi cell on Settings › Network and links
PER = 7                     # rows on the page at once
JOINABLE = ("psk", "sae", "open")
LESSON_PORT = os.environ.get("LESSON_UI_PORT", "8099")


def bars(signal):
    """4 levels, at the same steps as nmcli's own bars (above 80, 55, 30 and 5)."""
    return 4 if signal > 80 else 3 if signal > 55 else 2 if signal > 30 else 1 if signal > 5 else 0


def password_ok(pw):
    """What WPA takes: 8 to 63 printable ASCII characters, or 64 hex digits (root_actions checks again)."""
    if len(pw) == 64 and all(c in "0123456789abcdefABCDEF" for c in pw):
        return True
    return 8 <= len(pw) <= 63 and all(32 <= ord(c) <= 126 for c in pw)


class WifiViews:
    @property
    def wf(self):
        w = self.__dict__.get("_wf")
        if w is None:
            w = self._wf = {"nm": None, "dev": "", "ssid": "", "ip": "", "gw": "", "via": "", "cable_ip": "", "signal": 0, "nets": [], "saved": [], "err": "",
                            "busy": False, "scan_at": 0.0, "scanned": False, "status_at": 0.0,
                            "screen": "list", "from": "settings", "sel": 0, "ssel": 0, "wsel": 0,
                            "target": "", "t0": 0.0, "gen": 0, "result": {}, "fail": "", "last": None, "due": None}
        return w

    # ------------------------------------------------------------------ what NetworkManager says
    def wifi_take(self, data):
        w = self.wf
        w["nm"] = bool(data.get("nm"))
        w["err"] = ""
        w["blocked"] = bool(data.get("blocked"))
        for k in ("dev", "ssid", "ip", "gw", "via", "cable_ip"):
            if k in data:
                w[k] = str(data.get(k) or "")
        if "signal" in data:
            w["signal"] = int(data.get("signal") or 0)
        if isinstance(data.get("nets"), list):
            w["nets"] = [n for n in data["nets"] if isinstance(n, dict) and n.get("ssid")]
        if isinstance(data.get("saved"), list):
            w["saved"] = [c for c in data["saved"] if isinstance(c, dict) and c.get("uuid")]

    def wifi_status_soon(self):
        """The connection now, for the Wi-Fi cell: asked on a thread, at most once in 5 seconds."""
        w = self.wf
        if w["busy"] or time.monotonic() - w["status_at"] < 5:
            return
        w["busy"], w["status_at"] = True, time.monotonic()

        def go():
            try:
                ok, data = system.wifi_status()
                if ok:
                    self.wifi_take(data)
                else:
                    w["err"] = data
            finally:
                w["busy"] = False
        threading.Thread(target=go, daemon=True).start()

    def wifi_follow(self):
        """Called while the page or the setup's step is drawn: a new look at the networks every RESCAN
        seconds, the first one with a fresh scan. Nothing calls it while they are closed."""
        w = self.wf
        if w["busy"] or w["screen"] == "connecting" or w["nm"] is False:
            return
        if time.monotonic() - w["scan_at"] < RESCAN:
            return
        rescan = not w["scanned"]
        w["busy"], w["scanned"] = True, True

        def go():
            try:
                ok, data = system.wifi("scan", rescan=rescan, timeout=60)
                if ok:
                    self.wifi_take(data)
                else:
                    w["err"] = data
            finally:
                w["scan_at"] = time.monotonic()
                w["busy"] = False
        threading.Thread(target=go, daemon=True).start()

    def link_text(self):
        """The connection card: what carries the traffic (cable or Wi-Fi), the network's name and the signal."""
        w, tr = self.wf, self.tr
        if w["via"] == "ethernet":
            return tr("c.viaCable"), tr("c.viaCableD")
        if w["via"] == "wifi" and w["ssid"]:
            return tr("c.viaWifi", s=w["ssid"]), tr("c.sig", n=bars(int(w.get("signal") or 0)))
        return tr("notConnected"), ""

    def link_address(self):
        """The address card: this device's address and its gateway."""
        w, tr = self.wf, self.tr
        ip = (w.get("cable_ip") if w["via"] == "ethernet" else w["ip"]) or self.ip
        gw = tr("c.gw", g=w["gw"]) if w.get("gw") else ""
        return ip or tr("notConnected"), gw

    def wifi_cell_text(self):
        w = self.wf
        if w["nm"] is False:
            return self.tr("wf.naShort")
        if w["ssid"]:
            return w["ssid"]
        return self.tr("notConnected") if w["nm"] else "…"

    # ------------------------------------------------------------------ actions
    def open_wifi(self, back="settings"):
        w = self.wf
        w.update(screen="list", sel=0, ssel=0, scan_at=0.0, scanned=False, due=None, last=None, fail="")
        w["from"] = back
        self.go("wifi")

    def wifi_leave(self):
        """Back where the page was opened from: the network page, on the Wi-Fi cell, or the setup."""
        w = self.wf
        w.update(screen="list", last=None, due=None)
        if w["from"] == "setup":
            self.go("setup")
            return
        self.page, self.sel = "net", NET_CELL
        self.go("settings")

    def wifi_pick(self, n):
        tr = self.tr
        if n.get("kind") not in JOINABLE:
            self.toast(tr("wf.e.unsupported"), 4)
            return
        if n.get("active") and self.wf["ip"]:
            self.wf.update(screen="done", result={"ssid": n["ssid"], "ip": self.wf["ip"]})
            return
        if n.get("known") or not n.get("secure"):
            self.wifi_connect(n["ssid"], "", False)
        else:
            self.wifi_password(n["ssid"])

    def wifi_hidden(self):
        def named(v):
            if not v or len(v.encode("utf-8")) > 32 or any(ord(c) < 32 for c in v):
                self.toast(self.tr("wf.nameBad"), 3)
                return
            hold["e"]["back"] = "edit"
            self.wifi_password(v, hidden=True)
        self.edit_field(self.tr("wf.nameE"), lambda: "", named, keep=True, ok=self.tr("continue"), back=self.view)
        hold = {"e": self.edit}

    def wifi_password(self, ssid, typed="", note="", hidden=False):
        """The password on the touch keyboard (or a USB one), with Show and the symbols layer. Wrong,
        it opens again with what was typed and says why."""
        tr = self.tr

        def entered(v):
            if hidden and v == "":
                self.wifi_connect(ssid, "", True)            # a hidden open network
            elif not password_ok(v):
                hold["e"]["back"] = "edit"
                self.wifi_password(ssid, v, tr("wf.pwLen"), hidden)
                return
            else:
                self.wifi_connect(ssid, v, hidden)
            hold["e"]["back"] = "wifi"
        label = tr("wf.pwForOpen" if hidden else "wf.pwFor", s=ssid)
        # Cancel goes back to the list it came from: the setup's step or the Wi-Fi page
        self.edit_field(label, lambda: typed, entered, secret=True, keep=True, reveal=True, ok=tr("wf.connect"),
                        note=note, back="setup" if self.wf["from"] == "setup" else "wifi")
        hold = {"e": self.edit}

    def wifi_connect(self, ssid, pw, hidden):
        w = self.wf
        w["gen"] += 1
        gen = w["gen"]
        system.cancel(w.get("ticket"))                       # an earlier try still waiting its turn never runs
        ticket = w["ticket"] = system.Ticket()
        w.update(screen="connecting", target=ssid, t0=time.monotonic(), fail="", last=(ssid, pw, hidden), due=None)

        def go():
            ok, data = system.wifi("connect", ssid=ssid, password=pw, hidden=hidden, timeout=CONNECT_WAIT + 45, ticket=ticket)
            if w["gen"] != gen:
                return                                       # cancelled meanwhile: not this screen's answer
            if ok:
                w.update(screen="done", result={"ssid": ssid, "ip": str(data.get("ip") or "")}, last=None,
                         ssid=ssid, ip=str(data.get("ip") or ""), scan_at=0.0, nm=True)
                if data.get("ip") and not self.ip:
                    self.ip = str(data["ip"])
            elif data in ("wrong-password", "password-needed"):
                # the keyboard again, from the drawing loop (never from this thread), with what was typed
                w["due"] = ("pw", ssid, pw, hidden, "wf.badPw" if data == "wrong-password" else "")
            else:
                w.update(screen="fail", fail=str(data))
        threading.Thread(target=go, daemon=True).start()

    def wifi_cancel(self):
        """Gives up on this page's connection: still waiting behind another root action, it is dropped and never runs."""
        w = self.wf
        system.cancel(w.get("ticket"))
        w["gen"] += 1
        w.update(screen="list", last=None, scan_at=0.0)

    def wifi_forget(self, c):
        name = c.get("ssid") or c.get("name") or ""

        def go():
            ok, msg = system.wifi("forget", uuid=c["uuid"], timeout=40)
            self.wf["scan_at"] = 0.0
            if ok:
                self.wf["saved"] = [x for x in self.wf["saved"] if x.get("uuid") != c["uuid"]]
                if c.get("active"):
                    self.wf.update(ssid="", ip="")
                self.toast(self.tr("wf.forgot", s=name), 3)
            else:
                self.wifi_fail_toast(msg)
        threading.Thread(target=go, daemon=True).start()

    def wifi_reason(self, msg):
        key = "wf.e." + str(msg)
        if msg == "no-root":
            return self.tr("noRoot")
        return self.tr(key) if key in _known() else self.tr("wf.e.other")

    def wifi_fail_toast(self, msg):
        self.toast(self.wifi_reason(msg), 4)

    # ------------------------------------------------------------------ drawing pieces
    def wifi_bars(self, x, y, level):
        """4 bars, bottom at y. In Hebrew they sit at the row's other end but still rise to the right:
        a signal sign is not text, so it is not mirrored (as phones draw it)."""
        P, cv = self.P, self.cv
        if cv.mirrored:
            x = W - x - 22
        with cv.physical():
            for i, h in enumerate((6, 9, 12, 15)):
                cv.rect(x + i * 6, y - h, 4, h, P["lit"] if i < level else (128, 128, 128, 110), r=1)

    def wifi_lock(self, cx, y, color):
        """A small padlock, its base on y (the fonts have no lock sign)."""
        cv = self.cv
        cv.arc(cx, y - 9, 4, math.pi, 2 * math.pi, color, width=2)
        cv.line(cx - 4, y - 9, cx - 4, y - 7, color, 2)
        cv.line(cx + 4, y - 9, cx + 4, y - 7, color, 2)
        cv.rect(cx - 6, y - 8, 12, 9, color, r=2)

    def wifi_row(self, x, y, w, h, n, framed, fn):
        P, cv, tr = self.P, self.cv, self.tr
        self.hit(x, y, w, h, fn)
        cv.rect(x, y, w, h, P["panel"], r=10)
        if framed:
            cv.rect(x, y, w, h, P["sel"], r=10, width=2.5)
        base = y + h / 2 + 6
        self.wifi_bars(x + 16, base + 1, bars(int(n.get("signal") or 0)))
        end = x + w - 16
        if n.get("kind") not in JOINABLE:
            tail = tr("wf.unsupported")
            cv.text(tail, end, base - 1, 13, P["dim"], align="right")
            end -= cv.measure(tail, 13) + 14
        elif n.get("secure"):
            self.wifi_lock(end - 7, base - 1, P["dim"])
            end -= 28
        else:
            cv.text(tr("wf.open"), end, base - 1, 13, P["dim"], align="right")
            end -= cv.measure(tr("wf.open"), 13) + 14
        if n.get("active"):
            mark = "✓ " + tr("wf.connected")
            cv.text(mark, end, base - 1, 13, P["ok"], True, "right")
            end -= cv.measure(mark, 13, True) + 14
        name_x = x + 52
        cv.text(cv.fit(n["ssid"], max(60, end - name_x), 16, True), name_x, base, 16, P["ink"], True)

    def wifi_extra_row(self, x, y, w, h, sign, text, framed, fn):
        P, cv = self.P, self.cv
        self.hit(x, y, w, h, fn)
        cv.rect(x, y, w, h, P["sel"] if framed else P["dim"], r=10, width=2.5 if framed else 1.2)
        cv.text(sign, x + 22, y + h / 2 + 7, 18, P["gold"], True, "center")
        cv.text(self.short(text, w - 64, 15, True), x + 44, y + h / 2 + 6, 15, P["ink"], True)

    def wifi_message(self, big, small="", color=None, top=214):
        P, cv = self.P, self.cv
        cv.text(self.short(big, W - 48, 24, True), W / 2, top, 24, color or P["ink"], True, "center")
        for i, line in enumerate(l for l in small.split("\n") if l):
            cv.text(self.short(line, W - 48, 15), W / 2, top + 34 + i * 26, 15, P["dim"], align="center")

    # ------------------------------------------------------------------ the page
    def view_wifi(self, t):
        w, tr = self.wf, self.tr
        self.hills(0.35)
        if w["due"]:
            kind, ssid, pw, hidden, note = w["due"]
            w["due"] = None
            w["screen"] = "list"
            self.wifi_password(ssid, pw, tr(note) if note else "", hidden)
            return
        if w["screen"] == "connecting" and time.monotonic() - self.wifi_started() > CONNECT_WAIT + 50:
            system.cancel(w.get("ticket"))
            w["gen"] += 1                                    # the root unit never answered
            w.update(screen="fail", fail="timeout")
        getattr(self, "wifi_screen_" + w["screen"], self.wifi_screen_list)(t)

    def wifi_started(self):
        """When the connection really started: the time it got its turn at the root unit. While it waits behind another
        root action (a Bluetooth pairing, a scan) the clock has not started, so it reads as now."""
        t = self.wf.get("ticket")
        if t is None:
            return self.wf["t0"]
        return t.started if t.started is not None else time.monotonic()

    def wifi_unavailable(self):
        """True (and the page says why) when there is no Wi-Fi to work with."""
        w, tr, P, cv = self.wf, self.tr, self.P, self.cv
        if w["nm"] is False:
            big, small = tr("wf.na"), tr("wf.naD")
        elif w["nm"] and not w["dev"]:
            big, small = tr("wf.noAdapter"), tr("wf.noAdapterD")
        elif w.get("blocked") and not w["nets"]:
            big, small = tr("wf.blocked"), tr("wf.blockedD")
        elif w["err"] and not w["nets"]:
            big, small = tr("wf.na") if w["err"] == "no-nm" else self.wifi_reason(w["err"]), ""
        else:
            return False
        cv.text(self.short(big, W - 48, 18, True), 24, 100, 18, P["ink"], True)
        if small:
            self.paras([small], 24, 128, W - 48, 15, 21, P["dim"])
        return True

    def wifi_items(self, few=None):
        nets = self.wf["nets"] if few is None else self.wf["nets"][:few]
        return [("net", n) for n in nets] + [("hidden", None)]

    def wifi_screen_list(self, t):
        w, tr = self.wf, self.tr
        self.wifi_follow()
        self.header(tr("wf.title"), tr("wf.scanning") if w["busy"] else "")
        if self.wifi_unavailable():
            self.bar([("☰", tr("back"), self.wifi_leave), ("▼", "", None), ("▲", "", None), ("✓", "", None)])
            return
        items = self.wifi_items() + [("saved", None)]
        w["sel"] = max(0, min(w["sel"], len(items) - 1))
        top = 58
        if not w["nets"]:
            self.cv.text(tr("wf.scanning") if w["busy"] or not w["scan_at"] else tr("wf.none"), 24, top + 24, 15, self.P["dim"])
            top += 44
        per = PER - (1 if not w["nets"] else 0)
        first = max(0, min(w["sel"] - per + 1, len(items) - per))
        for j, (kind, n) in enumerate(items[first:first + per]):
            i = first + j
            y = top + j * 52
            pick = lambda i=i: (w.update(sel=i), self.wifi_act(items[i]))
            if kind == "net":
                self.wifi_row(24, y, W - 48, 46, n, i == w["sel"], pick)
            elif kind == "hidden":
                self.wifi_extra_row(24, y, W - 48, 46, "+", tr("wf.hidden"), i == w["sel"], pick)
            else:
                count = len(w["saved"])
                self.wifi_extra_row(24, y, W - 48, 46, "›", tr("wf.savedRow") + (f" · {count}" if count else ""),
                                    i == w["sel"], pick)
        self.list_marks(first, per, len(items), top, 52, lambda d: w.update(sel=max(0, min(len(items) - 1, w["sel"] + d))))
        cur = items[w["sel"]]
        ok = tr("wf.connect") if cur[0] == "net" and cur[1].get("kind") in JOINABLE and not cur[1].get("active") \
            else tr("wf.typeName") if cur[0] == "hidden" else tr("wf.see") if cur[0] == "saved" else \
            tr("wf.details") if cur[1].get("active") else ""
        self.bar([("☰", tr("back"), self.wifi_leave),
                  ("▼", tr("down"), lambda: w.update(sel=(w["sel"] + 1) % len(items))),
                  ("▲", tr("up"), lambda: w.update(sel=(w["sel"] - 1) % len(items))),
                  ("✓", ok, (lambda: self.wifi_act(cur)) if ok else None)])

    def wifi_act(self, item):
        kind, n = item
        if kind == "net":
            self.wifi_pick(n)
        elif kind == "hidden":
            self.wifi_hidden()
        elif kind == "saved":
            self.wf.update(screen="saved", ssel=0)

    def wifi_screen_saved(self, t):
        w, tr, P, cv = self.wf, self.tr, self.P, self.cv
        self.wifi_follow()                                   # the same look keeps the list of saved ones fresh
        saved = sorted(w["saved"], key=lambda c: (not c.get("active"), -int(c.get("at") or 0)))
        self.header(tr("wf.saved"), str(len(saved)) if saved else "")
        back = lambda: w.update(screen="list")
        if not saved:
            cv.text(tr("wf.noneSaved"), 24, 100, 17, P["ink"], True)
            self.bar([("☰", tr("back"), back), ("▼", "", None), ("▲", "", None), ("✓", "", None)])
            return
        w["ssel"] = max(0, min(w["ssel"], len(saved) - 1))
        per = PER - 1
        first = max(0, min(w["ssel"] - per + 1, len(saved) - per))
        y = 58
        for j, c in enumerate(saved[first:first + per]):
            i = first + j
            y = 58 + j * 52
            self.hit(24, y, W - 48, 46, lambda i=i: w.update(ssel=i))
            cv.rect(24, y, W - 48, 46, P["panel"], r=10)
            if i == w["ssel"]:
                cv.rect(24, y, W - 48, 46, P["sel"], r=10, width=2.5)
            if c.get("active"):
                state, col = tr("wf.now"), P["ok"]
            elif c.get("at"):
                state, col = tr("wf.used", d=self.local(int(c["at"]) * 1000).strftime("%d.%m")), P["dim"]
            else:
                state, col = tr("wf.neverUsed"), P["dim"]
            forget = tr("wf.forget")
            fw = cv.measure(forget, 14, True)
            cv.text(forget, W - 40, y + 29, 14, RED, True, "right")
            sw = cv.measure(state, 13)
            cv.text(state, W - 40 - fw - 18, y + 29, 13, col, align="right")
            cv.text(cv.fit(c.get("ssid") or c.get("name") or "", W - 120 - fw - sw, 16, True), 40, y + 29, 16, P["ink"], True)
        self.list_marks(first, per, len(saved), 58, 52, lambda d: w.update(ssel=max(0, min(len(saved) - 1, w["ssel"] + d))))
        y += 46 + 14
        self.paras([tr("wf.savedNote")], 28, y + 8, W - 56, 13, 19, P["dim"])
        cur = saved[w["ssel"]]
        name = cur.get("ssid") or cur.get("name") or ""
        self.bar([("☰", tr("back"), back),
                  ("▼", tr("down"), lambda: w.update(ssel=(w["ssel"] + 1) % len(saved))),
                  ("▲", tr("up"), lambda: w.update(ssel=(w["ssel"] - 1) % len(saved))),
                  ("✓", tr("wf.forget"), lambda: self.ask(tr("wf.forgetQ", s=name), lambda: self.wifi_forget(cur)))])

    def wifi_screen_connecting(self, t):
        w, tr, P = self.wf, self.tr, self.P
        self.header(tr("wf.title"))
        self.spin(W / 2, 160, 22, P["gold"], t)
        t0 = self.wifi_started()
        left = max(0, int(CONNECT_WAIT - (time.monotonic() - t0) + 0.99))
        ticket = w.get("ticket")
        if ticket is not None and ticket.waiting():
            small = tr("wf.waitTurn")
        else:
            small = tr("wf.upTo", n=left) if left else tr("wf.stillTrying")
        self.wifi_message(tr("wf.connecting", s=w["target"]), small)
        self.bar([("☰", tr("cancel"), self.wifi_cancel), ("▼", "", None), ("▲", "", None), ("✓", "", None)])

    def wifi_done_go(self):
        w = self.wf
        if w["from"] == "setup":
            w.update(screen="list")
            self.go("setup")
            self.setup_go(1)
        else:
            self.wifi_leave()

    def wifi_screen_done(self, t):
        w, tr, P = self.wf, self.tr, self.P
        r = w["result"] or {}
        self.header(tr("wf.title"))
        ip = r.get("ip", "")
        lines = tr("wf.addr", s=r.get("ssid", ""), ip=ip) if ip else tr("wf.noAddr", s=r.get("ssid", ""))
        if ip:
            lines += "\n" + tr("wf.lesson", a=f"{ip}:{LESSON_PORT}")
        url = f"http://{ip}:{LESSON_PORT}" if ip else ""
        if url:                                      # the message moves up to make room for the lesson screen's code
            self.wifi_message("✓ " + tr("wf.ok"), lines, P["ok"], top=100)
            self.draw_qr(W / 2 - 100, 198, 200, url)
        else:
            self.wifi_message("✓ " + tr("wf.ok"), lines, P["ok"])
        self.bar([("☰", tr("back"), lambda: w.update(screen="list", scan_at=0.0)), ("▼", "", None), ("▲", "", None),
                  ("✓", tr("w.continue").rstrip(" ›") if w["from"] == "setup" else tr("wf.close"), self.wifi_done_go)])

    def wifi_screen_fail(self, t):
        w, tr = self.wf, self.tr
        self.header(tr("wf.title"))
        self.wifi_message("✕ " + tr("wf.fail"), self.wifi_reason(w["fail"]), RED)
        again = w["last"]
        self.bar([("☰", tr("back"), lambda: w.update(screen="list", last=None, scan_at=0.0)), ("▼", "", None), ("▲", "", None),
                  ("✓", tr("wf.again") if again else "", (lambda: self.wifi_connect(*again)) if again else None)])

    # ------------------------------------------------------------------ the setup's step
    def wifi_setup_body(self, t):
        """The setup's Wi-Fi step, under its title and dots: the strongest networks, a hidden one, and
        one line on what works without a network. ☰ skips it."""
        w, tr, P, cv = self.wf, self.tr, self.P, self.cv
        w["from"] = "setup"
        self.wifi_follow()
        cv.text(tr("w.wifiI"), 24, 98, 17, P["ink"], True)
        if w["busy"]:
            cv.text(tr("wf.scanning"), W - 24, 98, 13, P["dim"], align="right")
        y0 = 110
        if self.wifi_unavailable_setup(y0):
            items = []
        else:
            items = self.wifi_items(few=4)
            w["wsel"] = max(0, min(w["wsel"], len(items) - 1))
            for i, (kind, n) in enumerate(items):
                y = y0 + i * 48
                pick = lambda i=i: (w.update(wsel=i), self.wifi_setup_act(items[i]))
                if kind == "net":
                    self.wifi_row(24, y, W - 48, 42, n, i == w["wsel"], pick)
                else:
                    self.wifi_extra_row(24, y, W - 48, 42, "+", tr("wf.hidden"), i == w["wsel"], pick)
        self.paras([tr("w.wifiOff")], 24, H - BAR - 40, W - 48, 14, 20, P["dim"])
        cur = items[w["wsel"]] if items else None
        if cur is None:
            ok = ""
        elif cur[0] == "net" and cur[1].get("active"):
            ok = tr("w.continue").rstrip(" ›")
        elif cur[0] == "net":
            ok = tr("pick") if cur[1].get("kind") in JOINABLE else ""
        else:
            ok = tr("wf.typeName")
        n_items = len(items)
        self.bar([("☰", tr("w.skipBtn"), lambda: self.setup_go(1)),
                  ("▼", tr("down"), (lambda: w.update(wsel=(w["wsel"] + 1) % n_items)) if n_items else None),
                  ("▲", tr("up"), (lambda: w.update(wsel=(w["wsel"] - 1) % n_items)) if n_items else None),
                  ("✓", ok, (lambda: self.wifi_setup_act(cur)) if ok else None)])

    def wifi_unavailable_setup(self, y):
        w, tr, P, cv = self.wf, self.tr, self.P, self.cv
        if w["nm"] is False or (w["err"] == "no-nm" and not w["nets"]):
            text = tr("wf.na")
        elif w["nm"] and not w["dev"]:
            text = tr("wf.noAdapter")
        elif w.get("blocked") and not w["nets"]:
            text = tr("wf.blocked")
        elif w["err"] and not w["nets"]:
            text = self.wifi_reason(w["err"])
        else:
            return False
        cv.text(self.short(text, W - 48, 16, True), 24, y + 24, 16, P["ink"], True)
        return True

    def wifi_setup_act(self, item):
        kind, n = item
        if kind == "net" and n.get("active"):
            self.setup_go(1)
            return
        self.wf["from"] = "setup"
        if kind == "net":
            if n.get("kind") not in JOINABLE:
                self.toast(self.tr("wf.e.unsupported"), 4)
                return
            if n.get("known") or not n.get("secure"):
                self.wifi_connect(n["ssid"], "", False)
                self.go("wifi")
            else:
                self.wifi_password(n["ssid"])
        else:
            self.wifi_hidden()


def _known():
    from .texts import T
    return T
