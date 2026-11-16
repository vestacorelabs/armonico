"""views_bt.py - Bluetooth sound on the device: headphones and speakers found, paired and connected from the
screen. Settings › Sound › Output › Bluetooth.

Everything that asks BlueZ goes to the root unit (system.bt, root_actions bt-*) on a thread of its own, one at
a time; the drawing only reads what came back. A paired device is trusted, so headphones that are switched on
come back by themselves; a speaker that does not call back is connected again by follow_bt() while it is in the
sound choice. The sound itself is PipeWire's: a connected device is one more output (audio.py, "bt:<node>").
"""
import os
import threading
import time

from .canvas import W, H
from .widgets import RED, BAR
from . import system, audio

STATUS_EVERY = 8            # seconds between two looks at the saved devices, while the page is open
SCAN_SECONDS = 10           # how long a scan listens
RETRY_FIRST = 20            # seconds before the first try to connect a chosen device that is not there
RETRY_MAX = 120             # and the longest wait between tries
RETRY_TRIES = 12           # tries of a chosen device that does not come back (about 20 minutes), then it waits
BOOT_WINDOW = 300          # seconds after the Pi started in which a connected device with no sound is asked at once
SILENT_EVERY = 30          # seconds between two looks for a connected device with no sound (bt_boot_cycle)
SILENT_TRIES = 3           # asks for its sound profile, for each device, until it has an output or disconnects
RADIO_CYCLES = 2           # the Bluetooth radio off and on again for a device still silent after that, at most in an hour
ROW = 46
STEP = 52
LABEL = 26
TOP = 58


def hci_present(base="/sys/class/bluetooth"):
    """Whether the kernel has a Bluetooth adapter (hci0 ...), read without root."""
    try:
        return any(n.startswith("hci") for n in os.listdir(base))
    except OSError:
        return False


class BtViews:
    @property
    def bt(self):
        b = self.__dict__.get("_bt")
        if b is None:
            b = self._bt = {"hw": None, "why": "", "powered": True, "saved": [], "found": [], "busy": False,
                            "scanning": False, "scanned": False, "status_at": 0.0, "sel": 0, "act": {},
                            "screen": "list", "fsel": 0, "retry": {}, "retrying": False, "seen": None, "named": 0, "nameless": False,
                            "origin": "outs", "manual_off": set(self.st.get("bt_manual_off") or []), "queue": [], "lock": threading.Lock(),
                            "tickets": {}, "scan_ticket": None, "status_err": ""}
        return b

    # ------------------------------------------------------------------ what BlueZ says
    def bt_take(self, data):
        b = self.bt
        hw, why = bool(data.get("hw")), str(data.get("why") or "")
        # "no adapter" while the kernel has one (hci0 in /sys): bluetoothd had not picked it up yet, just after it
        # started or after the radio came on, and the page said "No Bluetooth adapter" for a few seconds before the
        # next look. It is believed only on the third such answer in a row; meanwhile the page says it is checking
        # and asks again within a second.
        if not hw and why in ("", "no-adapter") and hci_present():
            b["noad"] = b.get("noad", 0) + 1
            if b["noad"] < 3:
                if b["hw"] is not True:
                    b["hw"] = None
                b["status_at"] = time.monotonic() - STATUS_EVERY + 1
                return
        else:
            b["noad"] = 0
        b["hw"] = hw
        b["why"] = why
        b["powered"] = bool(data.get("powered", True))
        if isinstance(data.get("saved"), list):
            new = [d for d in data["saved"] if isinstance(d, dict) and d.get("mac")]
            # a saved device missing from one look is kept for that look: bluetoothctl that answers late leaves a
            # device out now and then, and the page jumped as its row went and came back. Gone twice in a row: gone.
            have, miss = {d["mac"] for d in new}, b.setdefault("saved_miss", {})
            for d in b["saved"]:
                if d["mac"] not in have:
                    miss[d["mac"]] = miss.get(d["mac"], 0) + 1
                    if miss[d["mac"]] < 2:
                        new.append(d)
            for m in list(miss):
                if m in have:
                    miss.pop(m)
            b["saved"] = new
        if "seen" in data:
            b["seen"], b["named"] = int(data.get("seen") or 0), int(data.get("named") or 0)
        if isinstance(data.get("found"), list):
            have = {d["mac"] for d in b["saved"]}
            b["found"] = [d for d in data["found"] if isinstance(d, dict) and d.get("mac") and d["mac"] not in have]
        else:
            have = {d["mac"] for d in b["saved"]}
            b["found"] = [d for d in b["found"] if d["mac"] not in have]

    def bt_job(self, fn, queue=False):
        """One BlueZ question at a time, on a thread; fn() runs there. While another one runs, a job with queue=True
        waits its turn and runs right after it (True); without queue it is not taken (False)."""
        b = self.bt
        with b["lock"]:
            if b["busy"]:
                if not queue:
                    return False
                b["queue"].append(fn)
                return True
            b["busy"] = True

        def go():
            job = fn
            while job is not None:
                try:
                    job()
                except Exception:
                    import traceback
                    traceback.print_exc()
                with b["lock"]:                      # the queue and busy change together, so no job is left behind
                    job = b["queue"].pop(0) if b["queue"] else None
                    if job is None:
                        b["busy"] = False
        threading.Thread(target=go, daemon=True).start()
        return True

    def bt_status_now(self):
        """The saved devices asked again. A failure is kept (status_err), so the page can say why while it has
        nothing to show; bt_follow asks again after STATUS_EVERY seconds."""
        ok, data = system.bt("status", timeout=20)
        if ok:
            self.bt["status_err"] = ""
            self.bt_take(data)
        elif data != "cancelled":
            self.bt["status_err"] = str(data or "failed")
        return ok

    def bt_refresh(self):
        self.bt["status_at"] = time.monotonic()
        self.bt_job(self.bt_status_now)

    def bt_follow(self):
        """While the page is in front: the saved devices again every STATUS_EVERY seconds."""
        b = self.bt
        if not b["busy"] and not b["scanning"] and not b["act"] and time.monotonic() - b["status_at"] > STATUS_EVERY:
            self.bt_refresh()

    def bt_scan(self):
        b = self.bt
        if b["scanning"] or b["busy"]:
            return

        ticket = b["scan_ticket"] = system.Ticket()

        def go():
            b["scanning"], b["scanned"] = True, True
            try:
                ok, data = system.bt("scan", timeout=SCAN_SECONDS + 25, ticket=ticket, seconds=SCAN_SECONDS)
                if not ok and data == "no-adapter" and hci_present() and not ticket.cancelled:
                    time.sleep(1)                    # bluetoothd a moment late with the adapter: once more
                    ok, data = system.bt("scan", timeout=SCAN_SECONDS + 25, ticket=ticket, seconds=SCAN_SECONDS)
                if ok:
                    self.bt_take(data)
                elif data != "cancelled":
                    self.toast(self.bt_reason(data), 4)
            finally:
                b["scanning"] = False
                b["status_at"] = time.monotonic()
        self.bt_job(go)

    def bt_missing(self):
        """Why there is no adapter, in words: (title, line). A board with Bluetooth of its own always has one, so
        "no adapter" is never the answer there: config.txt turns the chip off (by Armonico or by hand), or it went
        off with the Wi-Fi chip (the two share a power line on some boards), or the chip is not answering."""
        tr = self.tr
        off, hand = system.chips_off()
        if "bluetooth" in off:
            return tr("bt.chipOff"), tr("bt.chipOffHandD" if "bluetooth" in hand else "bt.chipOffD")
        builtin = system.board_bluetooth()
        if "wifi" in off and builtin is not False:
            return tr("bt.chipWithWifi"), tr("bt.chipWithWifiD")
        if builtin:
            return tr("bt.stuck"), tr("bt.e.stuck")
        return tr("bt.noAdapter"), tr("bt.noAdapterD")

    def bt_fix(self):
        """The chip the page can turn on to bring Bluetooth back ("bluetooth" or "wifi"), or None."""
        b = self.bt
        if b["hw"] is not False or b["why"] not in ("", "no-adapter"):
            return None
        off, _ = system.chips_off()
        if "bluetooth" in off:
            return "bluetooth"
        if "wifi" in off and system.board_bluetooth() is not False:
            return "wifi"
        return None

    def bt_reason(self, code):
        tr = self.tr
        code = (code or "failed").strip()
        if code == "no-adapter":
            return self.bt_missing()[0]
        key = "bt.e." + code
        return tr(key) if key in self.bt_known() else tr("bt.e.failed")

    def bt_known(self):
        from .texts import T
        return T

    # ------------------------------------------------------------------ actions
    def open_bt(self, origin="outs"):
        """origin: the page it was opened from, "outs" (Settings › Sound › Output), "quick" (the quick check) or "net" (Network and links)."""
        b = self.bt
        b.update(screen="list", sel=0, fsel=0, status_at=0.0, scanned=False, found=[], seen=None, named=0,
                 origin=origin if origin in ("outs", "quick", "net") else "outs")
        if b["hw"] is False:
            b["hw"], b["noad"] = None, 0             # an old "no adapter" is not shown again: checking until it answers
        self.go("bt")

    def open_bt_quick(self):
        self.open_bt("quick")

    def bt_leave(self):
        """Back to the page it was opened from, on its own card: the Output page's Bluetooth cell, or the quick
        check's list. A pairing or connection still running is called off, so it does not hold the next one up."""
        b = self.bt
        self.bt_cancel(quiet=True)
        b["screen"] = "list"
        if b.get("origin") == "quick":
            keys = [k for k, _ in self.quick_areas()]
            self.page, self.sel = "quick", keys.index("bt") if "bt" in keys else 0
        elif b.get("origin") == "net":
            self.page, self.sel = "net", 2          # the Bluetooth card on Network and links
        else:
            self.page, self.sel = "outs", len([k for k, _ in self.snd_list or [] if k != "aux"])   # AUX has no cell
        self.go("settings")

    def bt_cancel(self, quiet=False):
        """Gives up on what is running now (a scan, a pairing, a connection): the root side stops within a moment and
        the answer comes back as "cancelled". A scan that is stopped keeps the devices it had heard. Only this page's own
        requests are called off (their tickets), also those still waiting their turn; another page's root action is not."""
        b = self.bt
        if b["act"] or b["scanning"]:
            for t in list(b["tickets"].values()) + [b["scan_ticket"]]:
                system.cancel(t)
            if not quiet:
                self.toast(self.tr("bt.stopping"), 2)

    def bt_cell_text(self):
        b = self.bt
        now = [d for d in b["saved"] if d.get("connected")]
        if now:
            return now[0]["name"] + (f" +{len(now) - 1}" if len(now) > 1 else "")
        return self.tr("bt.noneConnected")

    def bt_toggle(self, d):
        """A saved device: connected when it is not, disconnected when it is."""
        b = self.bt
        mac = d["mac"]
        if mac in b["act"]:
            return
        want = not d.get("connected")
        label = self.tr("bt.connecting") if want else self.tr("bt.disconnecting")
        ticket = b["tickets"][mac] = system.Ticket()
        b["act"][mac] = self.tr("bt.waiting") if b["busy"] else label
        if want:
            b["manual_off"].discard(mac)
            b["retry"].pop(mac, None)                # chosen by hand: the tries behind the screen start over
        else:
            b["manual_off"].add(mac)                 # a disconnect by hand stays: nothing connects it again on its own
            b["retry"].pop(mac, None)
        self.bt_keep_off()

        def go():
            try:
                if ticket.cancelled:                  # called off while it waited its turn: nothing to ask
                    if not want:
                        b["manual_off"].discard(mac)
                        self.bt_keep_off()
                    return
                b["act"][mac] = label
                ok, data = system.bt("connect" if want else "disconnect", timeout=30 if want else 35, ticket=ticket, mac=mac)
                if not ok:
                    if data != "cancelled":
                        self.toast(self.bt_reason(data), 5)
                    if not want:
                        b["manual_off"].discard(mac)
                        self.bt_keep_off()
                else:
                    d["connected"] = want
                    if want:
                        self.toast(self.tr("bt.connected", n=self.bt_name(d)), 4)
                    else:
                        self.toast(self.tr("bt.removedLink" if data.get("removed") else "bt.disconnectedOk",
                                           n=self.bt_name(d)), 4)
                self.bt_status_now()
            finally:
                self.bt_done(mac, ticket)
        self.bt_job(go, queue=True)

    def bt_keep_off(self):
        """The devices disconnected by hand, kept in the settings: after a restart of the screen nothing connects
        them again on its own either (follow_bt)."""
        off = sorted(self.bt["manual_off"])
        if off != sorted(self.st.get("bt_manual_off") or []):
            self.save(bt_manual_off=off)

    def bt_drop_back(self, mac):
        """A forgotten device leaves the sound output it was waiting to come back to (follow_bt)."""
        back = self.st.get("sound_back")
        if isinstance(back, dict) and back.get("keys"):
            keys = [k for k in back["keys"] if not (isinstance(k, str) and audio.bt_mac(k) == mac)]
            if keys != back["keys"]:
                self.save(sound_back=dict(back, keys=keys) if keys else None)
        self.bt["retry"].pop(mac, None)

    def bt_done(self, mac, ticket):
        """A connection or pairing is over (or was called off): its row shows the device again."""
        b = self.bt
        if b["tickets"].get(mac) is ticket:
            b["tickets"].pop(mac, None)
            b["act"].pop(mac, None)
        b["status_at"] = time.monotonic()

    def bt_pair(self, d):
        """A new device: paired, trusted and connected in one go (root_actions bt-pair)."""
        b = self.bt
        mac = d["mac"]
        if mac in b["act"]:
            return
        ticket = b["tickets"][mac] = system.Ticket()
        b["act"][mac] = self.tr("bt.waiting") if b["busy"] else self.tr("bt.pairing")

        def go():
            try:
                if ticket.cancelled:
                    return
                b["act"][mac] = self.tr("bt.pairing")
                ok, data = system.bt("pair", timeout=70, ticket=ticket, mac=mac)
                if not ok:
                    # root_actions called the pairing off and forgot the device (bt_release): it is free again
                    self.toast(self.bt_reason(data) + " · " + self.tr("bt.freed"), 7 if data != "cancelled" else 3)
                elif isinstance(data, dict) and data.get("connected") is False:
                    # paired and kept, the first connection did not finish in time: it is in the saved list
                    self.toast(self.tr("bt.pairedOnly", n=d.get("name") or mac), 6)
                else:
                    self.toast(self.tr("bt.connected", n=d.get("name") or mac), 4)
                if ok:                               # paired again: an old disconnect by hand no longer holds it back
                    b["manual_off"].discard(mac)
                    b["retry"].pop(mac, None)
                    self.bt_keep_off()
                self.bt_status_now()
            finally:
                self.bt_done(mac, ticket)
        self.bt_job(go, queue=True)

    def bt_forget(self, d):
        """Forgets a saved device. While another Bluetooth action runs it waits its turn (a short note says so)."""
        b = self.bt

        def go():
            ok, data = system.bt("forget", timeout=15, mac=d["mac"])
            if not ok:
                self.toast(self.bt_reason(data), 4)
            else:
                b["manual_off"].discard(d["mac"])
                self.bt_keep_off()
                self.bt_drop_back(d["mac"])          # a device forgotten is not connected again behind the screen
                b["saved"] = [x for x in b["saved"] if x["mac"] != d["mac"]]   # gone at once, not kept for a look
            self.bt_status_now()
            b["fsel"] = 0
        if b["busy"]:
            self.toast(self.tr("bt.queued"), 3)
        self.bt_job(go, queue=True)

    # ------------------------------------------------------------------ the devices connected before the sound was there
    def bt_boot_cycle(self, names=()):
        """A saved device that is connected but has no sound output in PipeWire is asked for its sound profile
        (root_actions bt-reconnect, which never drops the link). It shows as connected and stays silent: at boot it
        connected before the screen's PipeWire was up, and later PipeWire or its Bluetooth side restarted under a
        device that stayed connected. Before, this ran only in the first minutes after boot, so a device that went
        silent later stayed silent until Bluetooth was switched off and on by hand.
        Looked at every SILENT_EVERY seconds (follow_sound). In the first minutes after boot a silent device is asked
        at once; later only when it was silent at 2 looks in a row, so a device that is still setting up its sound
        is left to finish. At most SILENT_TRIES asks for each device, counted again once it has an output or
        disconnects. A device that has its output is not touched, and neither is one disconnected by hand.
        names: the output keys PipeWire has now (audio.keys_now)."""
        b = self.bt
        now = time.monotonic()
        if now - b.get("silent_at", -SILENT_EVERY) < SILENT_EVERY:
            return
        if self.radio_now("bluetooth") in (None, "off", "hard") or b["busy"] or b["retrying"] or b.get("cycling"):
            return
        if b.get("silent_looked") and not b["saved"]:
            return                                       # no saved device: nothing to ask bluetoothd about
        b["silent_at"] = now
        outs = " ".join(str(k).upper() for k in names)
        boot = now <= BOOT_WINDOW

        def go():
            ok, data = system.bt("status", timeout=30)
            if not ok:
                b["silent_at"] = -SILENT_EVERY          # bluetoothd still starting: asked again at the next look
                return
            self.bt_take(data)
            b["silent_looked"] = True
            calls = []
            if any(d.get("connected") for d in b["saved"]):
                audio.bt_a2dp()                          # a device on its headset profile: back to A2DP
                calls = audio.bt_no_a2dp()               # and one with no music profile at all: asked for its sound
            seen = b.setdefault("silent", {})            # mac: (looks silent in a row, asks made)
            silent = {d["mac"] for d in b["saved"] if d.get("connected") and d["mac"] not in b["manual_off"]
                      and (d["mac"].replace(":", "_").upper() not in outs or d["mac"].upper() in calls)}
            for mac in list(seen):
                if mac not in silent:
                    seen.pop(mac)                        # it has its output, or it disconnected: counted again
            todo, stuck = [], False
            for mac in silent:
                looks, asks = seen.get(mac, (0, 0))
                looks += 1
                if asks < SILENT_TRIES and (boot or looks >= 2):
                    todo.append(mac)
                    asks += 1
                elif asks >= SILENT_TRIES and looks >= SILENT_TRIES + 3:
                    stuck = True                         # asked 3 times and still silent 2 looks after the last
                seen[mac] = (looks, asks)
            # the last step, the one that was done by hand: the radio off and on again. At most RADIO_CYCLES in an
            # hour, so a device that stays silent for another reason does not keep the radio going off
            recent = [x for x in b.get("radio_cycles", []) if now - x < 3600]
            if stuck and len(recent) < RADIO_CYCLES:
                b["radio_cycles"] = recent + [now]
                b["cycling"] = True
                try:
                    if system.radio_set("bluetooth", False)[0]:
                        system.radio_set("bluetooth", True)
                        self.toast(self.tr("bt.radioCycled"), 5)
                    seen.clear()
                finally:
                    b["cycling"] = False
                    self.snd_miss = {}
                    self.snd_ext_looked = False
                    self.__dict__.setdefault("_radio_cache", {}).pop("bluetooth", None)
                return
            if not todo:
                return
            b["cycling"] = True
            try:
                for mac in todo:
                    system.bt("reconnect", timeout=60, mac=mac)
            finally:
                b["cycling"] = False
                self.snd_miss = {}
                # the outputs that came back are the ones there before: the next look notes them, chooses nothing
                self.snd_ext_looked = False
        self.bt_job(go, queue=True)

    # ------------------------------------------------------------------ a chosen device that is not there
    def follow_bt(self, keys):
        """Called by follow_sound with the Bluetooth keys that are chosen (or were, before they dropped out) and
        the sound outputs there are now: a headset that is switched on connects by itself, a speaker does not, so
        its address is connected again with a growing wait (20 s, then twice as long, up to 2 minutes). Nothing
        runs while the page is open (it asks for itself) or while another BlueZ question is on."""
        b = self.bt
        if self.view == "bt" or b["busy"] or b["retrying"] or not keys:
            return
        if self.radio_now("bluetooth") in ("off", "hard"):      # switched off in Settings: nothing is connected behind it
            return
        now = time.monotonic()
        todo = []
        for k in keys:
            mac = audio.bt_mac(k)
            if not mac or mac in b["manual_off"]:
                continue
            r = b["retry"].setdefault(mac, {"at": now + RETRY_FIRST - 1, "wait": RETRY_FIRST})
            if now >= r["at"]:
                todo.append((mac, r))
        if not todo:
            return
        b["retrying"] = True

        def go():
            try:
                for mac, r in todo:
                    ok, _ = system.bt("connect", timeout=30, mac=mac)
                    if ok:
                        b["retry"].pop(mac, None)
                    else:
                        # a growing wait, and after RETRY_TRIES tries it stops until the device is chosen again or
                        # the screen starts again: a device that is away for good does not keep the radio busy
                        r["n"] = r.get("n", 0) + 1
                        r["wait"] = min(RETRY_MAX, r["wait"] * 2)
                        r["at"] = time.monotonic() + (r["wait"] if r["n"] < RETRY_TRIES else float("inf"))
            finally:
                b["retrying"] = False
        threading.Thread(target=go, daemon=True).start()

    # ------------------------------------------------------------------ the page
    def bt_lines(self):
        """The page as lines: (kind, data). Rows and the scan line can be selected; a label cannot."""
        b, tr = self.bt, self.tr
        lines = []
        if b["saved"]:
            lines.append(("label", tr("bt.saved")))
            lines += [("saved", d) for d in b["saved"]]
        lines.append(("label", tr("bt.nearby")))
        lines.append(("scan", None))
        shown = [d for d in b["found"] if d.get("name") or d.get("kind") == "audio"]
        hidden = [d for d in b["found"] if d not in shown]
        lines += [("found", d) for d in shown]
        if hidden:
            lines.append(("hidden", len(hidden)))
            if b["nameless"]:
                lines += [("found", d) for d in hidden]
        if b["saved"]:
            lines.append(("forgetrow", None))
        return lines

    def bt_unavailable(self):
        """True (and the page says why) when there is no Bluetooth to work with."""
        b, tr, P, cv = self.bt, self.tr, self.P, self.cv
        if b["hw"] is None:
            if b["status_err"]:                     # the last look failed: why, while bt_follow keeps asking
                cv.text(self.short(self.bt_reason(b["status_err"]), W - 48, 17, True), 24, 100, 17, P["ink"], True)
                cv.text(tr("bt.retrying"), 24, 128, 15, P["dim"])
            else:
                cv.text(tr("bt.loading"), 24, 100, 17, P["dim"])
            return True
        if b["hw"]:
            return False
        why = b["why"] or "no-adapter"
        if why == "stuck":                          # the chip stopped answering (root_actions bt_power)
            big, small = tr("bt.stuck"), tr("bt.e.stuck")
        elif why == "no-bluez":
            big, small = tr("bt.noBluez"), tr("bt.noBluezD")
        else:
            big, small = self.bt_missing()
        cv.text(self.short(big, W - 48, 18, True), 24, 100, 18, P["ink"], True)
        self.paras([small], 24, 128, W - 48, 15, 21, P["dim"])
        return True

    def view_bt(self, t):
        b, tr, P, cv = self.bt, self.tr, self.P, self.cv
        self.hills(0.35)
        if b["screen"] == "forget":
            self.bt_screen_forget(t)
            return
        if self.radio_now("bluetooth") == "off":
            self.header(tr("bt.title"), "")
            cv.text(self.short(tr("bt.radioOff"), W - 48, 18, True), 24, 100, 18, P["ink"], True)
            self.paras([tr("bt.radioOffD")], 24, 128, W - 48, 15, 21, P["dim"])
            self.bar([("☰", tr("back"), self.bt_leave), ("▼", "", None), ("▲", "", None),
                      ("✓", tr("radio.turnOn"), lambda: self.radio_toggle("bluetooth"))])
            return
        self.bt_follow()
        sub = tr("bt.looking") if b["scanning"] else tr("bt.seen", n=b["seen"], m=b["named"]) if b["seen"] is not None else ""
        self.header(tr("bt.title"), sub)
        if self.bt_unavailable():
            fix = self.bt_fix()
            self.bar([("☰", tr("back"), self.bt_leave), ("▼", "", None), ("▲", "", None),
                      ("✓", tr("radio.turnOn"), lambda: self.chip_ask(fix)) if fix else ("✓", "", None)])
            return
        lines = self.bt_lines()
        sel_idx = [i for i, (k, _) in enumerate(lines) if k != "label"]

        def key_of(j):
            k, d = lines[j]
            return (k, d.get("mac") if isinstance(d, dict) else None)
        keys = [key_of(j) for j in sel_idx]
        # the list changed under the frame (a look or a scan added or took a row): the frame stays on the same
        # row, not on the same number, so the page does not jump. A move by ▲ ▼ or a touch changes sel, and is kept.
        ref = b.get("sel_ref")
        if ref and ref[0] == b["sel"] and ref[1] in keys and (b["sel"] >= len(keys) or keys[b["sel"]] != ref[1]):
            b["sel"] = keys.index(ref[1])
        b["sel"] = max(0, min(b["sel"], len(sel_idx) - 1))
        b["sel_ref"] = (b["sel"], keys[b["sel"]])
        cur_i = sel_idx[b["sel"]]
        # y of each line, scrolled so the selected one is on the page
        ys, y = [], 0
        for k, _ in lines:
            ys.append(y)
            y += LABEL if k == "label" else STEP
        limit = H - BAR - TOP - 6
        scroll = max(0, ys[cur_i] + (ROW if lines[cur_i][0] != "label" else LABEL) - limit)
        first_y = TOP - scroll
        for i, (kind, d) in enumerate(lines):
            yy = first_y + ys[i]
            if yy < TOP - 4 or yy + (LABEL if kind == "label" else ROW) > H - BAR - 2:
                continue
            framed = i == cur_i
            pick = lambda i=i: (b.update(sel=sel_idx.index(i)), self.bt_act(lines[i]))
            if kind == "label":
                cv.text(lines[i][1], 28, yy + 18, 14, P["dim"])
            elif kind == "saved":
                self.bt_row(24, yy, W - 48, ROW, d["name"] or d["mac"], d, framed, pick)
            elif kind == "found":
                self.bt_row(24, yy, W - 48, ROW, self.bt_name(d), d, framed, pick)
            elif kind == "hidden":
                self.wifi_extra_row(24, yy, W - 48, ROW, "‹" if b["nameless"] else "›",
                                    tr("bt.hidden", n=d), framed, pick)
            elif kind == "forgetrow":
                self.wifi_extra_row(24, yy, W - 48, ROW, "›", tr("bt.forgetRow"), framed, pick)
            else:
                self.bt_scan_row(24, yy, W - 48, ROW, framed, pick, t)
        if y > limit:                                # longer than the page: position bar, counter and drag, as the other lists
            vis = [j for j, (k, _) in enumerate(lines) if k != "label"
                   and first_y + ys[j] >= TOP - 4 and first_y + ys[j] + ROW <= H - BAR - 2]
            if vis:
                a = sel_idx.index(vis[0])
                cv.text(self.page_counter(a, len(vis), len(sel_idx)), W - 22, 47, 11, P["dim"], align="right")
            self.scroll_bar(TOP, limit - 6, scroll, limit, y)
            self.scroll_zone(24, TOP, W - 48, limit, STEP,
                             lambda d: b.update(sel=max(0, min(len(sel_idx) - 1, b["sel"] + d))))
        cur = lines[cur_i]
        ok = self.bt_ok_label(cur)
        act_now = bool(b["act"]) or b["scanning"]
        self.bar([("☰", tr("back"), self.bt_leave),
                  ("▼", tr("down"), lambda: b.update(sel=(b["sel"] + 1) % len(sel_idx))),
                  ("▲", tr("up"), lambda: b.update(sel=(b["sel"] - 1) % len(sel_idx))),
                  ("✓", tr("cancel") if act_now else ok,
                   self.bt_cancel if act_now else ((lambda: self.bt_act(cur)) if ok else None))])

    def bt_ok_label(self, cur):
        tr, b = self.tr, self.bt
        kind, d = cur
        if kind == "scan":
            return "" if (b["scanning"] or b["busy"]) else tr("bt.scan")
        if kind == "hidden":
            return tr("bt.hide") if b["nameless"] else tr("bt.show")
        if d and d["mac"] in b["act"]:
            return ""
        if kind == "saved":
            return tr("bt.disconnect") if d.get("connected") else tr("bt.connect")
        if kind == "found":
            return "" if d.get("kind") == "other" else tr("bt.pair")
        if kind == "forgetrow":
            return tr("wf.see")
        return ""

    def bt_name(self, d):
        """What a row says: the name, or for a device with none "No name" and the end of its address."""
        return d.get("name") or f"{self.tr('bt.unnamed')} · {d['mac'][-5:]}"

    def bt_act(self, item):
        kind, d = item
        if kind == "scan":
            self.bt_scan()
        elif kind == "saved":
            self.bt_toggle(d)
        elif kind == "found":
            if d.get("kind") != "other":
                self.bt_pair(d)
        elif kind == "hidden":
            self.bt["nameless"] = not self.bt["nameless"]
        elif kind == "forgetrow":
            self.bt.update(screen="forget", fsel=0)

    def bt_row(self, x, y, w, h, name, d, framed, fn):
        P, cv, tr, b = self.P, self.cv, self.tr, self.bt
        self.hit(x, y, w, h, fn)
        cv.rect(x, y, w, h, P["panel"], r=10)
        if framed:
            cv.rect(x, y, w, h, P["sel"], r=10, width=2.5)
        base = y + h / 2 + 6
        act = b["act"].get(d["mac"])
        if act:
            state, col = act, P["gold"]
        elif d.get("connected"):
            state, col = "✓ " + tr("bt.on"), P["ok"]
        elif d in b["saved"]:
            state, col = tr("bt.off"), P["dim"]
        elif d.get("kind") == "other":
            state, col = tr("bt.notAudio"), P["dim"]
        else:
            state, col = "", P["dim"]
        end = x + w - 16
        if state:
            cv.text(state, end, base - 1, 13, col, True, "right")
            end -= cv.measure(state, 13, True) + 14
        cv.text(cv.fit(name, max(60, end - (x + 16)), 16, True), x + 16, base, 16, P["ink"], True)

    def bt_scan_row(self, x, y, w, h, framed, fn, t):
        """The line the scan starts from: a gold button, or the spinner while it looks."""
        P, cv, tr, b = self.P, self.cv, self.tr, self.bt
        self.hit(x, y, w, h, fn)
        looking = b["scanning"]
        cv.rect(x, y, w, h, P["sel"] if framed else P["dim"], r=10, width=2.5 if framed else 1.2)
        if looking:
            self.spin(x + 28, y + h / 2, 10, P["gold"], t)
            cv.text(tr("bt.looking"), x + 54, y + h / 2 + 6, 16, P["ink"], True)
        else:
            label = tr("bt.scanAgain") if b["scanned"] else tr("bt.scan")
            cv.text("+", x + 22, y + h / 2 + 7, 18, P["gold"], True, "center")
            cv.text(cv.fit(label, w - 64, 16, True), x + 44, y + h / 2 + 6, 16, P["ink"], True)

    # ------------------------------------------------------------------ forgetting a saved device
    def bt_screen_forget(self, t):
        b, tr, P, cv = self.bt, self.tr, self.P, self.cv
        self.header(tr("bt.forgetTitle"), "")
        back = lambda: b.update(screen="list")
        saved = b["saved"]
        if not saved:
            cv.text(tr("bt.noSaved"), 24, 100, 17, P["ink"], True)
            self.bar([("☰", tr("back"), back), ("▼", "", None), ("▲", "", None), ("✓", "", None)])
            return
        b["fsel"] = max(0, min(b["fsel"], len(saved) - 1))
        per = 6
        first = max(0, min(b["fsel"] - per + 1, len(saved) - per))
        for j, d in enumerate(saved[first:first + per]):
            i = first + j
            y = TOP + j * STEP
            self.hit(24, y, W - 48, ROW, lambda i=i: b.update(fsel=i))
            cv.rect(24, y, W - 48, ROW, P["panel"], r=10)
            if i == b["fsel"]:
                cv.rect(24, y, W - 48, ROW, P["sel"], r=10, width=2.5)
            cv.text(tr("wf.forget"), W - 40, y + 29, 14, RED, True, "right")
            cv.text(cv.fit(d["name"] or d["mac"], W - 160, 16, True), 40, y + 29, 16, P["ink"], True)
        self.list_marks(first, per, len(saved), TOP, STEP, lambda d: b.update(fsel=max(0, min(len(saved) - 1, b["fsel"] + d))))
        cur = saved[b["fsel"]]
        name = cur["name"] or cur["mac"]
        self.bar([("☰", tr("back"), back),
                  ("▼", tr("down"), lambda: b.update(fsel=(b["fsel"] + 1) % len(saved))),
                  ("▲", tr("up"), lambda: b.update(fsel=(b["fsel"] - 1) % len(saved))),
                  ("✓", tr("wf.forget"), lambda: self.ask(tr("bt.forgetQ", s=name), lambda: self.bt_forget(cur)))])

    # ------------------------------------------------------------------ the Wi-Fi and Bluetooth switches
    def radio_now(self, kind):
        """"on", "off", "hard" or None (no such radio), read from /sys at most every 2 seconds. While a switch is
        being made it shows the state it is going to."""
        pend = self.__dict__.setdefault("_radio_pend", {})
        if kind in pend and time.monotonic() < pend[kind][0]:
            return pend[kind][1]
        cache = self.__dict__.setdefault("_radio_cache", {})
        at, val = cache.get(kind, (-1e9, None))
        if time.monotonic() - at > 2:
            val = system.radio_state(kind)
            cache[kind] = (time.monotonic(), val)
        return val

    def radio_switch(self, kind):
        """The card's value: On or Off with its switch, or why there is none (blocked by a hardware switch, not found)."""
        from .widgets import SwitchVal
        tr = self.tr
        if self.__dict__.get("_radio_busy", {}).get(kind):
            v = SwitchVal(tr("radio.working"))
            v.on = self.radio_now(kind) == "on"
            return v
        st = self.radio_now(kind)
        if st == "hard":
            return tr("radio.hard")
        if st is None:
            return tr("radio.none")
        v = SwitchVal(tr("on") if st == "on" else tr("off"))
        v.on = st == "on"
        return v

    def radio_toggle(self, kind):
        """On to off or off to on, at once, with no question."""
        busy = self.__dict__.setdefault("_radio_busy", {})
        if busy.get(kind):
            return
        st = self.radio_now(kind)
        if st == "hard":
            self.toast(self.tr("radio.hardD"), 5)
            return
        if st is None:
            return
        if kind == "wifi" and self.st.get("wifi_rested"):
            self.save(wifi_rested=False)            # a switch by hand: the automatic rest no longer turns it back on
        self.radio_apply(kind, st == "off")

    def radio_apply(self, kind, on):
        busy = self.__dict__.setdefault("_radio_busy", {})
        pend = self.__dict__.setdefault("_radio_pend", {})
        busy[kind] = True
        pend[kind] = (time.monotonic() + 40, "on" if on else "off")

        def go():
            try:
                ok, msg = system.radio_set(kind, on)
                if not ok:
                    self.toast(self.tr("radio.failed"), 5)
                elif kind == "bluetooth":
                    self.bt["status_at"] = 0.0
                    self.bt["manual_off"].clear()
                    self.bt_keep_off()
            finally:
                busy[kind] = False
                pend.pop(kind, None)
                self.__dict__.setdefault("_radio_cache", {}).pop(kind, None)
        threading.Thread(target=go, daemon=True).start()
