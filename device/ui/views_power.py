"""views_power.py - the screen after a power loss, and what keeps the record it works from.

Armonico Pi's boot unit (system/boot-mark.service, device/powerloss.py) notes a boot that follows a
cut power in <data>/device/unclean.json. The first view of the screen program is then this one, before
the normal screens: what was open at the last moment, and then, with no key pressed, one line for each
repair or check, each waiting, running or done:

  file system   what the boot's own check did (root: boot-check)
  settings      each file parsed; one broken is put back from its last good copy (the boot unit already did
                this before the services started; the screen says what it did and looks again)
  learning      the profiles and the learning data, the same
  recording     a screen recording the power cut is put together from its saved parts, or cleaned up
  songs         every MIDI file read; a damaged one is moved aside
  services      the device's services started and looked at (root: service-start)
  sound, keys   a short self test (the full check's areas)

Skipping is offered once the file system and the settings are done; the rest goes on in the background.
At the end the screen goes on to the normal one, or stays and says what was not repaired. An alarm that
was ringing when the power went does not ring again: the day's ring is marked as done before anything else.

The same file keeps the record: last.json, written when the view, the lesson or the profile changes
(powerloss.LastWriter), and the last good copies of the settings (powerloss.snapshot, every 10 minutes
and at a clean shutdown).
"""
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .canvas import W, H
from .widgets import BAR, RED
from .texts import T
from .texts_b import TEXTS

T.update(TEXTS)

try:
    import powerloss                                 # device/powerloss.py
except Exception:                                    # pragma: no cover - the screen runs without it
    powerloss = None
try:
    import fullcheck                                 # device/fullcheck.py
except Exception:                                    # pragma: no cover
    fullcheck = None

OK, NOTE, PROBLEM, SKIP = 0, 1, 2, 3
PACE = 0.6              # seconds a step shows as running, so each line can be read
SNAP_EVERY = 600        # seconds between two refreshes of the last good copies
DONE_HOLD = 1.5         # seconds the finished screen stays when everything is fine
STEPS = ("fs", "cfg", "learn", "rec", "songs", "svc", "snd")
SKIP_AFTER = 2          # skipping is offered once this many steps (file system, settings) are done


def data_dir():
    return Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))


class PowerViews:
    def init_power_state(self):
        self.pl = None
        self.pl_last = powerloss.LastWriter() if powerloss else None
        self.pl_seen = 0.0
        self.pl_snap_at = time.monotonic()

    # ------------------------------------------------------------------ the record
    def pl_profile(self):
        ov = getattr(self.screen.engine, "overview", None) or {}
        profs = ov.get("profiles") if isinstance(ov, dict) else None
        if isinstance(profs, list):
            for p in profs:
                if isinstance(p, dict) and p.get("active"):
                    return str(p.get("name") or "")
        return ""

    def pl_track(self):
        """Called every frame: cheap, writes only on a change (powerloss.LastWriter) and refreshes the last good
        copies of the settings every SNAP_EVERY seconds."""
        w = self.pl_last
        if w is None or not self.background_on:
            return
        now = time.monotonic()
        if now - self.pl_seen < 0.5:
            return
        self.pl_seen = now
        if self.view == "power":
            return
        lesson = ""
        if self.screen.lesson_on():
            lesson = str(self.screen.engine.state.get("song") or "")
        w.note(self.view, lesson, self.pl_profile())
        if now - self.pl_snap_at > SNAP_EVERY and not (self.pl and self.pl["phase"] == "run"):
            self.pl_snap_at = now
            threading.Thread(target=self.pl_snapshot, daemon=True, name="power-snapshot").start()

    def pl_snapshot(self):
        try:
            powerloss.snapshot()
        except Exception:
            pass

    # ------------------------------------------------------------------ starting
    def pl_start(self):
        """True when the last shutdown was a power loss and the recovery screen is now the one to show."""
        if powerloss is None or not self.background_on:
            return False
        info = powerloss.pending()
        if not info or not self.st.get("setup_done"):
            return False
        return self.pl_begin(info)

    def pl_begin(self, info, pace=PACE):
        self.pl = {"info": info, "phase": "run", "gen": 0, "pace": pace, "t0": time.monotonic(), "end": None,
                   "rows": [{"key": k, "state": "wait", "level": OK, "text": ("pl.w." + k, {})} for k in STEPS],
                   "left": False, "unfixed": [], "restored": False}
        self.pl_hold_alarm()
        threading.Thread(target=self.pl_work, daemon=True, name="power-recovery").start()
        self.go("power")
        return True

    def pl_hold_alarm(self):
        """An alarm that was ringing when the power went must not ring again by itself: today's ring is marked as
        done (the same mark follow_alarm keeps), and the broker's retained request is cleared."""
        try:
            a = self.alarm()
            if a.get("on") and a.get("song"):
                local = self.local()
                h, m = (int(x) for x in a["time"].split(":"))
                if local.hour * 60 + local.minute >= h * 60 + m:
                    self.save(alarm_last=f"{local.strftime('%Y-%m-%d')} {a['time']}")
        except Exception:
            pass
        try:
            self.screen.pub("piano/alarm/set", "", retain=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ the work, off the drawing thread
    def pl_work(self):
        p = self.pl
        for i, key in enumerate(STEPS):
            row = p["rows"][i]
            row["state"] = "now"
            t = time.monotonic()
            try:
                level, text = getattr(self, "pl_step_" + key)()
            except Exception as e:                     # never a crash: one line that could not be checked
                level, text = SKIP, ("pl.r.failed", {"e": (str(e) or type(e).__name__)[:80]})
            rest = p["pace"] - (time.monotonic() - t)
            if rest > 0:
                time.sleep(rest)
            row.update(state="done", level=level, text=text)
        if powerloss:
            powerloss.clear_pending()
        p["end"] = time.monotonic()                # before the phase: the drawing thread reads end once it sees done
        p["phase"] = "done"

    def pl_step_fs(self):
        from . import system
        ok, msg = system.request("boot-check", timeout=40)
        if not ok:
            return NOTE, ("pl.r.fsUnknown", {})
        try:
            import json
            d = json.loads(msg)
        except ValueError:
            return NOTE, ("pl.r.fsUnknown", {})
        return {"clean": (OK, ("pl.r.fsClean", {})), "repaired": (OK, ("pl.r.fsRepaired", {})),
                "errors": (PROBLEM, ("pl.r.fsErrors", {}))}.get(d.get("fsck"), (NOTE, ("pl.r.fsUnknown", {})))

    def pl_files(self, group, ok_key, restored_key, lost_key):
        res = powerloss.repair(group)
        if any(x["state"] != "ok" for x in res):
            self.pl["restored"] = True             # put right only now: the lesson engine reads its files again
        # what the boot unit already put right, before the services started (powerloss.mark_boot)
        early = [x for x in ((self.pl.get("info") or {}).get("repaired") or {}).get(group, []) if isinstance(x, dict)]
        seen = {x.get("path") for x in early}
        res = early + [x for x in res if x["path"] not in seen]
        n = len(res)
        r = sum(1 for x in res if x["state"] == "restored")
        lost = sum(1 for x in res if x["state"] in ("removed", "failed"))
        if lost:
            return PROBLEM, (lost_key, {"x": lost, "n": n})
        if r:
            return NOTE, (restored_key, {"n": n - r, "r": r})
        return OK, (ok_key, {"n": n})

    def pl_step_cfg(self):
        return self.pl_files("config", "pl.r.cfgOk", "pl.r.cfgRestored", "pl.r.cfgLost")

    def pl_step_learn(self):
        return self.pl_files("learning", "pl.r.learnOk", "pl.r.learnRestored", "pl.r.learnLost")

    def pl_step_rec(self):
        folder = data_dir() / "device" / "recordings"
        from . import recorder
        res = powerloss.recover_recordings(folder, recorder.mux_cmd, recorder.start_of)
        if not res:
            return OK, ("pl.r.recNone", {})
        if any(x["state"] == "failed" for x in res):
            return PROBLEM, ("pl.r.recFailed", {})
        secs = sum(x["seconds"] for x in res)
        if secs > 0:
            return OK, ("pl.r.recDone", {"m": max(1, round(secs / 60))})
        return OK, ("pl.r.recCleaned", {})

    def pl_step_songs(self):
        folder = Path(os.environ.get("SONGS_DIR") or data_dir() / "songs")
        n, bad = powerloss.check_songs(folder)
        if bad:
            return NOTE, ("pl.r.songsBad", {"b": len(bad), "n": n})
        return (OK, ("pl.r.songsOk", {"n": n})) if n else (OK, ("pl.r.songsNone", {}))

    def pl_step_svc(self):
        import subprocess
        units = ("armonico-bridge", "armonico-lessons", "armonico-device", "mosquitto")

        def state(u):
            try:
                r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=10)
                return r.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return ""

        def enabled(u):
            try:
                return subprocess.run(["systemctl", "is-enabled", "--quiet", u], timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
        have = [u for u in units if enabled(u)]
        down = [u for u in have if state(u) != "active"]
        again = ["armonico-lessons"] if self.pl["restored"] and "armonico-lessons" in have else []
        if again and self.pl["left"]:
            # skipped, and a lesson started meanwhile: the engine is restarted after it, never in the middle
            until = time.monotonic() + 1800
            while self.screen.lesson_on() and time.monotonic() < until:
                time.sleep(2)
        if down or again:
            from . import system
            system.request("service-start", timeout=120, units=down, restart=again)
            down = [u for u in have if state(u) != "active"]
        if down:
            return PROBLEM, ("pl.r.svcBad", {"x": ", ".join(down)})
        return OK, ("pl.r.svcOk", {})

    def pl_step_snd(self):
        F = fullcheck
        if not F:
            return SKIP, ("pl.r.failed", {"e": "-"})
        from . import system
        cfg = F.diag.config()
        ctx = {"settings": dict(self.st), "online": self.online(), "keyboards": list(getattr(self, "keyboards", []) or []),
               "volume": self.volume_now(), "request": lambda a, timeout=10: system.request(a, timeout=timeout)}
        res = F.run_all(cfg, ctx, only=["sound", "samples", "gpio", "midi"])
        items = [i for _, r in res for i in r]
        worst = max([i.level for i in items if i.level != F.SKIP] or [F.OK])
        if worst == F.OK:
            return OK, ("pl.r.sndOk", {})
        bad = next(i for i in items if i.level == worst)
        return (PROBLEM if worst == F.PROBLEM else NOTE), ("raw", {"pair": (bad.label[0] + ": " + bad.row[0], bad.label[1] + ": " + bad.row[1])})

    # ------------------------------------------------------------------ drawing
    def pl_text(self, text):
        key, kw = text
        if key == "raw":
            return kw["pair"][1 if self.lang == "he" else 0]
        return self.tr(key, **kw)

    def pl_last_line(self):
        info = (self.pl or {}).get("info") or {}
        last = info.get("last") or {}
        if not last:
            return self.tr("pl.lastNone")
        parts = []
        if last.get("lesson"):
            parts.append(self.tr("pl.w.lesson", song=str(last["lesson"]).replace("_", " ").capitalize()))
        elif last.get("view"):
            name = self.tr(last["view"])
            if name != last["view"]:
                parts.append(name)
        if last.get("profile"):
            parts.append(self.tr("pl.w.profile", name=last["profile"]))
        try:
            ts = datetime.fromtimestamp(float(last["ts"]), self.local().tzinfo)
            parts.append(f"{ts.day}.{ts.month}.{ts.year} {ts:%H:%M}")
        except Exception:
            pass
        return self.tr("pl.last", what=", ".join(parts)) if parts else self.tr("pl.lastNone")

    def pl_leave(self):
        p = self.pl
        if p and not p["left"]:
            p["left"] = True
            self.go("rest")

    def view_power(self, t):
        p = self.pl
        if not p or p["left"]:
            self.go("rest")
            return
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.3)
        done = p["phase"] == "done"
        n_done = sum(1 for r in p["rows"] if r["state"] == "done")
        bad = [r for r in p["rows"] if r["state"] == "done" and r["level"] == PROBLEM]
        title = tr("pl.notFixed") if done and bad else tr("pl.title")
        cv.text(cv.fit(title, W - 48, 26, True), W / 2, 62, 26, RED if done and bad else P["ink"], True, "center")
        y = 86
        for line in cv.wrap(self.pl_last_line(), W - 80, 14)[:2]:
            cv.text(line, W / 2, y, 14, P["dim"], align="center")
            y += 20
        sub = tr("pl.working") if not done else (tr("pl.allFixed") if not bad else "")
        if sub:
            cv.text(cv.fit(sub, W - 80, 14), W / 2, y, 14, P["ink"], align="center")
        top = 140
        bottom = H - BAR - 44
        pitch = min(36.0, (bottom - top) / len(p["rows"]))
        for i, r in enumerate(p["rows"]):
            yy = top + i * pitch
            cv.rect(24, yy, W - 48, pitch - 5, P["panel"], r=8)
            mid = yy + (pitch - 5) / 2 + 5
            if r["state"] == "wait":
                cv.text("○", 44, mid, 14, P["dim"], True, "center")
                cv.text(cv.fit(tr("pl.w." + r["key"]), W - 110, 14), 64, mid, 14, P["dim"])
                continue
            if r["state"] == "now":
                self.icon(self.spinner(t + i * 0.1), 44, mid, 14, P["gold"])
                cv.text(cv.fit(tr("pl.w." + r["key"]), W - 110, 14, True), 64, mid, 14, P["ink"], True)
                continue
            mark, col = {OK: ("✓", P["ok"]), NOTE: ("!", P["gold"]), PROBLEM: ("✕", RED), SKIP: ("·", P["dim"])}[r["level"]]
            cv.text(mark, 44, mid, 15, col, True, "center")
            cv.text(cv.fit(self.pl_text(r["text"]), W - 110, 14), 64, mid, 14, P["ink"] if r["level"] != OK else P["dim"])
        by = H - BAR - 30
        cv.rect(24, by, W - 48, 6, P["panel"], r=3)
        cv.rect(24, by, max(6, (W - 48) * n_done / len(p["rows"])), 6, P["ok"] if not bad else RED, r=3)
        if done and not bad and time.monotonic() - p["end"] > DONE_HOLD:
            self.pl_leave()
        skip_ok = n_done >= SKIP_AFTER and not done
        if done:
            ok_item = ("✓", tr("pl.go"), self.pl_leave)
        elif skip_ok:
            ok_item = ("✓", tr("pl.skip"), self.pl_leave)
        else:
            ok_item = ("✓", "", None)
        self.bar([("☰", "", lambda: None), ("▼", "", None), ("▲", "", None), ok_item])
