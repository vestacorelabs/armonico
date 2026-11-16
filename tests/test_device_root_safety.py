"""The device's runtime fixes. Root never writes through a link in the service user's
folders, a backup cannot carry a request to root, a restore that stops part way comes back up, the
USB keyboard has channels of its own and lets go when it goes, and the threads of the keys' program
never end on one error."""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
import backup as B                                   # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_paths(t):
    paths = B.Paths(data=t / "data", etc=t / "etc", songs=t / "data" / "songs", app=ROOT)
    paths.data.mkdir(parents=True)
    paths.etc.mkdir()
    return paths


def archive(t, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        ent = []
        for n, b in files.items():
            z.writestr(n, b)
            ent.append({"path": n, "sha256": hashlib.sha256(b).hexdigest()})
        z.writestr("manifest.json", json.dumps({"format": "armonico-backup", "format_version": 1,
                                                "install": "regular", "files": ent}))
    p = t / "x.armonico"
    p.write_bytes(buf.getvalue())
    return p


class Services:
    def __init__(self):
        self.log = []

    def stop(self):
        self.log.append("stop")

    def start(self):
        self.log.append("start")

    def check(self, wait=0):
        return True, ""


class RootWrites(unittest.TestCase):
    def test_the_status_never_goes_through_a_link(self):
        ra = load("ra_fix", "device/root_actions.py")
        t = Path(tempfile.mkdtemp())
        victim = t / "victim"
        victim.write_text("precious\n")
        os.chmod(victim, 0o600)
        os.symlink(victim, t / ".device-request.status.tmp")
        with mock.patch.multiple(ra, REQUEST=t / "device-request", STATUS=t / "device-request.status"):
            (t / "device-request").write_text(json.dumps({"id": "ab", "action": "nope"}))
            ra.main()
        self.assertEqual(victim.read_text(), "precious\n")
        self.assertEqual(victim.stat().st_mode & 0o777, 0o600)
        st = t / "device-request.status"
        self.assertFalse(st.is_symlink())
        self.assertEqual(json.loads(st.read_text())["state"], "fail")
        self.assertEqual(st.stat().st_mode & 0o777, 0o644)

    def test_a_request_that_is_a_link_is_not_read(self):
        ra = load("ra_fix2", "device/root_actions.py")
        t = Path(tempfile.mkdtemp())
        real = t / "elsewhere"
        real.write_text(json.dumps({"id": "ab", "action": "reboot"}))
        os.symlink(real, t / "device-request")
        with mock.patch.multiple(ra, REQUEST=t / "device-request", STATUS=t / "device-request.status"), \
                mock.patch.object(ra, "run") as run:
            ra.main()
        run.assert_not_called()
        self.assertFalse((t / "device-request").exists())

    def test_the_copy_to_a_usb_stick_never_goes_through_a_link(self):
        t = Path(tempfile.mkdtemp())
        paths = make_paths(t)
        (paths.data / "x.json").write_text("{}")
        stick = t / "stick"
        stick.mkdir()
        victim = t / "victim"
        victim.write_text("root-owned\n")
        name = B.backup_name()
        os.symlink(victim, stick / ("." + name + ".part"))
        orig = B.Local.__init__
        with mock.patch.object(B.Local, "__init__", lambda self, paths=None, roots=None: orig(self, paths, [stick])):
            r = B._carry_out({"action": "create", "where": {"kind": "local"}, "path": str(stick)}, paths, None, None)
        self.assertEqual(victim.read_text(), "root-owned\n")
        self.assertTrue(B.validate(r["file"], paths).ok)
        self.assertEqual([p.name for p in stick.iterdir() if p.name.endswith(".part") and not p.is_symlink()], [])

    def test_the_report_is_put_in_place_and_not_written_through_a_link(self):
        text = (ROOT / "device" / "report.sh").read_text()
        self.assertIn('install -m 644 -T "$TMP" "$OUT"', text)
        self.assertNotIn('> "$OUT"', text)


class UsbDisks(unittest.TestCase):
    def setUp(self):
        self.ra = load("ra_usb", "device/root_actions.py")
        drives = [{"dev": "/dev/sda1", "disk": "/dev/sda", "fs": "ext4", "mount": "", "usable": True, "label": ""},
                  {"dev": "/dev/sda2", "disk": "/dev/sda", "fs": "ext4", "mount": "", "usable": True, "label": ""}]
        self.ra.backup_mod = lambda: types.SimpleNamespace(usb_drives=lambda: drives)
        self.ra.disk_of = lambda path: "/dev/mmcblk0"

    def lsblk(self, mounts):
        tree = {"blockdevices": [{"path": "/dev/sda", "mountpoints": [None], "children": [
            {"path": "/dev/sda1", "mountpoints": [mounts[0]]}, {"path": "/dev/sda2", "mountpoints": [mounts[1]]}]}]}
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(tree), "")

    def test_a_disk_the_pi_runs_from_is_refused(self):
        self.ra.run = self.lsblk(["/boot/firmware", "/"])     # a Pi booted from a USB SSD
        with self.assertRaises(ValueError):
            self.ra.do_usb_format(lambda *a: None, {"disk": "/dev/sda", "fs": "ext4"})
        with self.assertRaises(ValueError):
            self.ra.do_usb_mount(lambda *a: None, {"dev": "/dev/sda1"})
        self.ra.run = self.lsblk([None, "/mnt/data"])          # anything mounted outside /media/armonico
        with self.assertRaises(ValueError):
            self.ra.usb_entry("/dev/sda", "disk")
        self.ra.disk_of = lambda path: "/dev/sda"
        self.ra.run = self.lsblk([None, None])
        with self.assertRaises(ValueError):
            self.ra.usb_entry("/dev/sda", "disk")

    def test_a_stick_of_its_own_is_taken(self):
        self.ra.run = self.lsblk(["/media/armonico/ARMONICO", None])
        self.assertEqual(len(self.ra.usb_entry("/dev/sda", "disk")), 2)

    def test_when_lsblk_cannot_tell_nothing_is_erased(self):
        self.ra.run = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no")
        with self.assertRaises(ValueError):
            self.ra.usb_entry("/dev/sda", "disk")


class Restore(unittest.TestCase):
    def test_a_backup_with_a_request_to_root_is_refused(self):
        t = Path(tempfile.mkdtemp())
        paths = make_paths(t)
        for name in ("device-request", "device-fix-request", "update-request", "device-mode-request",
                     "reset-request", "edit-mode", "backups/restore-state.json"):
            p = archive(t, {"data/a.json": b"1", "data/" + name: b"x"})
            chk = B.validate(p, paths)
            self.assertFalse(chk.ok, name)
            self.assertEqual(chk.reason_key, "request_file")
            # and skipped if it ever got as far as being applied
            with open(p, "rb") as f:
                B.apply_data(f, paths)
            self.assertFalse((paths.data / name).exists(), name)
            self.assertTrue((paths.data / "a.json").exists())

    def test_a_restore_that_stops_part_way_goes_back_and_comes_up(self):
        t = Path(tempfile.mkdtemp())
        paths = make_paths(t)
        (paths.data / "keep.json").write_text("before")
        (paths.data / "songs").mkdir()
        p = archive(t, {"data/a.json": b"1", "data/songs": b"a file where a folder is"})
        s = Services()
        r = B.restore(p, paths=paths, services=s, snapshot=True)
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(s.log[-1], "start")
        self.assertEqual(s.log.count("stop"), s.log.count("start"))
        self.assertEqual((paths.data / "keep.json").read_text(), "before")

    def test_an_undo_that_failed_can_be_tried_again(self):
        t = Path(tempfile.mkdtemp())
        paths = make_paths(t)
        before = paths.backups / "before-restore-x.armonico"
        paths.backups.mkdir(parents=True, exist_ok=True)
        before.write_bytes(b"x")
        B.save_state(paths, {"result": "restored", "before": str(before), "undone": False})
        with mock.patch.object(B, "restore", return_value={"ok": False, "key": "failed"}):
            B.undo_last(paths=paths, services=Services())
        self.assertTrue(B.last_restore(paths)["can_undo"])
        with mock.patch.object(B, "restore", return_value={"ok": True, "key": "restored"}):
            B.undo_last(paths=paths, services=Services())
        self.assertFalse(B.last_restore(paths)["can_undo"])


class Chan:
    def __init__(self):
        self.busy, self.vol = False, 1.0

    def get_busy(self):
        return self.busy

    def play(self, snd):
        self.busy = True

    def set_volume(self, v):
        self.vol = v

    def fadeout(self, ms):
        self.busy = False


def sampler(mod, n=8):
    s = object.__new__(mod.Sampler)
    s.lock = threading.RLock()
    s.playing, s.sustained, s.pedal, s.master = {}, set(), {}, 1.0
    s.chans = [Chan() for _ in range(n)]
    s.started, s.level, s.fade, s.gain, s.limit = {}, {}, {}, {}, 1.0
    s.program, s.voice_of, s.smart, s.last_on = {}, {}, set(), {}
    s.sounds = {n: object() for n in range(21, 109)}
    return s


class Stop(Exception):
    pass


class SamplerFixes(unittest.TestCase):
    def setUp(self):
        self.mod = load("pi_fix", "device/armonico_pi.py")

    def tick(self, s, n=3, fail=None):
        calls = {"n": 0}

        def sleep(x):
            calls["n"] += 1
            if calls["n"] >= n:
                raise Stop

        clock = iter(range(1000, 100000))                # a second between two ticks
        fake = types.SimpleNamespace(monotonic=lambda: float(next(clock)), sleep=sleep)
        with mock.patch.object(self.mod, "time", fake):
            try:
                s._tick()
            except Stop:
                pass
        return calls["n"]

    def test_held_notes_whose_sample_ended_do_not_hold_the_level_down(self):
        s = sampler(self.mod, 12)
        for n in range(60, 68):
            s.note_on(self.mod.DEV_CH, n, 127)
        self.tick(s)
        self.assertLess(s.limit, 1.0)
        for c in s.chans:
            c.busy = False                               # the samples ended, the keys still held
        s.chans[0].busy = True
        self.tick(s)
        self.assertEqual(s.limit, 1.0)

    def test_an_error_in_the_levels_does_not_end_them(self):
        s = sampler(self.mod)
        bad = mock.Mock(get_busy=mock.Mock(side_effect=RuntimeError("ALSA gone")))
        s.chans = [bad]
        self.assertEqual(self.tick(s, n=3), 3)          # it went on after the error

    def test_the_usb_keyboard_has_channels_of_its_own(self):
        s = sampler(self.mod)
        s.midi(bytes([0x90, 60, 100]))                  # the lesson engine's note on channel 1
        s.midi(bytes([0x90, 60, 100]), base=self.mod.USB_CH)   # the player's, on a USB keyboard
        self.assertIn((0, 60), s.playing)
        self.assertIn((self.mod.USB_CH, 60), s.playing)
        s.midi(bytes([0xB0, 123, 0]))                   # the engine's all notes off at the lesson's end
        self.assertNotIn((0, 60), s.playing)
        self.assertIn((self.mod.USB_CH, 60), s.playing)
        self.assertEqual(s.family(self.mod.USB_CH + 9), -1)   # its channel 10 still plays the drums

    @unittest.skipIf(importlib.util.find_spec("numpy") is None, "numpy is not installed")
    def test_a_44k_sample_is_resampled_to_the_mixers_rate(self):
        t = Path(tempfile.mkdtemp())
        f = t / "60.wav"
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x01\x00" * 44100)
        s = object.__new__(self.mod.Sampler)
        snd = s._sound(f)
        self.assertAlmostEqual(len(snd), self.mod.MIX_RATE, delta=2)       # one second at the mixer's rate


class SplitMidi(unittest.TestCase):
    def test_system_common_lengths(self):
        mod = load("pi_split", "device/armonico_pi.py")
        got = mod.split_midi(bytes([0xF6, 0x90, 60, 100, 0xF1, 0x10, 0xF2, 1, 2, 0xF3, 5, 0x80, 60, 0]))
        self.assertEqual(got, [b"\xF6", bytes([0x90, 60, 100]), bytes([0xF1, 0x10]), bytes([0xF2, 1, 2]),
                               bytes([0xF3, 5]), bytes([0x80, 60, 0])])
        # no running status after one: the bytes after a tune request are not a note
        self.assertEqual(mod.split_midi(bytes([0x90, 60, 100, 0xF6, 61, 100])), [bytes([0x90, 60, 100]), b"\xF6"])


class DeviceFixes(unittest.TestCase):
    def device(self):
        mod = load("pi_dev", "device/armonico_pi.py")
        d = object.__new__(mod.Device)
        d.sampler, d.port, d.pub = mock.Mock(), mock.Mock(), mock.Mock()
        d.sampler.voice_of = {}
        d.port.here, d.port.there = True, False
        d.octave, d.set_octave = 4, mock.Mock()
        d.keys_on, d.kb, d.kb_seen = True, {"mode": "both", "name": "", "out": "both"}, []
        d.held, d.alarm, d.song, d.testing = {}, False, False, False
        return mod, d

    def test_the_stop_button_also_asks_the_bridge_through_a_file(self):
        # with an external broker gone while the alarm rings, the MQTT stop goes nowhere: the file
        # the bridge reads every second still stops it
        mod, d = self.device()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(mod, "DATA_DIR", Path(tmp)), \
                mock.patch.object(mod, "load_settings", return_value={"stop_button": "key:3"}):
            d.alarm = True
            d.key(3, True)
            d.pub.assert_any_call("piano/stop", "Armonico Pi stop key")
            self.assertEqual((Path(tmp) / "stop-request").read_text(), "Armonico Pi stop key\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["stop-request"], "no temporary file left")

    def test_an_unplugged_keyboard_lets_go_of_its_notes_and_pedal(self):
        mod, d = self.device()
        d.external(bytes([0x90, 60, 100]))
        d.external(bytes([0x91, 64, 100]))
        d.external(bytes([0x90, 67, 100]))
        d.external(bytes([0x80, 67, 0]))
        d.external(bytes([0xB0, 64, 127]))
        d.sampler.midi.assert_called_with(bytes([0xB0, 64, 127]), base=mod.USB_CH)
        d.sampler.reset_mock()
        d.port.send.reset_mock()
        d.release_external()
        d.sampler.set_pedal.assert_called_once_with(mod.USB_CH, False)
        self.assertEqual(sorted(c.args for c in d.sampler.note_off.call_args_list),
                         [(mod.USB_CH, 60), (mod.USB_CH + 1, 64)])
        sent = [c.args[0] for c in d.port.send.call_args_list]
        self.assertEqual(sent, [[0xB0, 64, 0], [0x80, 60, 0], [0x81, 64, 0]])   # the bridge: piano/pedal off
        d.port.send.reset_mock()
        d.release_external()                             # nothing twice
        d.port.send.assert_not_called()

    def test_the_watch_lets_go_when_the_keyboard_is_gone(self):
        mod, d = self.device()
        d.port.keyboards.return_value = {}
        d.port.follow_external.return_value = {(20, 0)}
        d.release_external = mock.Mock()
        with mock.patch.object(mod.time, "sleep", side_effect=Stop):
            with self.assertRaises(Stop):
                d.watch_keyboards()
        d.release_external.assert_called_once()

    def route(self, kb_mode, out, inputs):
        """Where the sound goes after one look: (here, there)."""
        mod, d = self.device()
        d.kb = {"mode": kb_mode, "name": "PSR-E363", "out": out}
        d.port.keyboards.return_value = {"PSR-E363": (20, 0)}
        d.port.keyboard_inputs.return_value = {"PSR-E363": (20, 0)} if inputs else {}
        d.port.follow_external.return_value = set()
        with mock.patch.object(mod.time, "sleep", side_effect=Stop):
            with self.assertRaises(Stop):
                d.watch_keyboards()
        return d.port.here, d.port.there

    def test_automatic_sound_comes_out_of_one_place_only(self):
        """07.11.2026: with a USB keyboard every note played twice, on the device and on the keyboard (out "both")."""
        self.assertEqual(self.route("external", "auto", True), (False, True))     # a keyboard with a sound: there alone
        self.assertEqual(self.route("both", "auto", True), (False, True))
        self.assertEqual(self.route("external", "auto", False), (True, False))    # a controller with no sound: here
        self.assertEqual(self.route("internal", "auto", True), (True, False))     # connected, not chosen: here
        self.assertEqual(self.route("external", "both", True), (True, True))      # both, chosen on purpose
        self.assertEqual(self.route("external", "device", True), (True, False))

    def test_the_octave_never_follows_the_usb_keyboard_in_any_part_of_a_lesson(self):
        mod, d = self.device()
        d.mqtt = None
        msg = lambda p: types.SimpleNamespace(topic="piano/device/expect", payload=p.encode())
        handler = self.handler(mod, d)
        handler(msg("-1"))                               # the demo, the count-in, a pause
        d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_not_called()
        d.key(0, True)
        self.assertEqual(d.held, {0: 60})                # no note to wait for: the key's own octave
        handler(msg("72"))
        d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_not_called()
        handler(msg(""))                                 # the lesson is over
        d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_called_once_with(6)

    def test_an_expect_not_sent_again_runs_out(self):
        mod, d = self.device()
        handler = self.handler(mod, d)
        now = [1000.0]
        with mock.patch.object(mod.time, "monotonic", lambda: now[0]):
            handler(types.SimpleNamespace(topic="piano/device/expect", payload=b"72"))
            self.assertEqual(d.current_expect(), 72)
            now[0] += mod.EXPECT_TTL + 1
            self.assertIsNone(d.current_expect())
            d.external(bytes([0x90, 84, 90]))
        d.set_octave.assert_called_once_with(6)

    def handler(self, mod, d):
        """on_message as start_mqtt gives it to paho."""
        fake = types.SimpleNamespace(Client=lambda *a, **k: mock.Mock(), CallbackAPIVersion=types.SimpleNamespace(VERSION2=2))
        paho = types.ModuleType("paho")
        paho.mqtt = types.ModuleType("paho.mqtt")
        paho.mqtt.client = fake
        with mock.patch.dict(sys.modules, {"paho": paho, "paho.mqtt": paho.mqtt, "paho.mqtt.client": fake}):
            d.start_mqtt()
        return lambda m: d.mqtt.on_message(None, None, m)

    def test_no_message_ends_the_mqtt_thread(self):
        mod, d = self.device()
        handler = self.handler(mod, d)
        for topic, payload in (("piano/device/expect", "²"), ("piano/device/note", "on ²"),
                               ("piano/device/voice/set", "¹"), ("piano/device/octave/set", "+²"),
                               ("piano/device/keyboard/set", "[1]")):
            handler(types.SimpleNamespace(topic=topic, payload=payload.encode()))
        d.sampler.note_on.assert_not_called()
        d.touch_note = mock.Mock(side_effect=RuntimeError("boom"))
        handler(types.SimpleNamespace(topic="piano/device/note", payload=b"on 60"))   # logged, not raised

    def test_the_voice_preview_leaves_a_touched_note_on(self):
        mod, d = self.device()
        d.sampler = sampler(mod)
        d.sampler.voice_of = {mod.DEV_CH: 0}
        d.warm_voice = lambda: None
        timers = []
        with mock.patch.object(mod, "save_settings"), \
                mock.patch.object(mod.threading, "Timer", lambda s, f: timers.append(f) or mock.Mock()):
            d.set_voice(0)
        d.touch_note(60, True)                           # middle C touched before the preview ended
        timers[0]()
        self.assertIn((mod.DEV_CH, 60), d.sampler.playing)

    def test_the_voice_is_warmed_again_where_the_octave_moved(self):
        mod, d = self.device()
        del d.set_octave
        d.sampler.voice_of = {mod.DEV_CH: 3}
        d.warm_voice = mock.Mock()
        with mock.patch.object(mod, "save_settings"):
            d.set_octave(5)
        for _ in range(100):
            if not getattr(d, "warming", False):
                break
            threading.Event().wait(0.01)
        d.warm_voice.assert_called()

    def test_the_power_request_is_written_whole(self):
        mod, d = self.device()
        t = Path(tempfile.mkdtemp())
        d.power_down_at = 5
        with mock.patch.object(mod, "DATA_DIR", t), mock.patch.object(mod.os, "replace", wraps=os.replace) as rep:
            d.power_held(5)
        rep.assert_called_once()
        self.assertEqual(json.loads((t / "device-request").read_text())["action"], "shutdown")
        self.assertEqual(sorted(p.name for p in t.iterdir()), ["device-request"])


class Virmidi(unittest.TestCase):
    def test_the_device_amidi_names_is_listened_to_and_looked_for_again(self):
        mod = load("pi_vm", "device/armonico_pi.py")
        port = object.__new__(mod.Port)
        port.am = types.SimpleNamespace(ALSAError=OSError)
        port.port = mock.Mock()
        ports = [types.SimpleNamespace(client_name="Virtual Raw MIDI 1-0", client_id=20, port_id=0)]
        port.client = mock.Mock(list_ports=lambda input=True: ports)
        with mock.patch.dict(mod.ENV, {"AMIDI_DEVICE": "hw:7,0"}):
            self.assertFalse(port.listen_virmidi(quiet=True))   # another card's: not the lesson engine's
            ports.append(types.SimpleNamespace(client_name="Virtual Raw MIDI 7-0", client_id=24, port_id=0))
            self.assertTrue(port.listen_virmidi(quiet=True))
        port.port.connect_from.assert_called_once_with(ports[1])


class Versions(unittest.TestCase):
    def test_a_pre_release_is_older_than_its_release(self):
        diag = load("diag_fix", "device/diag.py")
        v = diag._version
        self.assertLess(v("v1.1.0-rc2"), v("1.1.0"))
        self.assertLess(v("1.1.0-rc2"), v("1.1.0-rc10"))
        self.assertEqual(v("1.1"), v("v1.1.0"))
        self.assertGreater(v("1.2"), v("1.1.9"))
        self.assertIsNone(v("latest"))
        self.assertIsNone(v(None))

    def test_a_bad_cache_is_ignored(self):
        diag = load("diag_fix2", "device/diag.py")
        t = Path(tempfile.mkdtemp())
        cache = t / "update-check.json"
        cache.write_text(json.dumps({"at": "yesterday", "tag": 5}))
        with mock.patch.object(diag, "UPDATE_CACHE", cache), \
                mock.patch.object(diag, "latest_release", return_value=("v99.0", "notes")), \
                mock.patch.object(diag, "VERSION", "1.1"):
            self.assertEqual(diag.update_available(), ("99.0", "notes"))

    def test_the_fix_request_is_written_whole(self):
        diag = load("diag_fix3", "device/diag.py")
        t = Path(tempfile.mkdtemp())
        with mock.patch.object(diag, "FIX_REQUEST", t / "device-fix-request"), \
                mock.patch.object(diag.os, "replace", wraps=os.replace) as rep:
            diag.fix("services", timeout=0.01, cfg={})
        self.assertEqual(Path(rep.call_args.args[1]), t / "device-fix-request")   # put in place by a rename
        self.assertEqual(sorted(p.name for p in t.iterdir()), [])   # nobody took it: removed again, no temp left


class Prepare(unittest.TestCase):
    def test_a_sample_is_written_whole_or_not_at_all(self):
        mod = load("pi_prep", "device/armonico_pi.py")
        t = Path(tempfile.mkdtemp())
        outs = []

        def ffmpeg(cmd, **k):
            out = Path(cmd[-1])
            outs.append(out)
            out.write_bytes(b"RIFF")
            return subprocess.CompletedProcess(cmd, 0)

        fonts = t / "fonts"
        fonts.mkdir()
        for label in ("Regular", "ExtraBold", "Medium", "Bold"):
            (fonts / f"Heebo-{label}.ttf").write_bytes(b"")
            (fonts / f"Heebo-Hebrew-{label}.ttf").write_bytes(b"")
        with mock.patch.multiple(mod, SAMPLE_DIR=t / "s", FONT_DIR=fonts, GM_SAMPLES=t / "none", GM_DIR=t / "gm",
                                 shutil_which=lambda c: "/usr/bin/ffmpeg",
                                 sample_for=lambda n: (Path("x.flac"), 0)), \
                mock.patch.object(mod.subprocess, "run", ffmpeg):
            try:
                mod.prepare()
            except SystemExit:                           # fontTools missing here: the samples are done by then
                pass
        self.assertTrue(all(o.name.startswith(".") and o.name.endswith(".part") for o in outs))
        names = sorted(p.name for p in (t / "s").iterdir())
        self.assertEqual(len([n for n in names if n.endswith(".wav")]), 88)
        self.assertEqual([n for n in names if n.startswith(".")], [])
        self.assertIn("recipe.txt", names)


class FullReset(unittest.TestCase):
    def test_the_sum_is_the_one_of_the_release_file(self):
        text = (ROOT / "device" / "full_reset.sh").read_text()
        awk = re.search(r"awk -v f=\"armonico-\$TAG.zip\" '([^']*)'", text).group(1)
        sums = ("a" * 64 + "  armonico-v1.2-docs.zip\n" + "b" * 64 + " *armonico-v1.2.zip\n")
        got = subprocess.run(["awk", "-v", "f=armonico-v1.2.zip", awk], input=sums, capture_output=True, text=True)
        self.assertEqual(got.stdout.strip(), "b" * 64)
        got = subprocess.run(["awk", "-v", "f=armonico-v1.3.zip", awk], input=sums, capture_output=True, text=True)
        self.assertEqual(got.stdout.strip(), "")

    def test_the_work_folder_is_not_in_a_folder_open_to_everyone(self):
        text = (ROOT / "device" / "full_reset.sh").read_text()
        self.assertIn('WORK="${ARMONICO_RESET_WORK:-/var/lib/$APP-reset}"', text)
        self.assertIn("mktemp -d", text)
        self.assertNotIn("/var/tmp/armonico-reset", (ROOT / "device" / "reset_screen.py").read_text())


if __name__ == "__main__":
    unittest.main()
