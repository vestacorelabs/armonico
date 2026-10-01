"""views_plex.py - the TV card on the device: what plays on the TV, picked from the Plex library by name.

Only while the card is turned on (PlexFun10 on the command line or in the Android console): the engine
answers /api/plex then, and Settings › Playing shows a TV row. The server and its token are set once on
the lesson screen (Settings › TV) or here: the address and the token are typed on the screen or pasted from a
phone (its QR code), then Connect. Easier: Sign in with Plex shows a code for plex.tv/link and its QR, and
the engine gets the token and finds the server by itself (piano_plex.link_start). Once connected, the library is walked as a list: a library, a series, a season,
then ✓ on a movie, an episode, a video or "the whole series" adds it to the list. With something on the
list, the first rows are the actions: play it now, keep it as the reward after a good lesson, clear it.
The playing itself is the engine's /plex/play: the engine goes through the items and Home Assistant works the
TV (lessons/piano_plex.py). The card is there only with Home Assistant (an external broker).
"""
import threading
import time

from .canvas import W

ROWS = 5
ROW_H, ROW_GAP, LIST_Y = 50, 6, 70
MAX_QUEUE = 8


class PlexViews:
    def init_plex_state(self):
        self.px_on = False           # the engine has the card on (asked now and then, see px_refresh)
        self.px_asked = 0.0
        self.px = None

    # ---- is the card on
    def px_refresh(self, force=False):
        """Asks the engine, off the loop, at most once a minute: the Playing page shows the row only when it is on."""
        if not force and time.time() - self.px_asked < 60:
            return
        self.px_asked = time.time()

        def go():
            try:
                got = self.screen.engine._req("/api/plex", timeout=4)
                self.px_on = bool(got.get("on"))
                if self.px is not None:
                    self.px["cfg"] = got
            except Exception:
                self.px_on = False
        threading.Thread(target=go, daemon=True).start()

    # ---- the list
    def open_plex(self):
        self.px = {"cfg": None, "path": [], "items": [], "sel": 0, "first": 0, "queue": [], "off": True,
                   "busy": False, "error": None, "note": ("", 0.0), "setup": False, "url": "", "token": ""}
        self.go("plex")

        def go():
            try:
                got = self.screen.engine._req("/api/plex", timeout=4)
            except Exception:
                got = None
            px = self.px
            if px is None:
                return
            if not got:
                px["error"] = "engine"
                return
            px["cfg"] = got
            px["url"] = got.get("url") or ""
            px["queue"] = list(got.get("reward", {}).get("items") or [])
            px["off"] = got.get("reward", {}).get("off", True) is not False
            if got.get("url") and got.get("token"):
                self.px_browse("")
            else:
                px["setup"] = True                   # not connected yet: the connection rows first
        threading.Thread(target=go, daemon=True).start()

    def px_browse(self, key):
        px = self.px
        px.update(busy=True, error=None, items=[], sel=0, first=0)

        def go():
            from urllib.parse import quote
            try:
                got = self.screen.engine._req("/api/plex/browse?key=" + quote(key), timeout=10)
            except Exception:
                got = {"error": "engine"}
            if self.px is not px:
                return
            if got.get("error"):
                px.update(busy=False, error=got["error"])
                return
            items = got.get("items") or []
            last = px["path"][-1] if px["path"] else None
            if last and last.get("type") == "show":         # a series: the whole of it, from where it stopped
                items = [{"key": last["key"], "title": self.tr("px.whole"), "sub": last["title"], "type": "show",
                          "pick": True, "open": False, "whole": True}] + items
            px.update(busy=False, items=items)
        threading.Thread(target=go, daemon=True).start()

    def px_rows(self):
        """Not connected (or asked): the connection rows. Else the actions while the list holds something,
        then the library's rows, and at the libraries' level the connection as a last row."""
        px = self.px
        if px["setup"]:
            saved = bool((px["cfg"] or {}).get("token"))
            found = px.get("found") or []
            lk = px.get("link") or {}
            sub = self.tr("px.linkOld") if lk.get("state") == "expired" else self.tr("px.noServer") if lk.get("state") == "noserver" \
                else self.tr("px.linkD")
            return [{"act": "link", "title": self.tr("px.link"), "sub": sub},
                    {"act": "find", "title": self.tr("px.find"),
                     "sub": self.tr("px.finding") if px.get("finding") else self.tr("px.foundN", n=len(found)) if len(found) > 1 else self.tr("px.findD")},
                    {"act": "url", "title": self.tr("px.addr"), "sub": px["url"] or self.tr("px.addrD")},
                    {"act": "token", "title": self.tr("px.token"),
                     "sub": self.tr("px.tokTyped") if px["token"] else self.tr("px.tokSaved") if saved else self.tr("px.tokD")},
                    {"act": "connect", "title": self.tr("px.connect"), "sub": self.tr("px.connectD")}]
        acts = []
        if px["queue"]:
            n = len(px["queue"])
            rw = (px["cfg"] or {}).get("reward") or {}
            kept = rw.get("on") and [q["key"] for q in rw.get("items") or []] == [q["key"] for q in px["queue"]]
            acts = [{"act": "play", "title": self.tr("px.play", n=n), "sub": ", ".join(q["title"] for q in px["queue"])},
                    {"act": "reward", "title": self.tr("px.reward"), "sub": self.tr("px.kept") if kept else ""},
                    {"act": "off", "title": self.tr("px.offRow"), "sub": self.tr("px.offOn") if px["off"] else self.tr("px.offOff")},
                    {"act": "clear", "title": self.tr("px.clear"), "sub": ""}]
        tail = [{"act": "setup", "title": self.tr("px.conn"), "sub": px["url"]}] if not px["path"] and not px["busy"] else []
        return acts + px["items"] + tail

    def px_move(self, d):
        px = self.px
        n = len(self.px_rows())
        if not n:
            return
        px["sel"] = (px["sel"] + d) % n
        if px["sel"] < px["first"]:
            px["first"] = px["sel"]
        elif px["sel"] >= px["first"] + ROWS:
            px["first"] = px["sel"] - ROWS + 1

    def px_note(self, key, **kw):
        self.px["note"] = (self.tr(key, **kw), time.time())

    def px_ok(self, i=None):
        px = self.px
        rows = self.px_rows()
        if i is not None:
            px["sel"] = i
        if not rows or px["sel"] >= len(rows):
            return
        r = rows[px["sel"]]
        act = r.get("act")
        if act == "link":
            self.px_link()
        elif act == "find":
            found = px.get("found") or []
            if len(found) > 1 and px["url"] in [f["url"] for f in found]:
                k = [f["url"] for f in found].index(px["url"])          # ✓ again: the next server found
                px["url"] = found[(k + 1) % len(found)]["url"]
                self.px_note("px.found", name=found[(k + 1) % len(found)]["name"])
            else:
                self.px_find()
        elif act == "url":
            self.edit_field(self.tr("px.addr"), lambda: px["url"], lambda v: px.update(url=str(v or "").strip()),
                            phone=True, back="plex", note=self.tr("px.addrD"))
        elif act == "token":
            self.edit_field(self.tr("px.token"), lambda: px["token"], lambda v: px.update(token=str(v or "").strip()),
                            secret=True, reveal=True, phone=True, back="plex", note=self.tr("px.tokHow"))
        elif act == "connect":
            self.px_connect()
        elif act == "setup":
            px.update(setup=True, sel=0, first=0)
        elif act == "play":
            self.px_post("/plex/play", {"keys": [q["key"] for q in px["queue"]], "off": px["off"]}, "px.sent")
        elif act == "reward":
            rw = dict((px["cfg"] or {}).get("reward") or {})
            rw.update(on=True, items=px["queue"], off=px["off"])
            self.px_post("/plex/reward", rw, "px.saved")
        elif act == "off":
            px["off"] = not px["off"]
        elif act == "clear":
            px.update(queue=[], sel=0, first=0)
        elif r.get("open"):
            px["path"].append({"key": r["key"], "title": r["title"], "type": r.get("type")})
            self.px_browse(r["key"])
        elif r.get("pick"):
            if len(px["queue"]) >= MAX_QUEUE:
                self.px_note("px.full")
                return
            px["queue"].append({"key": r["key"], "title": r["sub"] if r.get("whole") else r["title"]})
            self.px_note("px.added", name=px["queue"][-1]["title"])

    def px_link(self):
        """Sign in with Plex: the engine asks plex.tv for a code and waits for it; this screen shows the code
        and the sign-in page's QR, and asks every 2 seconds how it went."""
        px = self.px
        if (px.get("link") or {}).get("state") == "waiting":
            return

        def go():
            try:
                got = self.screen.engine._req("/plex/link", {}, timeout=10)
            except Exception:
                got = {"ok": False}
            if self.px is not px:
                return
            if not got.get("ok"):
                self.px_note("px.tvFail")
                return
            px["link"] = got["link"]
            while self.px is px and (px.get("link") or {}).get("state") == "waiting":
                time.sleep(2)
                try:
                    c = self.screen.engine._req("/api/plex", timeout=4)
                except Exception:
                    continue
                if self.px is not px or (px.get("link") or {}).get("state") != "waiting":
                    return                          # left, or ☰ took the code off the screen
                lk = c.get("link") or {}
                if lk.get("state") == "waiting":
                    continue
                px["link"] = lk
                if lk.get("state") == "done":
                    px.update(cfg=c, url=c.get("url") or "", setup=False, token="", path=[], sel=0, first=0)
                    self.px_note("px.connected", name=lk.get("server") or c.get("url") or "")
                    self.px_browse("")
        threading.Thread(target=go, daemon=True).start()

    def px_find(self):
        """The servers on the network (GDM and mDNS, through the engine): the first one fills the address."""
        px = self.px
        if px.get("finding"):
            return
        px["finding"] = True

        def go():
            try:
                got = self.screen.engine._req("/api/plex/find", timeout=10).get("found") or []
            except Exception:
                got = []
            if self.px is not px:
                return
            px.update(finding=False, found=got)
            if got:
                px["url"] = got[0]["url"]
                self.px_note("px.found", name=got[0]["name"])
            else:
                self.px_note("px.notFound")
        threading.Thread(target=go, daemon=True).start()

    def px_connect(self):
        """The address and the token to the engine, which checks them against the server and keeps them."""
        px = self.px
        if not px["url"]:
            self.px_note("px.eAddr")
            return
        self.px_note("px.checking")

        def go():
            try:
                got = self.screen.engine._req("/plex/config", {"url": px["url"], "token": px["token"]}, timeout=10)
            except Exception:
                got = {"ok": False, "error": "engine"}
            if self.px is not px:
                return
            if got.get("ok"):
                px.update(setup=False, token="", url=got.get("url", px["url"]), path=[])
                if px["cfg"] is not None:
                    px["cfg"].update(url=px["url"], token=True)
                self.px_note("px.connected", name=got.get("name", ""))
                self.px_browse("")
            else:
                self.px_note({"address": "px.eAddr", "token": "px.eToken", "net": "px.eNet",
                              "engine": "noEngine"}.get(got.get("error"), "px.eServer"))
        threading.Thread(target=go, daemon=True).start()

    def px_post(self, path, body, ok_key):
        def go():
            try:
                got = self.screen.engine._req(path, body, timeout=6)
                if path == "/plex/reward" and self.px is not None and self.px.get("cfg") is not None:
                    self.px["cfg"]["reward"] = got.get("reward", body)
                self.px_note(ok_key if got.get("ok") else "px.failed")
            except Exception:
                self.px_note("px.failed")
        threading.Thread(target=go, daemon=True).start()

    def px_back(self):
        px = self.px
        if px and (px.get("link") or {}).get("state") == "waiting":
            px["link"] = None                          # the code off the screen; plex.tv lets it run out
            return
        if px and px["setup"] and (px["cfg"] or {}).get("token") and px["url"]:
            px.update(setup=False, sel=0, first=0)       # connected before: back to the library
            if not px["items"]:
                self.px_browse("")
        elif px and px["path"]:
            px["path"].pop()
            self.px_browse(px["path"][-1]["key"] if px["path"] else "")
        else:
            self.px = None
            self.go("settings")

    # ---- drawing
    def view_plex(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.35)
        px = self.px
        if px is None:
            self.go("settings")
            return
        where = tr("px.conn") if px["setup"] else " › ".join([tr("px.libs")] + [p["title"] for p in px["path"]])
        self.header(tr("px.title"), self.short(where, 380, 13), path=tr("settings"))
        lk = px.get("link") or {}
        if px["setup"] and lk.get("state") == "waiting":
            self.px_link_card(lk, t)
            return
        rows = self.px_rows()
        msg = None
        if px["error"] and not px["setup"]:
            msg = tr({"setup": "px.setup", "engine": "noEngine", "address": "px.eAddr", "token": "px.eToken",
                      "net": "px.eNet"}.get(px["error"], "px.eServer"))
        elif px["busy"]:
            msg = tr("px.loading")
        elif not rows:
            msg = tr("px.empty")
        if msg and not rows:
            for k, part in enumerate(cv.wrap(msg, W - 96, 16)[:4]):
                cv.text(part, W / 2, 150 + k * 28, 16, P["ink"] if k == 0 else P["dim"], k == 0, "center")
        first = max(0, min(px["first"], max(0, len(rows) - ROWS)))
        px["first"] = first
        for k, r in enumerate(rows[first:first + ROWS]):
            i = first + k
            y = LIST_Y + k * (ROW_H + ROW_GAP)
            sel = px["sel"] == i
            self.hit(24, y, W - 48, ROW_H, lambda i=i: self.px_ok(i))
            act = r.get("act")
            cv.rect(24, y, W - 48, ROW_H, P["panel"], r=10)
            if sel:
                cv.rect(24, y, W - 48, ROW_H, P["gold"] if act else P["sel"], r=10, width=2)
            mark = "▶" if act == "play" else "★" if act == "reward" else "✎" if act in ("url", "token") else "✓" if act == "connect" else "↻" if act == "find" else \
                "›" if r.get("open") or act == "setup" else "+" if r.get("pick") else ""
            room = W - 48 - 28 - 40
            title = r.get("title", "")
            if sel:
                cv.ticker(title, 38, y + 21, room, 16, P["ink"], t, True)
            else:
                cv.text(self.short(title, room, 16, True), 38, y + 21, 16, P["ink"], True)
            cv.text(self.short(r.get("sub") or "", room, 12), 38, y + 40, 12, P["dim"])
            if mark:
                cv.text(mark, W - 44, y + 31, 18, P["gold"] if act or sel else P["dim"], True, "right")
        note, at = px["note"]
        if note and time.time() - at < 2.5:
            w = cv.measure(note, 14, True) + 32
            cv.rect(W / 2 - w / 2, 362, w, 34, P["panel"], r=17)
            cv.text(note, W / 2, 384, 14, P["gold"], True, "center")
        cur = rows[px["sel"]] if rows and px["sel"] < len(rows) else None
        ok = tr("px.yes") if cur and cur.get("pick") and not cur.get("open") and not cur.get("act") else \
            tr("px.open") if cur and cur.get("open") else tr("px.do") if cur and cur.get("act") else ""
        many = len(rows) > 1
        self.bar([("☰", tr("back"), self.px_back),
                  ("▼", tr("down") if many else "", (lambda: self.px_move(1)) if many else None),
                  ("▲", tr("up") if many else "", (lambda: self.px_move(-1)) if many else None),
                  ("✓", ok, self.px_ok if cur else None)])

    def px_link_card(self, lk, t):
        """The code to enter at plex.tv/link, big, and the sign-in page's QR beside it (the code is filled in there)."""
        P, cv, tr = self.P, self.cv, self.tr
        cv.rect(24, 70, W - 48, 286, P["panel"], r=16)
        with cv.physical():
            cv.text(lk.get("code", ""), 230, 196, 64, P["gold"], True, "center")
            cv.text("plex.tv/link", 230, 242, 20, P["ink"], True, "center")
        for k, part in enumerate(cv.wrap(tr("px.linkGo"), 380, 14)[:2]):
            cv.text(part, W - 230 if cv.mirrored else 230, 282 + k * 20, 14, P["dim"], False, "center")
        dots = "." * (1 + int(t * 2) % 3)
        cv.text(tr("px.linkWait").rstrip("…") + dots, W - 230 if cv.mirrored else 230, 334, 13, P["dim"], False, "center")
        self.draw_qr(W - 24 - 30 - 230, 98, 230, lk.get("url", ""))
        self.bar([("☰", tr("back"), self.px_back), ("▼", "", None), ("▲", "", None), ("✓", "", None)])

