"""views_songnet.py - Search online, from the Songs screen: a song found in the free archives is
downloaded into the songs.

The lesson engine does the searching and the downloading (lessons/piano_game.py, /api/search and
/songs/fetch, the same ones the lesson web screen uses): only the archives whose files may be kept,
each song saved with its credit. The screen only asks the engine over its local HTTP API, always off
the drawing thread: the drawing reads the state a worker leaves in self.sn.
"""
import json
import threading
import urllib.error
import urllib.parse

from .canvas import W, H
from .widgets import BAR, RED

ROWS = 5                    # results shown at once; ▼▲ scroll the rest
ROW_H, ROW_GAP, LIST_Y = 52, 6, 112
SEARCH_TIMEOUT = 200        # the engine asks up to 4 Mutopia pages, 3 single words and Commons twice, 20 s each at worst
FETCH_TIMEOUT = 60


class EngineDown(Exception):
    """The lesson engine itself did not answer (not the archives)."""


def call(request, path, body=None, timeout=10):
    """(status, answer) from the engine: 200 and its JSON, or the HTTP code and the JSON it sent with it.
    EngineDown when nothing answered on the device itself."""
    try:
        return 200, request(path, body, timeout)
    except urllib.error.HTTPError as e:
        try:
            ans = json.loads(e.read() or b"{}")
        except (ValueError, OSError):
            ans = {}
        return e.code, ans if isinstance(ans, dict) else {}
    except (OSError, ValueError) as e:
        raise EngineDown(str(e)) from e


def search(request, query):
    """{"search", "results", "failed"} from the engine, or {"error": "net" | "engine"}."""
    try:
        code, ans = call(request, "/api/search?q=" + urllib.parse.quote(query), None, SEARCH_TIMEOUT)
    except EngineDown:
        return {"error": "engine"}
    if code != 200 or not isinstance(ans, dict):
        return {"error": "engine"}
    if ans.get("error"):                       # no archive answered: the internet is not there
        return {"error": "net"}
    results = [r for r in ans.get("results") or [] if isinstance(r, dict) and "id" in r]
    return {"search": str(ans.get("search") or ""), "results": results, "failed": list(ans.get("failed") or [])}


def fetch(request, token, rid):
    """("ok" | "nolesson" | "expired" | "failed" | "engine", the engine's answer)."""
    try:
        code, ans = call(request, "/songs/fetch", {"id": rid, "search": token}, FETCH_TIMEOUT)
    except EngineDown:
        return "engine", {}
    if code == 404:
        return "expired", ans                  # the engine keeps the last few searches only
    if code != 200:
        return "failed", ans
    return ("ok" if ans.get("ok", True) is not False else "nolesson"), ans


def same_result(a, b):
    return all(str(a.get(k, "")) == str(b.get(k, "")) for k in ("name", "source", "license"))


class SongNetViews:
    def sn_state(self):
        sn = self.__dict__.get("sn")
        if sn is None:
            sn = self.sn = {"query": "", "token": "", "results": [], "failed": [], "busy": False, "error": "",
                            "sel": 0, "first": 0, "rows": {}, "gen": 0, "added": None}
        return sn

    def sn_spawn(self, fn):
        """Network work on its own thread: the screen never waits for it (tests run it in place)."""
        threading.Thread(target=fn, daemon=True).start()

    def sn_request(self, path, body=None, timeout=10):
        return self.screen.engine._req(path, body, timeout)

    # ------------------------------------------------------------------ opening and searching
    def open_songnet(self):
        sn = self.sn_state()
        self.go("songnet")
        if not sn["query"] and not sn["busy"]:
            self.sn_type()                          # nothing searched yet: straight to the keyboard

    def sn_type(self):
        sn = self.sn_state()
        self.edit_field(self.tr("sn.title"), lambda: sn["query"], self.sn_search, ok=self.tr("sn.find"),
                        back="songnet", kb="he" if self.lang == "he" else "en")

    def sn_search(self, query):
        sn = self.sn_state()
        query = " ".join(str(query or "").split())[:80]
        if not query:
            return
        sn["gen"] += 1
        gen = sn["gen"]
        sn.update(query=query, token="", results=[], failed=[], error="", sel=0, first=0, rows={})
        if not self.online():
            sn.update(busy=False, error="net")
            return
        sn["busy"] = True

        def work():
            ans = search(self.sn_request, query)
            if sn["gen"] != gen:
                return                              # a newer search started meanwhile
            if ans.get("error"):
                sn.update(busy=False, error=ans["error"])
            else:
                sn.update(busy=False, token=ans["search"], results=ans["results"], failed=ans["failed"],
                          error="" if ans["results"] else "none", sel=1 if ans["results"] else 0)
            self.layer_dirty()
        self.sn_spawn(work)

    # ------------------------------------------------------------------ downloading
    def sn_have(self, r):
        return any(self.same_song(s.get("name"), r.get("name")) for s in self.screen.songs())

    def sn_download(self, i):
        sn = self.sn_state()
        if not (0 <= i < len(sn["results"])) or sn["rows"].get(i) in ("busy", "ok"):
            return
        r = sn["results"][i]
        if self.sn_have(r):
            return
        sn["rows"][i] = "busy"
        gen, token = sn["gen"], sn["token"]

        def work():
            state, ans = fetch(self.sn_request, token, r["id"])
            if state == "expired":                  # pushed out by other searches: the same search again
                again = search(self.sn_request, sn["query"])
                hit = next((x for x in again.get("results") or [] if same_result(x, r)), None)
                if hit is not None:
                    if sn["gen"] == gen:
                        sn["token"] = again["search"]
                    state, ans = fetch(self.sn_request, again["search"], hit["id"])
                else:
                    state = "failed"
            if sn["gen"] != gen:
                return
            sn["rows"][i] = {"ok": "ok", "nolesson": "nolesson"}.get(state, "failed")
            if state in ("ok", "nolesson"):
                sn["added"] = ans.get("name") or r["name"]
                try:                                # the new song in the list now, not at the next look
                    ov = self.sn_request("/api/overview", None, 8)
                    if isinstance(ov, dict) and "songs" in ov:
                        self.screen.engine.overview = ov
                except Exception:
                    pass
            many = state == "ok" and isinstance(ans.get("count"), int) and ans["count"] > 1
            self.toast(self.tr("sn.addedN", n=ans["count"]) if many else
                       self.tr({"ok": "sn.added", "nolesson": "sn.noLesson", "engine": "noEngine"}.get(state, "sn.dlFailMsg")))
            self.layer_dirty()
        self.sn_spawn(work)

    # ------------------------------------------------------------------ moving
    def sn_move(self, d):
        sn = self.sn_state()
        n = len(sn["results"]) + 1                 # 0 is the search field
        sn["sel"] = (sn["sel"] + d) % n
        i = sn["sel"] - 1
        if i >= 0:
            if i < sn["first"]:
                sn["first"] = i
            elif i >= sn["first"] + ROWS:
                sn["first"] = i - ROWS + 1

    def sn_ok(self):
        sn = self.sn_state()
        if sn["sel"] == 0:
            self.sn_type()
        else:
            self.sn_download(sn["sel"] - 1)

    def sn_back(self):
        sn = self.sn_state()
        if sn.get("added"):                         # back in Songs on the song just added
            songs = self.screen.songs()
            at = next((k for k, s in enumerate(songs) if self.same_song(s.get("name"), sn["added"])), None)
            if at is not None:
                self.screen.sel_song = at
                self.sn_entry = False
            sn["added"] = None
        self.go("songs")

    def sn_ok_label(self):
        sn = self.sn_state()
        if sn["sel"] == 0:
            return self.tr("sn.type"), self.sn_type
        i = sn["sel"] - 1
        row = sn["rows"].get(i)
        if row in ("busy", "ok", "nolesson") or self.sn_have(sn["results"][i]):
            return "", None
        return self.tr("sn.dl"), lambda: self.sn_download(i)

    # ------------------------------------------------------------------ the screen
    def view_songnet(self, t):
        P, cv = self.P, self.cv
        sn = self.sn_state()
        self.hills(0.35)
        res = sn["results"]
        if sn["sel"] > len(res):
            sn["sel"] = 0
        first = max(0, min(sn["first"], max(0, len(res) - ROWS)))
        sn["first"] = first
        shown = res[first:first + ROWS]
        count = self.tr("sn.count", a=first + 1, b=first + len(shown), n=len(res)) if len(res) > ROWS else ""
        self.header(self.tr("sn.title"), count, path=self.tr("songs"))
        # the search field: a touch, or ✓ on it, opens the keyboard
        self.hit(24, 62, W - 48, 40, self.sn_type)
        cv.rect(24, 62, W - 48, 40, P["panel"], r=10)
        if sn["sel"] == 0:
            cv.rect(24, 62, W - 48, 40, P["sel"], r=10, width=2)
        note = self.tr("sn.searching") if sn["busy"] else \
            self.tr("sn.partial", a=", ".join(sn["failed"])) if sn["failed"] and res else ""
        note_w = cv.measure(note, 13) + 16 if note else 0
        cv.text(self.short(sn["query"] or self.tr("sn.field"), W - 96 - note_w, 17, True), 40, 89, 17,
                P["ink"] if sn["query"] else P["dim"], True)
        if note:
            cv.text(note, W - 40, 88, 13, P["gold"] if sn["busy"] else P["dim"], align="right")
        if sn["error"] and not sn["busy"]:
            head, sub = {"net": ("sn.noNet", "sn.noNetSub"), "engine": ("noEngine", "sn.engineSub"),
                         "none": ("sn.none", "sn.noneSub")}[sn["error"]]
            cv.text(self.short(self.tr(head, q=sn["query"]), W - 48, 18, True), 24, LIST_Y + 34, 18, P["ink"], True)
            cv.text(self.short(self.tr(sub), W - 48, 14), 24, LIST_Y + 62, 14, P["dim"])
        for k, r in enumerate(shown):
            i = first + k
            y = LIST_Y + k * (ROW_H + ROW_GAP)
            sel = sn["sel"] == i + 1
            self.hit(24, y, W - 48, ROW_H, lambda i=i: (sn.update(sel=i + 1), self.sn_download(i)))
            cv.rect(24, y, W - 48, ROW_H, P["panel"], r=10)
            if sel:
                cv.rect(24, y, W - 48, ROW_H, P["sel"], r=10, width=2)
            row = sn["rows"].get(i)
            if row == "busy":
                act, col = self.tr("sn.downloading"), P["gold"]
            elif row == "ok":
                act, col = self.tr("sn.added"), P["ok"]
            elif row == "nolesson":
                act, col = self.tr("sn.noLessonShort"), P["dim"]
            elif self.sn_have(r):
                act, col = self.tr("sn.have"), P["dim"]
            elif row == "failed":
                act, col = self.tr("sn.dlFail"), RED
            elif sel:
                act, col = "✓ " + self.tr("sn.dl"), P["gold"]
            else:
                act, col = "", P["dim"]
            act_w = (cv.measure(act, 13, True) + 20) if act else 0
            room = W - 48 - 28 - act_w
            if sel:
                cv.ticker(r.get("name", ""), 38, y + 22, room, 16, P["ink"], t, True)
            else:
                cv.text(self.short(r.get("name", ""), room, 16, True), 38, y + 22, 16, P["ink"], True)
            meta = " · ".join(x for x in (r.get("source"), r.get("license")) if x)
            cv.text(self.short(meta, room, 12), 38, y + 42, 12, P["dim"])
            if act:
                cv.text(act, W - 38, y + 32, 13, col, True, "right")
        ok_label, ok_fn = self.sn_ok_label()
        many = len(res) > 0
        self.bar([("☰", self.tr("back"), self.sn_back),
                  ("▼", self.tr("down") if many else "", (lambda: self.sn_move(1)) if many else None),
                  ("▲", self.tr("up") if many else "", (lambda: self.sn_move(-1)) if many else None),
                  ("✓", ok_label, ok_fn)])

    # ------------------------------------------------------------------ the entry on the Songs screen
    def sn_entry_card(self, x, y, w, h, framed):
        """The Search online card that Songs shows first when there are no songs at all."""
        P, cv = self.P, self.cv
        self.hit(x, y, w, h, self.open_songnet)
        cv.rect(x, y, w, h, P["panel"], r=14)
        if framed:
            cv.rect(x, y, w, h, P["sel"], r=14, width=2)
        cv.text(self.short(self.tr("sn.title"), w - 40, 19, True), x + 20, y + 28, 19, P["ink"], True)
        cv.text(self.short(self.tr("sn.entrySub"), w - 40, 13), x + 20, y + 49, 13, P["dim"])

    def sn_entry_chip(self, framed):
        """Search online at the top of a Songs screen that has songs, just after the title: a touch opens it,
        and ▲ from the first song (or ▼ from the last) frames it for ✓."""
        P, cv = self.P, self.cv
        label = self.tr("sn.title") + " ›"
        w = cv.measure(label, 14, True) + 28
        x = 22 + cv.measure(self.tr("songs"), 20, True) + 22
        self.hit(x, 12, w, 32, self.open_songnet)
        cv.rect(x, 12, w, 32, P["panel"], r=10)
        if framed:
            cv.rect(x, 12, w, 32, P["sel"], r=10, width=2)
        cv.text(label, x + w / 2, 33, 14, P["gold"] if framed else P["ink"], True, "center")
        return x + w

    def imp_entry_chip(self, x, framed):
        """Songs from USB, just after Search online: MIDI files on a stick (views_extra.py, open_import)."""
        P, cv = self.P, self.cv
        label = self.tr("c.import") + " ›"
        w = cv.measure(label, 14, True) + 28
        self.hit(x, 12, w, 32, self.open_import)
        cv.rect(x, 12, w, 32, P["panel"], r=10)
        if framed:
            cv.rect(x, 12, w, 32, P["sel"], r=10, width=2)
        cv.text(label, x + w / 2, 33, 14, P["gold"] if framed else P["ink"], True, "center")

    def imp_entry_card(self, x, y, w, h, framed):
        """Songs from USB under Search online, when there are no songs at all."""
        P, cv = self.P, self.cv
        self.hit(x, y, w, h, self.open_import)
        cv.rect(x, y, w, h, P["panel"], r=14)
        if framed:
            cv.rect(x, y, w, h, P["sel"], r=14, width=2)
        cv.text(self.short(self.tr("c.import"), w - 40, 19, True), x + 20, y + 28, 19, P["ink"], True)
        cv.text(self.short(self.tr("c.importV"), w - 40, 13), x + 20, y + 49, 13, P["dim"])

    def songs_step(self, d):
        """▼▲ on Songs: through the songs, with Songs from USB and Search online as two more stops between the last
        and the first (▲ from the first song: Search online, then Songs from USB, then the last song)."""
        s = self.screen
        n = len(s.songs())
        if getattr(self, "sn_entry", False):
            self.sn_entry = False
            if d < 0:
                self.imp_entry = True
            else:
                s.sel_song = 0
                s.move(0)
            return
        if getattr(self, "imp_entry", False):
            self.imp_entry = False
            if d > 0:
                self.sn_entry = True
            else:
                s.sel_song = max(0, n - 1)
                s.move(0)
            return
        cur = s.sel_song % n if n else 0
        if d > 0 and cur == n - 1:
            self.imp_entry = True
            return
        if d < 0 and cur == 0:
            self.sn_entry = True
            return
        s.move(d)
