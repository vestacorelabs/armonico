"""views_more.py - backup and restore, the full diagnosis, the update, About and the credits.

Backup and restore go through device/backup.py: the screen picks the place and the file, and the
root unit armonico-device-backup.path does the work, writing its steps to a job file this screen
follows. The diagnosis and its fixes are device/diag.py's. About turns its pages with ▼▲ or a
swipe; ☰ and ✓ held together there roll the credits.
"""
import functools
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from .canvas import W, H
from .texts import ABOUT, CREDITS, steps as text_steps
from .widgets import BAR, RED, Job, alpha

try:
    import backup                                  # device/backup.py
except Exception:                                  # pragma: no cover
    backup = None

LESSON_UI_PORT = os.environ.get("LESSON_UI_PORT", "8099")
PROTO_PORT = {"smb": "445", "scp": "22", "ftp": "21", "url": "443"}
PROTO_HINT = {"smb": "//192.168.1.30/share", "scp": "192.168.1.30", "ftp": "192.168.1.30",
              "url": "https://example.com/armonico-backup.armonico"}
APP_DIR = Path(__file__).resolve().parent.parent.parent
UPDATE_LIMIT = 35 * 60          # the update unit's own limit is 30 minutes (system/update.service)


def size_text(n):
    if n >= 1048576:
        return f"{n / 1048576:.1f}MB"
    return f"{max(1, round(n / 1024))}KB"


class MoreViews:
    def init_more_state(self):
        self.bk = None
        self.bk_job = None                         # (job id, Job) of the backup unit's work
        self.lr_cache = (0.0, None)
        self.diag_from, self.diag_res, self.diag_t, self.diag_busy = "settings", None, 0.0, False
        self.about_page, self.about_anim = 0, (0, -9.0)
        self.credits_at = 0.0
        self.net_from = "rest"

    # ------------------------------------------------------------------ backup helpers
    def last_restore(self):
        at, val = self.lr_cache
        if time.time() - at > 5 and backup:
            try:
                val = backup.last_restore()
                if val and not val.get("at_hm") and val.get("at"):
                    val["at_hm"] = str(val["at"])[11:16]
            except Exception:
                val = None
            self.lr_cache = (time.time(), val)
        return val

    def backup_job(self, title, action, back, where=None, path="", file=""):
        """Hands the work to the backup unit and shows its steps as they come."""
        job = Job(title, ["…"], back)
        job.state = [1]
        try:
            jid = backup.request(action, where or {"kind": "local"}, path=path, file=file)
        except Exception as e:
            job.fail(e)
            jid = None
        self.bk_job = (jid, job, time.time())
        self.job = job
        self.go("job")
        return job

    def follow_backup_job(self):
        if not self.bk_job or not backup:
            return
        jid, job, started = self.bk_job
        if jid is None or job.done:
            self.bk_job = None
            return
        try:
            st = backup.job_status()
        except Exception:
            st = None
        if not st or st.get("id") != jid:
            req = getattr(getattr(backup, "Paths", None), "from_env", None)
            pending = req and req().request.exists()
            if pending and time.time() - started > 20:
                job.fail(self.tr("noRoot"))
            return
        k = "he" if self.lang == "he" else "en"
        stp = st.get("steps") or []
        if stp:
            job.labels = [s.get(k) or s.get("en", "") for s in stp]
            job.state = [2 if s.get("state") == "done" else 3 if s.get("state") == "failed" else 1 for s in stp]
        res = st.get("result") or {}
        if st.get("state") == "done":
            job.done, job.ok = True, True
            self.lr_cache = (0.0, None)
        elif st.get("state") == "failed":
            job.done, job.ok, job.error = True, False, res.get(k) or res.get("en", "")
            self.lr_cache = (0.0, None)

    def undo_restore(self):
        self.backup_job(self.tr("j.undo"), "undo", "settings")

    def restart_device(self):
        self.root_power("reboot")

    # ------------------------------------------------------------------ backup screens
    def open_bk(self, mode):
        if not backup:
            return
        self.bk = {"mode": mode, "step": "where", "where": None, "proto": "smb", "host": "", "port": "445", "user": "",
                   "pass": "", "url": "", "test": "untested", "path": "/", "root": "/", "entries": None, "err": "",
                   "file": None, "check": None, "sel": 0}
        self.go("bk")

    def bk_spec(self):
        b = self.bk
        if b["where"] in ("usb", "sd"):
            return {"kind": "local"}
        if b["proto"] == "url":
            return {"kind": "http", "url": b["url"], "user": b["user"], "password": b["pass"]}
        host, share = b["host"], ""
        m = re.match(r"^(?:\\\\|//)?([^/\\]+)[/\\]?([^/\\]*)", host)
        if b["proto"] == "smb" and m:
            host, share = m.group(1), m.group(2)
        return {"kind": b["proto"], "host": host, "port": b["port"], "user": b["user"], "password": b["pass"], "share": share}

    def bk_go(self, step):
        self.bk["step"], self.bk["sel"] = step, 0
        if step == "browse":
            self.bk_list()

    def bk_list(self):
        b = self.bk
        b["entries"], b["err"] = None, ""
        spec, path = self.bk_spec(), b["path"]

        def go():
            try:
                ents = backup.transport_from(spec).listdir(path)
                if b["path"] == path:
                    b["entries"] = list(ents)
            except Exception as e:
                b["entries"], b["err"] = [], getattr(e, "he" if self.lang == "he" else "en", str(e))
        threading.Thread(target=go, daemon=True).start()

    def bk_test(self):
        b = self.bk
        b["test"] = "testing"
        spec = self.bk_spec()

        def go():
            try:
                backup.transport_from(spec).test()
                b["test"] = "ok"
            except Exception:
                b["test"] = "fail"
        threading.Thread(target=go, daemon=True).start()

    def bk_join(self, name):
        p = self.bk["path"]
        return (p.rstrip("/") + "/" + name) if p else name

    def bk_check(self, remote):
        """The file is checked before anything changes: copied here when it is not local, then validated."""
        b = self.bk
        b["check"], b["file"] = None, remote
        spec = self.bk_spec()
        self.bk_go("preview")

        def go():
            tmp = None
            try:
                if spec["kind"] == "local":
                    chk = backup.validate(remote)
                else:
                    fd, tmp = tempfile.mkstemp(suffix=".armonico")
                    os.close(fd)
                    backup.transport_from(spec).get(remote, tmp)
                    chk = backup.validate(tmp)
            except Exception as e:
                chk = backup.Check(False, "error", str(getattr(e, "en", e)), str(getattr(e, "he", e)))
            finally:
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            b["check"] = chk
        threading.Thread(target=go, daemon=True).start()

    def bk_save(self):
        b = self.bk
        self.backup_job(self.tr("bk.save"), "create", "settings", self.bk_spec(), path=b["path"])

    def bk_restore(self):
        b = self.bk
        self.ask(self.tr("bk.restoreQ"),
                 lambda: self.backup_job(self.tr("bk.restore"), "restore", "settings", self.bk_spec(), file=b["file"]))

    def bk_where_name(self):
        b = self.bk
        if b["where"] == "net":
            return {"smb": "SMB", "scp": "SCP (SSH)", "ftp": "FTP", "url": self.tr("bk.url")}[b["proto"]]
        return {"usb": self.tr("bk.usb"), "sd": self.tr("bk.sd")}.get(b["where"], "")

    def bk_options(self):
        tr, b, on = self.tr, self.bk, self.online()
        save = b["mode"] == "save"
        intro, opts = "", []
        if b["step"] == "where":
            intro = tr("bk.whereSave") if save else tr("bk.whereRestore")
            sticks = backup.Local.sticks()
            usb = self.usb_seen()

            def local(where, path):
                def f():
                    b.update(where=where, path=path, root=path)
                    self.bk_go("browse")
                return f

            def net(p, sub):
                def f():
                    if on:
                        b.update(where="net", proto=p, port=PROTO_PORT[p], test="untested", path="/", root="/")
                        self.bk_go("net")
                return (self.bk_where_name_of(p), sub if on else tr("c.needsNet"), f)
            sd = str(backup.Paths.from_env().backups)
            if sticks:
                usb_opt = [(tr("bk.usb"), Path(sticks[0]).name + " · " + tr("bk.connected"), local("usb", sticks[0]))]
            elif any(d["usable"] for d in usb):
                usb_opt = [(tr("bk.usb"), tr("bk.mounting"), None)]
            elif usb:
                d = usb[0]
                usb_opt = [(tr("bk.usb"), (tr("bk.badFs", fs=d["fs"]) if d["fs"] else tr("bk.noFs")) + " · " + d["size_text"], None),
                           (tr("bk.exfat"), tr("bk.exfatD"), lambda d=d: self.usb_format(d, "exfat")),
                           (tr("bk.ext4"), tr("bk.ext4D"), lambda d=d: self.usb_format(d, "ext4"))]
            else:
                usb_opt = [(tr("bk.usb"), tr("bk.noUsb"), None)]
            opts = usb_opt + [
                (tr("bk.sdLong"), sd, local("sd", sd)),
                net("smb", tr("bk.smb")), net("scp", tr("bk.scp")), net("ftp", tr("bk.ftp"))]
            if not save:
                opts.append(net("url", tr("bk.urlD")))
        elif b["step"] == "net":
            p = b["proto"]

            def ed(label, key, secret=False, digits=False):
                def set_(v):
                    b[key] = v
                    b["test"] = "untested"
                # a password kept as typed, spaces too (the symbols are on the !#$ layer)
                return lambda: self.edit_field(label, lambda: b[key], set_, secret=secret, keep=secret, digits=digits)
            intro = tr("bk.urlI") if p == "url" else tr("bk.netI")
            if p == "url":
                opts = [(tr("bk.link"), b["url"] or PROTO_HINT["url"], ed(tr("bk.linkE"), "url"))]
            else:
                opts = [(tr("bk.server"), b["host"] or PROTO_HINT[p], ed(tr("bk.serverE"), "host")),
                        (tr("c.port"), b["port"], ed(tr("c.port"), "port", digits=True)),
                        (tr("c.user"), b["user"] or tr("empty"), ed(tr("c.userE"), "user")),
                        (tr("c.pass"), "●●●●●●" if b["pass"] else tr("empty"), ed(tr("c.pass"), "pass", True))]
            opts.append((tr("bk.test"), tr("bk." + b["test"]), self.bk_test))
            if b["test"] == "ok":
                if p == "url":
                    opts.append((tr("bk.checkFile"), "", lambda: self.bk_check(b["url"])))
                else:
                    opts.append((tr("bk.openFolders"), "", lambda: (b.update(path="/"), self.bk_go("browse"))))
        elif b["step"] == "browse":
            intro = tr("bk.browseSave") if save else tr("bk.browseRestore")
            if b["path"].rstrip("/") != b["root"].rstrip("/"):
                def up():
                    b["path"] = b["path"].rstrip("/").rsplit("/", 1)[0] or "/"
                    self.bk_go("browse")
                opts.append(("⬑ ..", tr("bk.up"), up))
            ents = b["entries"]
            if ents is None:
                opts.append((tr("bk.reading"), "", None))
            elif b["err"]:
                opts.append((tr("bk.readFail", e=b["err"]), "", None))
            else:
                for e in ents:
                    if e.is_dir:
                        opts.append(("▸ " + e.name, "", lambda e=e: (b.update(path=self.bk_join(e.name)), self.bk_go("browse"))))
                    elif not save:
                        when = datetime.fromtimestamp(e.mtime).strftime("%-d.%-m.%Y %H:%M") if e.mtime else ""
                        if e.name.endswith(".armonico"):
                            opts.append((e.name, f"{when} · {size_text(e.size)}", lambda e=e: self.bk_check(self.bk_join(e.name))))
                        else:
                            opts.append(("⚠ " + e.name, self.L("not an Armonico backup", "לא גיבוי של Armonico"),
                                         lambda e=e: self.bk_check(self.bk_join(e.name))))
                if not ents:
                    opts.append((tr("bk.emptyDir"), "", None))
            if save:
                opts.append((tr("bk.saveHere"), b["path"], self.bk_save))
        elif b["step"] == "preview":
            chk, name = b["check"], str(b["file"]).rsplit("/", 1)[-1]
            back = lambda: self.bk_go("net" if b["where"] == "net" and b["proto"] == "url" else "browse")
            if chk is None:
                intro = tr("bk.checkingFile")
                opts = [(name, "", None)]
            elif chk.ok:
                intro = tr("bk.fileOk")
                i = chk.info or {}
                kind = self.L("device", "מכשיר") if i.get("type") == "device" else self.L("regular install", "התקנה רגילה")
                inside = f"Armonico {i.get('version', '')} · {kind} · {i.get('profiles', 0)} " + \
                    self.L("profiles", "פרופילים") + f" · {i.get('songs', 0)} " + self.L("songs", "שירים")
                if i.get("broker"):
                    inside += " · " + (tr("local") if i["broker"] == "local" else tr("external"))
                opts = [(name, str(i.get("date", ""))[:16].replace("T", " "), None), (tr("bk.inside"), inside, None)]
                if chk.reason_key not in ("ok",):
                    opts.append((tr("bk.why"), chk.reason_he if self.lang == "he" else chk.reason_en, None))
                opts.append((tr("bk.restoreGo"), "", self.bk_restore))
            else:
                intro = tr("bk.fileBad")
                opts = [("⚠ " + name, "", None), (tr("bk.why"), chk.reason_he if self.lang == "he" else chk.reason_en, None),
                        (tr("bk.backFiles"), "", back)]
        return intro, opts

    def usb_seen(self):
        """USB storage, read again every 2 seconds while the backup screen shows. Pi OS Lite mounts
        nothing by itself, so a stick with a file system the Pi reads is opened here, once."""
        at, found = getattr(self, "_usb_seen", (0.0, []))
        if time.monotonic() - at > 2:
            try:
                found = backup.usb_drives() if backup else []
            except Exception:
                found = []
            self._usb_seen = (time.monotonic(), found)
            asked = getattr(self, "_usb_asked", set())
            self._usb_asked = asked
            for d in found:
                if d["usable"] and not d["mount"] and d["dev"] not in asked:
                    asked.add(d["dev"])
                    from . import system

                    def go(dev=d["dev"]):
                        ok, msg = system.request("usb-mount", dev=dev, timeout=90)
                        if not ok:
                            self.fail_toast(msg)
                        self._usb_seen = (0.0, [])
                    threading.Thread(target=go, daemon=True).start()
            gone = {d["dev"] for d in found}
            asked.intersection_update(gone)          # pulled out and put back in: opened again
        return found

    def usb_format(self, d, fs):
        from . import system
        name = "exFAT" if fs == "exfat" else "ext4"

        def work(job):
            ok, msg = system.request("usb-format", disk=d["disk"], fs=fs, on_step=lambda i, st: job.step(i), timeout=900)
            if not ok:
                job.fail(self.root_msg(msg))
            self._usb_seen = (0.0, [])
        self.ask(self.tr("bk.formatQ", name=d["name"], size=d["size_text"], fs=name),
                 lambda: self.start_job(self.tr("j.format"), text_steps(self.lang, "format"), "bk", work))

    def bk_where_name_of(self, p):
        return {"smb": "SMB", "scp": "SCP (SSH)", "ftp": "FTP", "url": self.tr("bk.url")}[p]

    def view_bk(self, t):
        P, cv, b, tr = self.P, self.cv, self.bk, self.tr
        if not b:
            self.go("settings")
            return
        self.hills(0.3)
        where = self.bk_where_name()
        self.header((tr("bk.save") if b["mode"] == "save" else tr("bk.restore")) + (" · " + where if where else ""),
                    cv.fit(b["path"], 300, 14, end=True) if b["step"] == "browse" else "")
        intro, opts = self.bk_options()
        lines = cv.wrap(intro, 752, 15)
        for i, line in enumerate(lines):
            cv.text(line, 24, 82 + i * 21, 15, P["ink"])
        top = 90 + len(lines) * 21
        lst = b["step"] in ("browse", "preview")
        two = not lst and len(opts) > 2
        w = 370 if two else 752
        h = (38 if len(opts) > 5 else 46) if lst else (44 if len(opts) > 6 else 52)
        gap = 5 if lst else 7
        can = [o[2] is not None for o in opts]
        if opts and not can[min(b["sel"], len(opts) - 1)] and any(can):
            b["sel"] = next(i for i, c in enumerate(can) if c)
        b["sel"] = min(b["sel"], max(0, len(opts) - 1))
        per = max(1, int((H - BAR - 6 - top) // (h + gap))) * (2 if two else 1)
        first = max(0, min(b["sel"] - per + 1, len(opts) - per))
        for j, (a, sub, fn) in enumerate(opts[first:first + per]):
            i = first + j
            x = 24 + ((j % 2) * 382 if two else 0)
            y = top + ((j // 2) if two else j) * (h + gap)
            self.hit(x, y, w, h, fn and (lambda i=i, fn=fn: (b.update(sel=i), fn())))
            cv.rect(x, y, w, h, P["panel"], r=10)
            framed = i == b["sel"] and fn is not None
            if framed:
                cv.rect(x, y, w, h, P["sel"], r=10, width=2.5)
            # as a settings card: the two lines grow with the interface size only as far as the card lets them
            zoom = cv.zoom
            cv.zoom = min(zoom, max(1.0, 1.25 * h / 64))
            try:
                warn = a.startswith("⚠")
                # a file or folder name keeps its end (…), where the date or the name's tail tells files apart;
                # every other text in a card is said shorter instead (short())
                name = b["step"] == "browse" and fn is not None and not a.startswith("⬑")
                fit = (lambda s, mw, size, bold=False: cv.fit(s, mw, size, bold, end=True)) if name else self.short
                if lst and sub:
                    cv.text(fit(a, w * 0.45, 14, True), x + 16, y + h / 2 + 5, 14, RED if warn else P["ink"], True)
                    cv.text((functools.partial(cv.fit, end=True) if sub.startswith("/") else self.short)(sub, w * 0.5, 11), x + w - 16, y + h / 2 + 4, 11, P["gold"] if framed else P["dim"], align="right")
                else:
                    cv.text(fit(a, w - 32, 15, True), x + 16, y + (h / 2 - 3 if sub else h / 2 + 5), 15,
                            RED if warn else P["ink"], True)
                    if sub:
                        sub_fit = functools.partial(cv.fit, end=True) if sub.startswith("/") else self.short    # a path keeps its end
                        cv.text(sub_fit(sub, w - 32, 11), x + 16, y + h / 2 + 14, 11, P["gold"] if framed else P["dim"])
            finally:
                cv.zoom = zoom
        if not two:
            self.list_marks(first, per, len(opts), top, h + gap, lambda d: b.update(sel=max(0, min(len(opts) - 1, b["sel"] + d))))

        def back():
            if b["step"] == "where":
                self.go("settings")
            elif b["step"] == "browse" and b["where"] == "net":
                self.bk_go("net")
            elif b["step"] == "preview":
                self.bk_go("net" if b["where"] == "net" and b["proto"] == "url" else "browse")
            else:
                self.bk_go("where")

        def move(d):
            if not any(can):
                return
            i = b["sel"]
            for _ in range(len(opts)):
                i = (i + d) % len(opts)
                if can[i]:
                    break
            b["sel"] = i
        cur = opts[b["sel"]] if opts else None
        # ✓ says what it does: a folder opens, a file is checked; never a name cut after 16 letters
        if not (cur and cur[2]):
            label = ""
        elif b["step"] == "browse" and cur[0].startswith("⬑"):
            label = tr("bk.upB")
        elif b["step"] == "browse" and cur[0].startswith("▸ "):
            label = tr("bk.openB")
        elif b["step"] == "browse" and cur[0] != tr("bk.saveHere"):
            label = tr("bk.checkB")
        else:
            label = re.sub(r" ›|▸ |⬑ |⚠ ", "", cur[0])
        self.bar([("☰", tr("back"), back), ("▼", tr("down"), lambda: move(1)), ("▲", tr("up"), lambda: move(-1)),
                  ("✓", label, (lambda: cur[2]()) if cur and cur[2] else None)])

    # ------------------------------------------------------------------ the full diagnosis
    def open_diag(self, back):
        self.diag_from = back
        self.go("diag")
        self.run_diag()

    def run_diag(self):
        if self.diag_busy:
            return
        self.diag_busy, self.diag_res, self.diag_err = True, None, ""

        def go():
            try:
                import diag
                res = diag.run_checks()
            except Exception as e:
                # the checks did not run: said so, not "All fine" over 0 checks
                self.diag_res, self.diag_err = [], str(e) or type(e).__name__
                self.diag_t, self.diag_busy = self.t(), False
                return
            self.diag_res, self.checks = res, res
            self.diag_t, self.diag_busy = self.t(), False
        threading.Thread(target=go, daemon=True).start()

    def diag_fix(self):
        import diag
        bad = next((c for c in (self.diag_res or []) if c.level == 2 and c.fix in getattr(diag, "FIXES", {})), None)
        if not bad:
            self.run_diag()
            return
        k = 1 if self.lang == "he" else 0
        labels = [p[k] for p in diag.FIXES[bad.fix]]

        def work(job):
            res = diag.fix(bad.fix, progress=job.step)
            if not res or not all(s.get("ok") for s in res):
                job.fail(self.L("not fixed", "לא תוקן"))
        job = self.start_job(self.tr("j.fix"), labels, "diag", work)
        job.after = self.run_diag

    def view_diag(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.3)
        res = self.diag_res
        he = self.lang == "he"
        if res is None:
            self.header(tr("diag"), tr("checking"))
            for i in range(12):
                x, y = 24 + (i % 2) * 382, 60 + (i // 2) * 60
                cv.rect(x, y, 370, 54, P["panel"], r=12)
                self.icon(self.spinner(t + i * 0.1), x + 22, y + 33, 17, P["dim"])
            self.bar([("☰", tr("back"), lambda: self.go(self.diag_from)), ("▼", "", None), ("▲", "", None), ("✓", tr("checking"), None)])
            return
        err = getattr(self, "diag_err", "")
        if err and not res:
            self.header(tr("diag"), tr("dg.cantRun"))
            cv.rect(24, 64, W - 48, 150, P["panel"], r=14)
            cv.rect(24, 64, W - 48, 150, RED, r=14, width=2.5)
            self.icon("✕", 52, 104, 20, RED)
            cv.text(tr("dg.cantRunT"), 80, 104, 17, P["ink"], True)
            for i, line in enumerate(cv.wrap(err, W - 140, 14)[:3]):
                cv.text(line, 80, 138 + i * 22, 14, P["dim"])
            self.bar([("☰", tr("back"), lambda: self.go(self.diag_from)), ("▼", "", None), ("▲", "", None),
                      ("✓", tr("dg.again"), self.run_diag)])
            return
        k = (t - self.diag_t) / 0.18
        done = k >= len(res)
        nbad = sum(1 for c in res if c.level == 2)
        nwarn = sum(1 for c in res if c.level == 1)
        right = (tr("dg.problem1") if nbad == 1 else tr("dg.problems", n=nbad)) if nbad else \
            (tr("dg.note1") if nwarn == 1 else tr("dg.notes", n=nwarn)) if nwarn else tr("c.allFine")
        self.header(tr("diag"), right if done else tr("checking"))
        rows = max(1, (len(res) + 1) // 2)
        pitch = min(60.0, (H - BAR - 64) / rows)
        hh = pitch - 5
        for i, c in enumerate(res):
            x, y = 24 + (i % 2) * 382, 58 + (i // 2) * pitch
            shown = k > i
            cv.rect(x, y, 370, hh, P["panel"], r=10)
            if shown and c.level == 2:
                cv.rect(x, y, 370, hh, RED, r=10, width=2.5)
            ic = self.spinner(t) if not shown else "✓⚠✕"[c.level]
            col = P["dim"] if not shown else (P["ok"], P["gold"], RED)[c.level]
            self.icon(ic, x + 22, y + hh / 2 + 6, 17 if hh > 40 else 14, col)
            label = c.label_he if he else c.label_en
            detail = c.detail_he if he else c.detail_en
            if hh >= 44:
                cv.text(cv.fit(label, 310, 13, True), x + 42, y + hh * 0.4, 13, P["ink"], True)
                if shown:
                    cv.ticker(detail, x + 42, y + hh * 0.78, 315, 12, P["dim"], t)
            else:
                lw = cv.measure(label, 12, True)
                cv.text(label, x + 42, y + hh / 2 + 4, 12, P["ink"], True)
                if shown:
                    cv.ticker(detail, x + 42 + lw + 10, y + hh / 2 + 4, max(40, 310 - lw - 10), 11, P["dim"], t)
        try:
            import diag
        except Exception:
            diag = None
        fixable = any(c.level == 2 and c.fix in getattr(diag, "FIXES", {}) for c in res)
        self.bar([("☰", tr("back"), lambda: self.go(self.diag_from)), ("▼", "", None), ("▲", "", None),
                  ("✓", tr("dg.fix") if done and fixable else tr("dg.again"),
                   self.diag_fix if done and fixable else self.run_diag)])

    # ------------------------------------------------------------------ the update
    def release_lines(self):
        notes = (self.update or ("", ""))[1] or ""
        out = []
        for line in notes.splitlines():
            line = re.sub(r"^[#>\s]*[-*•]?\s*", "", line).replace("**", "").replace("`", "").strip()
            if line and not line.lower().startswith(("full changelog", "http")):
                out.append(line)
        return out

    def start_update(self):
        v = self.update[0]
        s = self.screen
        if s.lesson_on():
            self.toast(self.tr("up.busy"))
            return
        labels = text_steps(self.lang, "update", v=v)

        def work(job):
            job.step(0)
            if backup:
                jid = backup.request("create", {"kind": "local"})
                end = time.time() + 900
                while time.time() < end:
                    st = backup.job_status()
                    if st and st.get("id") == jid and st.get("state") in ("done", "failed"):
                        if st["state"] == "failed":
                            r = st.get("result") or {}
                            job.fail(r.get("he" if self.lang == "he" else "en", ""))
                            return
                        break
                    time.sleep(0.5)
                else:
                    job.fail(self.tr("noRoot"))
                    return
            job.step(1)
            asked = time.time()
            try:
                s.engine._req("/update", {}, timeout=30)
            except Exception as e:
                job.fail(e)
                return
            self.follow_update(job, asked)
        self.start_job(self.tr("j.update"), labels, "rest", work)

    def follow_update(self, job, asked, limit=UPDATE_LIMIT, req_wait=60, every=1.0):
        """setup/update.sh's status file (update-status.json), followed until the installer ends. On
        success it restarts the services, this screen with them; a failure is shown at once. It slept
        an hour here before, so a failed installer left "Working…" on the screen for an hour."""
        data = Path(os.environ.get("DATA_DIR", "/var/lib/armonico"))
        status, req = data / "update-status.json", data / "update-request"
        start = time.monotonic()
        while time.monotonic() - start < limit:
            try:
                st = json.loads(status.read_text())
            except (OSError, ValueError):
                st = None
            if isinstance(st, dict) and float(st.get("time") or 0) >= asked - 2:
                state, msg = st.get("state"), str(st.get("message") or "")
                if state == "failed":
                    job.fail(self.tr("up.failed", m=msg))
                    return
                if state == "done":
                    job.step(3)
                    return
                if msg.startswith("Installing"):
                    job.step(2)
            elif req.exists() and time.monotonic() - start > req_wait:
                job.fail(self.tr("up.noUnit"))       # nobody took the request: the update unit is not there
                return
            time.sleep(every)
        job.fail(self.tr("up.stuck"))

    def open_upd(self, back):
        """The update's screen; Later goes back where it was opened (it always went to Rest)."""
        self.upd_from = back
        self.go("upd")

    def view_upd(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.3)
        if not self.update:
            self.go("settings")
            return
        v = self.update[0]
        self.header(tr("upd"), f"{self.version()} → {v}")
        cv.rect(24, 64, W - 48, 330, P["panel"], r=14)
        cv.text(tr("up.new", v=v), 44, 96, 16, P["gold"], True)
        y = 130
        lines = self.release_lines() or [tr("up.none")]
        for line in lines:
            for part in cv.wrap("· " + line, W - 96, 15):
                if y > 330:
                    break
                cv.text(part, 44, y, 15, P["ink"])
                y += 26
        cv.text(cv.fit(tr("up.keep"), W - 96, 14), 44, 370, 14, P["dim"])
        self.bar([("☰", tr("later"), lambda: self.go(getattr(self, "upd_from", "rest"))), ("▼", "", None), ("▲", "", None),
                  ("✓", tr("c.update"), lambda: self.ask(tr("up.q", v=v), self.start_update))])

    # ------------------------------------------------------------------ About and the credits
    def lesson_url(self):
        """The address of the lesson screen in a browser (what the QR codes open), or "" with no address."""
        return f"http://{self.ip}:{LESSON_UI_PORT}" if self.ip else ""

    def open_qr(self):
        self.go("qr")

    def view_qr(self, t):
        """Settings › Network and links › Lesson screen in a browser: its QR code, large, with the address."""
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.35)
        self.header(tr("c.name"), "")
        url = self.lesson_url()
        if url and self.draw_qr(W / 2 - 150, 64, 300, url):
            cv.text(f"{self.ip}:{LESSON_UI_PORT}", W / 2, 402, 20, P["ink"], True, "center")
        else:
            cv.text(tr("c.noNet"), W / 2, 230, 20, P["ink"], True, "center")
        back = lambda: self.go("settings")
        self.bar([("☰", tr("back"), back), ("▼", "", None), ("▲", "", None), ("✓", tr("back"), back)])

    def open_about(self):
        self.about_page = 0
        self.go("about")

    def about_turn(self, d):
        """▼ ▲ and a swipe: a page whose text is longer than its box scrolls first, then the next page comes."""
        lim = getattr(self, "about_lim", 0)
        sc = getattr(self, "about_scroll", 0)
        if 0 <= sc + d <= lim and lim:
            self.about_scroll = sc + d
            return
        self.about_scroll = 0
        n = len(ABOUT[self.lang])
        self.about_page = (self.about_page + d) % n
        self.about_anim = (1 if d > 0 else -1, self.t())

    def about_drag(self, d):
        """A drag up or down on a long page: it scrolls the text and stops at its ends; the pages turn with ▼ ▲
        and a sideways swipe."""
        lim = getattr(self, "about_lim", 0)
        self.about_scroll = max(0, min(lim, getattr(self, "about_scroll", 0) + d))

    def ver_tap(self):
        t = self.t()
        self.ver_taps = [q for q in self.ver_taps if t - q < 3] + [t]
        left = 7 - len(self.ver_taps)
        if left <= 0:
            self.char.cool_until = t + 20
            self.ver_taps = []
            self.toast(self.tr("dev"), 3)
        elif left <= 3:
            self.toast(self.tr("more1") if left == 1 else self.tr("more", n=left), 1.2)

    def vcl_tap(self):
        """Ten touches on the Vesta Core Labs logo in About, each within 1.5 s of the one before: the screen
        recording's card shows in Settings › System until the next restart. It is never hidden from here."""
        t = self.t()
        taps = self.vcl_taps if self.vcl_taps and t - self.vcl_taps[-1] < 1.5 else []
        self.vcl_taps = taps + [t]
        left = 10 - len(self.vcl_taps)
        if left <= 0:
            self.vcl_taps = []
            self.rec_shown = True
            self.toast(self.tr("recShown"), 3)
        elif left <= 3 and not self.rec_shown:
            self.toast(self.tr("more1") if left == 1 else self.tr("more", n=left), 1.2)

    def soak_tap(self):
        """Seven touches on the Armonico logo in About, each within 1.5 s of the one before: the load test's card
        shows in Settings › System › Tests until the next restart (soak_card_on)."""
        t = self.t()
        taps = self.soak_taps if self.soak_taps and t - self.soak_taps[-1] < 1.5 else []
        self.soak_taps = taps + [t]
        left = 7 - len(self.soak_taps)
        if left <= 0:
            self.soak_taps = []
            self.soak_shown = True
            self.toast(self.tr("soakShown"), 3)
        elif left <= 3 and not self.soak_shown:
            self.toast(self.tr("more1") if left == 1 else self.tr("more", n=left), 1.2)

    def open_credits(self):
        self.credits_at = self.t()
        self.go("credits")

    def view_about(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.35)
        pages = ABOUT[self.lang]
        n = len(pages)
        self.about_page %= n
        title, paras = pages[self.about_page]
        ip = self.ip or self.L("no network", "אין רשת")
        paras = [p.format(ip=ip, port=LESSON_UI_PORT) for p in paras]
        self.header(tr("about"), f"{self.about_page + 1} / {n}")
        # the Armonico logo on the reading side (right in Hebrew), the Vesta Core Labs logo on the other,
        # each in its colors for a dark or a light background (vcl-day is vcl-light with its pale left letters darkened: they faded into a daytime sky); the name, the line and the version centered
        light_bg = sum(P["ink"][:3]) < 384          # dark letters: the background is light
        with cv.physical():
            icon_x = W - 24 - 92 if self.lang == "he" else 24
            vcl_x = 24 if self.lang == "he" else W - 24 - 150
            self.logo("armonico-icon-light" if light_bg else "armonico-icon-dark", icon_x, 70, 92, 92)
            self.logo("vcl-day" if light_bg else "vcl-dark", vcl_x, 98, 150, 40)
            cv.text("Armonico Pi", W / 2, 98, 30, P["ink"], True, "center")
            cv.text(tr("tagline"), W / 2, 126, 18, P["gold"], True, "center")
            cv.text(tr("version", v=self.version()), W / 2, 152, 15, P["dim"], align="center")
        self.hit(W / 2 - 130, 134, 260, 26, self.ver_tap)
        self.hit(W - 24 - 150, 92, 150, 52, self.vcl_tap)      # the Vesta Core Labs logo (vcl_tap), mirrored with it
        self.hit(24, 70, 92, 92, self.soak_tap)                  # the Armonico logo (soak_tap), mirrored with it
        x, y, w, h = 24, 176, W - 48, 232
        cv.rect(x, y, w, h, P["panel"], r=14)
        cv.text(title, x + 20, y + 28, 15, P["gold"], True)
        d, at = self.about_anim
        ak = min(1.0, (t - at) / 0.3)
        off = (1 - ak) ** 2 * 140 * d
        cv.save()
        cv.translate(off, 0)
        # the lines are wrapped to the box and a page that is longer than the box scrolls (▼ ▲, a drag); the line
        # pitch grows with the interface size, so lines never touch
        pitch = 21 * max(1.0, cv.zoom)
        lines = []
        for p in paras:
            ls = cv.wrap(p, w - 40 - 14, 15)
            lines += [(ln, pitch) for ln in ls[:-1]] + [(ls[-1] if ls else "", pitch + 7)]
        room = y + h - 24 - (y + 56) + pitch * 0.5
        vis = max(1, int((room - 4) // pitch))
        self.about_lim = max(0, len(lines) - vis)
        self.about_scroll = min(getattr(self, "about_scroll", 0), self.about_lim)
        ly = y + 56
        for ln, step in lines[self.about_scroll:self.about_scroll + vis]:
            cv.text(ln, x + 20, ly, 15, alpha(P["ink"], 0.3 + 0.7 * ak))
            ly += step
        cv.restore()
        if self.about_lim:
            self.scroll_bar(y + 40, h - 70, self.about_scroll, vis, len(lines), x=W - 36)
            self.scroll_zone(x, y + 40, w, h - 70, pitch, lambda d: self.about_drag(d))
        for i in range(n):
            cv.circle(W / 2 + (i - (n - 1) / 2) * 16, y + h - 14, 4.5 if i == self.about_page else 3.5,
                      P["gold"] if i == self.about_page else (128, 128, 128, 102))
        self.bar([("☰", tr("settings"), lambda: self.go("settings")), ("▼", tr("down"), lambda: self.about_turn(1)),
                  ("▲", tr("up"), lambda: self.about_turn(-1)), ("✓", tr("back"), lambda: self.go("settings"))])

    LOGOS = Path(__file__).resolve().parent / "data" / "logos"

    def logo(self, name, x, y, w, h):
        """A logo (device/ui/data/logos) fitted into a plan box, its proportions kept, centered in it.
        Scaled once for each size and kept."""
        cv = self.cv
        cache = getattr(self, "_logo_cache", None)
        if cache is None:
            cache = self._logo_cache = {}
        src = cache.get(name)
        if src is None:
            try:
                src = cache[name] = self.pg.image.load(str(self.LOGOS / f"{name}.png")).convert_alpha()
            except Exception:
                cache[name] = False
                return
        if src is False:
            return
        iw, ih = src.get_size()
        k = min(w / iw, h / ih)
        pw, ph = max(1, int(round(iw * k * cv.scale))), max(1, int(round(ih * k * cv.scale)))
        img = cache.get((name, pw, ph))
        if img is None:
            img = cache[(name, pw, ph)] = self.pg.transform.smoothscale(src, (pw, ph))
        px, py = cv.P(x + (w - iw * k) / 2, y + (h - ih * k) / 2)
        cv.surf.blit(img, (int(round(px)), int(round(py))))

    def view_credits(self, t):
        cv = self.cv
        cv.fill((5, 7, 13))
        k = t - self.credits_at
        y = H - BAR + 20 - k * 38
        with cv.physical():
            for line, size in CREDITS[self.lang]:
                if size and -30 < y < H - BAR - 2:
                    cv.text(line, W / 2, y, size, (255, 255, 255, 140) if size == 13 else (255, 255, 255), size > 13, "center")
                y += (40 if size >= 20 else 30) if size else 26
        if y < -40:
            self.go("about")
        P = dict(self.P, panel=(255, 255, 255, 20), ink=(255, 255, 255), bar=None)
        keep, self.P = self.P, P
        self.bar([("☰", self.tr("exit"), lambda: self.go("about"))])
        self.P = keep
