"""views_deep.py - the deep check in the device test window: Settings › System › Device test › Deep check.

The engine is device/deepcheck.py (the same one `armonico check --deep` runs). Here: an opening screen, the
measuring with its progress (in a thread, the drawing never waits), and the result in groups, scrolled with
▼ ▲: the summary row, the services table, then the groups. ✓ copies the report files to a USB stick (root:
report-usb); the report is also kept on the device in checks/ the moment it is made.
"""
import threading
from pathlib import Path

from .canvas import W, H
from .widgets import BAR, RED, Widgets
from .texts import T
from .texts_b import TEXTS

T.update(TEXTS)
Widgets.SCREEN_HOME = {**Widgets.SCREEN_HOME, "deep": ("test", "dc.t")}

try:
    import deepcheck                                 # device/deepcheck.py
except Exception:                                    # pragma: no cover - the screen runs without it
    deepcheck = None

OK, NOTE, PROBLEM, SKIP = 0, 1, 2, 3
STEP = 3                    # lines one ▼ or ▲ moves
COLS = (190, 78, 96, 96, 92, 96, 66)        # name, state, CPU average, CPU now, memory, writes, restarts
GAP = 4


class DeepViews:
    def init_deep_state(self):
        self.dc = None

    # ------------------------------------------------------------------ opening and leaving
    def open_deep(self):
        self.dc = {"phase": "intro", "gen": 0, "frac": 0.0, "step": "", "rep": None, "lines": None, "first": 0, "saved": None,
                   "saving": False}
        self.go("deep")

    def dc_back(self):
        if self.dc:
            self.dc["gen"] += 1                       # a measuring still running is dropped
        self.go("settings")

    # ------------------------------------------------------------------ measuring
    def dc_start(self):
        d = self.dc
        if not deepcheck or d["phase"] == "run":
            return
        d["gen"] += 1
        gen = d["gen"]
        d.update(phase="run", frac=0.0, step="services", rep=None, lines=None, first=0, saved=None)

        def progress(frac, step):
            if d["gen"] == gen:
                d["frac"], d["step"] = frac, step

        def go():
            try:
                rep = deepcheck.collect(progress=progress)
            except Exception as e:                    # never a crash: back to the opening with a message
                if d["gen"] == gen:
                    d["phase"] = "intro"
                    self.toast(self.tr("testNotSent", e=str(e)[:80]), 4)
                return
            if d["gen"] != gen:
                return
            try:
                d["file"] = deepcheck.save_report(rep, self.lang)
            except OSError:
                d["file"] = None
            d["rep"], d["phase"] = rep, "result"
        threading.Thread(target=go, daemon=True, name="deep-check").start()

    def dc_save(self):
        d = self.dc
        f = d.get("file")
        if not f or d["saving"]:
            return
        d["saving"] = True
        names = [f.name, f.with_suffix(".json").name]

        def go():
            try:
                from . import system
                ok, msg = system.request("report-usb", timeout=90, files=names)
                import json
                res = json.loads(msg) if ok else {}
                if ok and res.get("copied"):
                    self.toast(self.tr("dc.savedUsb", to=Path(res.get("to", "")).name), 4)
                elif ok:
                    self.toast(self.tr("dc.noUsb", f="checks/" + f.name), 5)
                else:
                    self.toast(self.tr("dc.failed"), 4)
            except Exception:
                self.toast(self.tr("dc.failed"), 4)
            finally:
                d["saving"] = False
        self.toast(self.tr("dc.saving"), 2)
        threading.Thread(target=go, daemon=True, name="deep-save").start()

    # ------------------------------------------------------------------ the lines of the result
    def dc_lines(self):
        """[(kind, height, data)]: summary, table header and rows, group titles and rows. Worked out once."""
        d = self.dc
        key = (self.lang, id(d["rep"]))
        if d["lines"] and d["lines"][0] == key:
            return d["lines"][1]
        rep, lang = d["rep"], self.lang
        D = deepcheck
        out = [("sum", 58, D.summary(rep, lang)), ("gap", 8, None)]
        tab = D.service_table(rep, lang)
        lv = D.table_levels(rep)
        out.append(("thead", 26, tab[0]))
        for r, lvl in zip(tab[1:], lv):
            out.append(("tr", 24, (r, lvl)))
        out.append(("gap", 10, None))
        for g in rep["groups"]:
            if g["key"] == "services":
                continue
            out.append(("grp", 30, D.tx("g." + g["key"], lang)))
            for r in g["rows"]:
                out.append(("row", 24, (D.cell(r["label"], lang), D.cell(r["value"], lang), r["level"])))
            out.append(("gap", 6, None))
        d["lines"] = (key, out)
        return out

    # ------------------------------------------------------------------ drawing
    def view_deep(self, t):
        d = self.dc
        if not d or not deepcheck:
            self.go("settings")
            return
        self.hills(0.3)
        getattr(self, "dc_" + d["phase"] + "_draw")(t)

    def dc_intro_draw(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        cv.text(tr("dc.t"), W / 2, 170, 30, P["ink"], True, "center")
        y = 214
        for para in (tr("dc.i1"), tr("dc.i2")):
            for line in cv.wrap(para, 600, 16):
                cv.text(line, W / 2, y, 16, P["ink"], align="center")
                y += 25
            y += 8
        self.header(tr("dc.t"))
        self.bar([("☰", tr("back"), self.dc_back), ("▼", "", None), ("▲", "", None), ("✓", tr("chk.start"), self.dc_start)])

    def dc_run_draw(self, t):
        P, cv, tr, d = self.P, self.cv, self.tr, self.dc
        self.header(tr("dc.t"))
        step = d["step"]
        n = deepcheck.LIVE_SECONDS
        if step == "live":
            sec = max(0, min(n, round((d["frac"] - 0.05) / 0.75 * n)))
            msg = tr("dc.p.live", s=sec, n=n)
        else:
            msg = tr("dc.p." + step)
        cv.text(tr("dc.t"), W / 2, 190, 28, P["ink"], True, "center")
        self.icon(self.spinner(t), W / 2, 250, 24, P["gold"])
        cv.text(cv.fit(msg, W - 120, 17), W / 2, 300, 17, P["ink"], align="center")
        cv.rect(120, 326, W - 240, 8, P["panel"], r=4)
        cv.rect(120, 326, max(8, (W - 240) * d["frac"]), 8, P["ok"], r=4)
        self.bar([("☰", tr("chk.stop"), self.dc_back), ("▼", "", None), ("▲", "", None), ("✓", "", None)])

    def dc_result_draw(self, t):
        P, cv, tr, d = self.P, self.cv, self.tr, self.dc
        D = deepcheck
        rep = d["rep"]
        n = rep["counts"]
        self.header(tr("dc.t"), tr("dc.sumLine", p=n[PROBLEM], w=n[NOTE]))
        lines = self.dc_lines()
        top, bottom = 62, H - BAR - 6
        first = min(d["first"], max(0, len(lines) - 1))
        col = {OK: P["ok"], NOTE: P["gold"], PROBLEM: RED, SKIP: P["dim"]}
        y = top
        shown = 0
        for kind, h, data in lines[first:]:
            if y + h > bottom and shown:
                break
            shown += 1
            if kind == "sum":
                w = (W - 48 - 3 * 8) / 4
                for i, (label, val) in enumerate(data):
                    x = 24 + i * (w + 8)
                    cv.rect(x, y, w, h, P["panel"], r=10)
                    cv.text(cv.fit(label, w - 16, 12), x + 10, y + 19, 12, P["dim"])
                    cv.text(cv.fit(val, w - 16, 17, True), x + 10, y + 44, 17, P["ink"], True)
            elif kind == "thead":
                x = 36
                for c, label in zip(COLS, data):
                    cv.text(cv.fit(label, c - GAP, 12), x, y + 17, 12, P["dim"], True)
                    x += c
            elif kind == "tr":
                row, lvl = data
                cv.rect(24, y, W - 48, h - 3, P["panel"], r=6)
                x = 36
                for i, (c, cell) in enumerate(zip(COLS, row)):
                    color = col[lvl] if i == 1 else P["ink"]
                    cv.text(cv.fit(cell, c - GAP, 13, i == 0), x, y + 17, 13, color, i == 0)
                    x += c
            elif kind == "grp":
                cv.text(cv.fit(data, W - 60, 15, True), 28, y + 22, 15, P["gold"], True)
            elif kind == "row":
                label, val, lvl = data
                half = (W - 72) * 0.42
                cv.text(cv.fit(label, half, 13), 36, y + 17, 13, P["dim"])
                cv.text(cv.fit(val, W - 72 - half - 10, 13, True), W - 36, y + 17, 13, col[lvl] if lvl != OK else P["ink"], True, "right")
            y += h
        more = first + shown < len(lines)

        def move(k):
            d["first"] = max(0, min(len(lines) - 1, d["first"] + k))
        self.bar([("☰", tr("back"), self.dc_back),
                  ("▼", tr("down") if more else "", (lambda: move(STEP)) if more else None),
                  ("▲", tr("up") if first > 0 else "", (lambda: move(-STEP)) if first > 0 else None),
                  ("✓", tr("dc.save") if d.get("file") else "", self.dc_save if d.get("file") else None)])
