"""views_setup.py - the setup on the device itself, with no computer after the one install command.

It opens by itself on the first start after the install, and later from System. The steps follow
the choices: a local broker is installed and tested right there; an external one is found by mDNS,
filled in and tested, then Home Assistant and Telegram can follow, or be skipped. The list of steps
is worked out again on every move, so a changed choice never leaves the wizard on a stale step.
The step in progress is kept in settings.json: a broker switch restarts the services, this screen
with them, and the setup comes back where it was.
"""
import os
import threading
import time
from datetime import datetime, timezone

from .canvas import W, H
from .texts import steps as text_steps
from .widgets import Job

RADIO = ("lang", "conn")       # one touch picks and moves on; the sound step stays, so its tone is heard there
AUTO_LAST = ("place", "time", "look", "done", "local")
LESSON_UI_PORT = os.environ.get("LESSON_UI_PORT", "8099")


class SetupViews:
    def init_setup_state(self):
        self.setup_step, self.setup_sel, self.setup_from = "lang", -1, "settings"
        self.setup_job = None

    def setup_steps(self):
        b = self.broker["type"]
        return ["lang"] + (["wifi"] if self.setup_wifi_step() else []) + ["place", "time", "sound", "conn"] + \
            (["local"] if b == "local" else ["broker", "ha", "tg"]) + ["look", "done"]

    def setup_wifi_step(self):
        """The Wi-Fi step only when no network cable has an address. Looked at once for each opening of
        the setup (a quick look at /sys), so the steps do not change under it while it is open."""
        v = getattr(self, "setup_wifi", None)
        if v is None:
            from . import system
            try:
                v = not system.wired_up()
            except Exception:
                v = True
            self.setup_wifi = v
        return v

    def open_setup(self, step="lang", start=False, single=False):
        if self.view not in ("setup", "edit", "place", "job", "wifi"):
            self.setup_from = "rest" if start or self.view != "settings" else "settings"
            self.setup_wifi = None                   # the cable looked at again for this setup
        if step == "wifi" and "wifi" not in self.setup_steps():
            step = "place"                           # a cable came meanwhile: the step after it
        self.setup_step = step if step in self.setup_steps() else "lang"
        self.setup_sel = -1
        # single: one step opened from the settings (Place › Time); Continue or Back returns there
        self.setup_single = single
        if self.setup_step == "local":
            self.start_local_step()
        self.go("setup")

    def setup_go(self, d):
        ss = self.setup_steps()
        here = self.setup_step if self.setup_step in ss else "lang"
        if here == "time":
            # forward sets the clock moved by hand; Back leaves it as it was (it stayed moved until a restart)
            if d > 0:
                self.apply_time()
            else:
                self.time_adj = 0.0
        if here == "broker" and d > 0 and (os.environ.get("MQTT_LOCAL", "no") == "yes" or self.broker["dirty"]):
            def switch():
                # the switch restarts the services and this screen: the setup comes back on Home Assistant
                self.setup_step = "ha"
                self.save(setup_step="ha")
                self.switch_external(back="setup")
            self.ask(self.tr("c.applyQ"), switch)    # asked first, as Apply in the settings asks
            return
        if getattr(self, "setup_single", False):
            self.setup_single = False
            self.go(self.setup_from)
            return
        i = ss.index(here) + d
        if i < 0:
            # left before the first step: a setup that was done once does not open again at the next start
            if self.st.get("setup_done"):
                self.save(setup_step=None)
            self.go(self.setup_from)
            return
        if i >= len(ss):
            return
        self.setup_step, self.setup_sel = ss[i], -1
        self.save(setup_step=self.setup_step)
        if self.setup_step == "local":
            self.start_local_step()

    def finish_setup(self):
        first = not self.st.get("setup_done")
        self.save(setup_done=True, setup_step=None)
        if first:
            self.tour_first_setup()                  # the opening tour after the first setup only (tour.py)
        self.setup_step = "lang"
        self.go(self.setup_from)                     # back where it was opened (the settings, or Rest)

    def apply_time(self):
        """The clock moved by hand, set on the machine (only offline: online, the network keeps it)."""
        if not self.time_adj or self.online():
            self.time_adj = 0.0
            return
        # in UTC: the screen counts in the city's zone, the machine may still be in another one
        value = datetime.fromtimestamp(self.now_ms() / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        from . import system

        def go():
            ok, msg = system.request("set-time", value=value, timeout=60)
            if ok:
                self.time_adj = 0.0
            else:
                self.fail_toast(msg)
        threading.Thread(target=go, daemon=True).start()

    def start_local_step(self):
        if self.setup_job and not (self.setup_job.done and not self.setup_job.ok):
            return
        if os.environ.get("MQTT_LOCAL", "no") == "yes":
            # on the local broker (already, or just switched and started again): this screen tests it
            # itself, and Continue opens only when the test passed
            job = Job(self.tr("j.local"), text_steps(self.lang, "local"), "setup")

            def work(j):
                from . import system
                j.step(3)
                self.broker["state"] = "testing"
                res = "fail"
                for i in range(3):                   # right after a switch the broker may still be starting
                    res, ms = system.mqtt_test("127.0.0.1", os.environ.get("MQTT_PORT", "1883"),
                                               os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", ""))
                    if res != "fail":
                        break
                    time.sleep(1)
                self.broker.update(state={"ok": "ok", "login": "login"}.get(res, "fail"), ms=ms if res == "ok" else 0)
                self.broker["used"] = (self.broker["state"], self.broker["ms"])
                if res != "ok":
                    j.fail(self.tr("bt." + ("login" if res == "login" else "fail")))
            self.setup_job = job.run(work)
        else:
            # the switch restarts this screen: it comes back on this step and tests the broker there,
            # instead of on Look with no test seen (it went on to Look before)
            self.save(setup_step="local")
            self.setup_job = self.switch_local(back="setup", show=False)

    def setup_cards(self, t):
        """(intro, [(title, sub, chosen, action)], can_go_on, progress job)."""
        tr, on = self.tr, self.online()
        step = self.setup_step
        opts, intro, can, job = [], "", True, None
        if step == "lang":
            intro = tr("w.langI")
            opts = [("עברית", "כל המסך מימין לשמאל", self.lang == "he", lambda: self.save(lang="he")),
                    ("English", "left to right", self.lang == "en", lambda: self.save(lang="en"))]
        elif step == "place":
            intro = tr("w.placeI")
            c = self.city
            opts = [(f"{c['name']}, {c.get('country', '')}".strip(", "), tr("w.chosen"), True, lambda: None),
                    (tr("w.searchCity"), tr("w.worldwide") if on else tr("w.builtin"), False, lambda: self.open_place("setup"))]
        elif step == "time":
            tm = self.hm()
            if on:
                intro = tr("w.timeOn")
                opts = [(tm, tr("w.fromNet", c=self.city["name"]), True, lambda: None)]
            else:
                intro = tr("w.timeOff")

                def adj(sec):
                    return lambda: setattr(self, "time_adj", self.time_adj + sec)
                opts = [(tm, tr("w.now"), True, lambda: None), (tr("w.h+"), "", False, adj(3600)),
                        (tr("w.h-"), "", False, adj(-3600)), (tr("w.m+"), "", False, adj(300)),
                        (tr("w.m-"), "", False, adj(-300))]
        elif step == "sound":
            intro = tr("w.soundI")
            out = self.output

            def pick(o):
                def f():
                    if o != self.output and not (o == "aux" and self.output != "off"):   # sound on already: a device beside AUX stays
                        # the tone waits for the switch: played at once it came out of the old output
                        self.set_sound(o)
                        # remembered with where it was asked: on another step later, it is not played
                        self.tone_after = (self.view, self.setup_step, time.time()) if o != "off" else False
                    elif o != "off":
                        self.test_tone()
                return f
            out = self.output_shown                   # no HDMI screen: HDMI and both are not offered
            opts = [(tr("w.soundOn"), tr("w.aux"), out != "off", pick("aux"))]    # AUX always plays: no choice of it
            if self.hdmi_here():
                opts += [("HDMI", tr("w.hdmi"), out == "hdmi", pick("hdmi")),
                         (tr("w.both"), "AUX + HDMI", out == "both", pick("both"))]
            # USB sound devices plugged in now (headphones, a sound card), each on its own
            # (a USB device plugged in becomes an output beside AUX by itself: follow_sound)
            opts += [(tr("w.none"), tr("w.silent"), out == "off", pick("off"))]
        elif step == "conn":
            intro = tr("w.connI")
            b = self.broker

            def ext():
                if on:
                    b.update(type="external", state="untested", gen=b.get("gen", 0) + 1)
                    if self.ha.get("found") is None:
                        self.find_ha()
            here = os.environ.get("MQTT_LOCAL", "no") == "yes"
            opts = [(tr("local"), tr("w.localHere") if here else tr("w.localSub"), b["type"] == "local",
                     lambda: b.update(type="local", gen=b.get("gen", 0) + 1)),
                    (tr("external"), tr("w.extSimple") if on else tr("c.needsNet"), b["type"] == "external", ext)]
        elif step == "local":
            intro = tr("w.localTest") if os.environ.get("MQTT_LOCAL", "no") == "yes" else tr("w.localI")
            job = self.setup_job
            # Continue only once the local broker is in use and this screen's test passed: the switch
            # itself ends well a moment before the restart, and Continue then skipped the test
            can = bool(job and job.done and job.ok and os.environ.get("MQTT_LOCAL", "no") == "yes")
            if job and job.done and not job.ok:
                opts = [(self.tr("dg.again"), job.error, False, lambda: (setattr(self, "setup_job", None), self.start_local_step()))]
        elif step == "broker":
            b, f = self.broker, self.ha.get("found") or {}
            intro = (tr("w.brokerI", h=f.get("name") or f.get("host"), ip=f.get("host")) if f and f.get("broker") == "yes"
                     else tr("w.brokerI2")) if on else tr("w.noNet")
            cells = self.conn_cells(True, on, f)[1:5]
            opts = [(c[0], c[1], False, c[3]) for c in cells]
            opts.append((tr("w.test"), self.broker_text(), b["state"] == "ok", self.test_broker))
            can = b["state"] == "ok"
        elif step == "ha":
            intro = tr("w.haI")
            done = bool(self.st.get("ha_done"))
            opts = [(tr("c.token"), tr("w.haDone") if done else masked(self.ha["token"]) or tr("c.tokenD"), done,
                     lambda: self.edit_field(tr("c.tokenE"), lambda: self.ha["token"], lambda v: self.ha.update(token=v),
                                             secret=True, phone=True)),
                    (tr("w.setIt"), tr("c.run") if self.ha["token"] else tr("c.tokenFirst"), False,
                     lambda: self.run_ha_setup("setup") if self.ha["token"] and self.ha_url() else
                     self.toast(tr("tokenFirst") if not self.ha["token"] else tr("haUrlFirst")))]
        elif step == "tg":
            if not self.st.get("ha_done"):
                intro = tr("w.tgNo")
            else:
                intro = tr("w.tgI")
                done = bool(self.st.get("tg_done"))
                opts = [(tr("c.bot"), masked(self.tg["token"]) or tr("empty"), done,
                         lambda: self.edit_field(tr("c.botE"), lambda: self.tg["token"], lambda v: self.tg.update(token=v),
                                                 secret=True, phone=True)),
                        (tr("c.tgSetup"), tr("c.run") if self.tg["token"] else tr("c.tokenFirst"), False,
                         lambda: self.run_tg_setup("setup") if self.tg["token"] else self.toast(tr("tokenFirst")))]
        elif step == "look":
            intro = tr("w.lookI")
            from .views_settings import nxt, THEMES, CHARS
            opts = [(tr("c.theme"), tr("th." + self.theme), False, lambda: self.save(theme=nxt(THEMES, self.theme))),
                    (tr("c.char"), tr("ch." + self.kind), False, lambda: self.save(character=nxt(CHARS, self.kind)))]
        elif step == "done":
            # the addresses are lines to read, not cards to pick; the IP itself, since a .local name
            # is answered only where mDNS is allowed, and the home network may not allow it
            intro = tr("w.doneI") + "\n" + tr("w.addr") + ": " + (self.ip or tr("c.noNet"))
            if on and self.ip:
                intro += "\n" + tr("w.lessonAddr") + ": " + f"{self.ip}:{LESSON_UI_PORT}"
                if self.lesson_code():
                    intro += "\n" + tr("w.code") + ": " + self.lesson_code()
            opts = []
        return intro, opts, can, job

    def view_setup(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.35)
        ss = self.setup_steps()
        if self.setup_step not in ss:
            self.setup_step = ss[min(len(ss) - 1, 4)]
        step = self.setup_step
        at, n = ss.index(step), len(ss)
        self.header(tr("w.title", s=tr("w." + step)), f"{at + 1} / {n}")
        for i in range(n):
            cv.rect(24 + i * (752 / n), 60, 752 / n - 6, 5, P["gold"] if i <= at else (128, 128, 128, 90), r=3)
        if step == "wifi":
            self.wifi_setup_body(t)                  # its own list of networks (views_wifi.py)
            return
        intro, opts, can, job = self.setup_cards(t)
        radio = step in RADIO
        if not radio:
            if step == "done":
                opts.append((tr("w.finish"), "", False, self.finish_setup))
            elif can:
                skip = (step == "ha" and not self.st.get("ha_done")) or (step == "tg" and not self.st.get("tg_done"))
                opts.append((tr("w.skip") if skip else tr("w.continue"), tr("w.later") if skip else "", False,
                             lambda: self.setup_go(1)))
        qr = self.lesson_url() if step == "done" and self.online() else ""   # its code sits on the side opposite the text
        lines = [ln for para in intro.split("\n") for ln in cv.wrap(para, 752 - 220 if qr else 752, 15)]
        if qr:
            qx = 32 if self.lang == "he" else W - 32 - 200
            if self.draw_qr(qx, 100, 200, qr):
                with cv.physical():
                    # the caption is wrapped to the code's own width, never wider than its box
                    cy = 322
                    for cap, size, col, bold in [(c, 14, P["ink"], True) for c in cv.wrap(tr("qr.scan"), 200, 14, True)] + \
                            [(c, 12, P["dim"], False) for c in cv.wrap(tr("qr.scan2"), 200, 12)]:
                        cv.text(cap, qx + 100, cy, size, col, bold, "center")
                        cy += (18 if size == 14 else 16) * max(1.0, cv.zoom)
        pitch = 21 * max(1.0, cv.zoom)               # the lines keep apart at every interface size
        for i, line in enumerate(lines):
            cv.text(line, 24, 94 + i * pitch, 15, P["ink"])
        top = 100 + len(lines) * pitch
        if job:
            for i, label in enumerate(job.labels):
                y = top + i * 40
                s = job.state[i]
                cv.rect(24, y, W - 48, 34, P["panel"], r=10)
                ic = "✓" if s == 2 else "✕" if s == 3 else self.spinner(t) if s == 1 else "·"
                col = P["ok"] if s == 2 else (226, 118, 111) if s == 3 else P["gold"] if s == 1 else P["dim"]
                self.icon(ic, 44, y + 23, 17, col)
                cv.text(cv.fit(label, W - 120, 15, s == 1), 66, y + 23, 15, P["ink"] if s else P["dim"], s == 1)
            top += len(job.labels) * 40 + 6
        if self.setup_sel < 0:
            if radio:
                # framed on the current choice: a ✓ on it goes on with nothing changed
                self.setup_sel = next((i for i, o in enumerate(opts) if o[2]), 0)
            elif step not in AUTO_LAST:
                self.setup_sel = 0
            elif opts and (step != "local" or can or (job and job.done and not job.ok)):
                self.setup_sel = len(opts) - 1      # the local step's job failed: ✓ checks again
        sel = self.setup_sel
        if opts:
            sel = min(sel, len(opts) - 1)
            self.setup_sel = sel if self.setup_sel >= 0 else self.setup_sel

        def act(i):
            o, was = opts[i], self.setup_step
            if step == "conn" and i == 0 and not self.local_now() and os.environ.get("MQTT_HOST"):
                # the local broker over an external one in use: asked first, as in the settings
                def yes():
                    o[3]()
                    if self.view == "setup" and self.setup_step == was:
                        self.setup_go(1)
                self.ask(tr("c.toLocalQ"), yes)
                return
            if radio and o[2]:
                self.setup_go(1)                     # the current choice: on to the next step
                return
            o[3]()
            if radio and self.setup_step == was and (step != "conn" or self.online() or self.broker["type"] == "local"):
                self.setup_go(1)
        two = len(opts) > 2
        w = 370 if two else 752 - 220 if qr else 752     # the code takes the side opposite the text
        h = 46 if len(opts) > 4 else 58
        for i, (a, b, chosen, fn) in enumerate(opts):
            x = 24 + ((i % 2) * 382 if two else 0)
            y = top + ((i // 2) if two else i) * (h + 8)
            self.hit(x, y, w, h, lambda i=i: (setattr(self, "setup_sel", i), act(i)))
            cv.rect(x, y, w, h, P["panel"], r=12)
            if i == max(0, sel) and self.setup_sel >= 0 or chosen:
                cv.rect(x, y, w, h, P["sel"] if i == self.setup_sel else P["ok"], r=12, width=2.5)
            dot = "● " if chosen else ""
            # as a settings card: the lines grow with the interface size only as far as the card's height lets them,
            # so the title never sits on the card's edge
            zoom = cv.zoom
            cv.zoom = min(zoom, max(1.0, 1.25 * h / 64))
            try:
                ta, ts = self.fit_text(a, w - 32 - (cv.measure(dot, 17, True) if dot else 0), 17, True)
                cv.text(dot + ta, x + 16, y + (h / 2 - 2 if b else h / 2 + 6), ts, P["ink"], True)
                if b:
                    tb, bs = self.fit_text(b, w - 32, 12)
                    cv.text(tb, x + 16, y + h / 2 + 16, bs, P["dim"])
            finally:
                cv.zoom = zoom
        if step == "look":
            self.char.draw(cv, self.kind, W / 2, min(top + 210, H - 58 - 70), t, 0.6)
        ok_label = opts[self.setup_sel][0].lstrip("●✓ ") if opts and self.setup_sel >= 0 else tr("installing")
        n_opts = len(opts)
        self.bar([("☰", tr("w.prev"), lambda: self.setup_go(-1)),
                  ("▼", tr("down"), (lambda: setattr(self, "setup_sel", (max(0, self.setup_sel) + 1) % n_opts)) if n_opts else None),
                  ("▲", tr("up"), (lambda: setattr(self, "setup_sel", (max(0, self.setup_sel) + n_opts - 1) % n_opts)) if n_opts else None),
                  ("✓", ok_label, (lambda: act(self.setup_sel)) if n_opts and self.setup_sel >= 0 else None)])


def masked(v):
    return ("●●●●●● " + v[-4:]) if v else ""
