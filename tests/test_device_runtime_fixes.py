"""Runtime fixes of the Armonico Pi and the bridge: the power button's device, a GPIO setup that fails
in part, the stop key with no broker, the sound output after PipeWire restarts, the screen that is not
there, the slower /state polling while the screen sleeps, and the automatic stop of the playback."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bridge as tb  # noqa: E402  the bridge's stand-ins and harness


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Stop(Exception):
    pass


def caps_with_power():
    """The key capability line of a device with KEY_POWER (116), as the kernel writes it."""
    w = 64 if sys.maxsize > 2 ** 32 else 32
    bits, words = 1 << 116, []
    while bits or not words:
        words.append(bits & ((1 << w) - 1))
        bits >>= w
    return " ".join(f"{x:x}" for x in reversed(words)) + "\n"


class PowerButton(unittest.TestCase):
    def setUp(self):
        self.mod = load("pi_power", "device/armonico_pi.py")
        self.t = Path(tempfile.mkdtemp())
        self.sys = self.t / "class" / "input"
        self.sys.mkdir(parents=True)

    def event(self, n, devpath, name, phys, power=True):
        real = self.t / "devices" / devpath
        real.mkdir(parents=True)
        (real / "capabilities").mkdir()
        (real / "capabilities" / "key").write_text(caps_with_power() if power else "0\n")
        (real / "name").write_text(name + "\n")
        (real / "phys").write_text(phys + "\n")
        ev = self.sys / f"event{n}"
        ev.mkdir()
        (ev / "device").symlink_to(real)

    def test_the_cec_remote_is_never_taken_and_the_gpio_key_is(self):
        self.event(0, "platform/soc/fef00700.hdmi/rc/rc0/input0", "vc4-hdmi-0", "vc4-hdmi-0/input0")
        self.event(1, "platform/usbkbd/input1", "USB Keyboard System Control", "usb-0000:01:00.0-1.4/input1")
        self.event(2, "platform/shutdown_button/input/input2", "shutdown_button", "gpio-keys/input0")
        self.assertEqual(self.mod.find_power_button(self.sys), Path("/dev/input/event2"))

    def test_without_the_gpio_key_another_power_key_but_never_cec(self):
        self.event(0, "platform/soc/hdmi/rc/rc0/input0", "vc4-hdmi-0", "vc4-hdmi-0/input0")
        self.assertIsNone(self.mod.find_power_button(self.sys))
        self.event(10, "platform/usbkbd/input10", "USB Keyboard System Control", "usb-1/input1")
        self.assertEqual(self.mod.find_power_button(self.sys), Path("/dev/input/event10"))

    def test_a_device_without_key_power_is_not_taken(self):
        self.event(0, "platform/shutdown_button/input/input0", "shutdown_button", "gpio-keys/input0", power=False)
        self.assertIsNone(self.mod.find_power_button(self.sys))

    def test_it_keeps_looking_at_a_low_rate_and_logs_once(self):
        d = object.__new__(self.mod.Device)
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            if len(sleeps) >= 3:
                raise Stop
        with mock.patch.object(self.mod, "find_power_button", return_value=None), \
                mock.patch.object(self.mod.time, "sleep", side_effect=sleep), \
                mock.patch.object(self.mod, "log") as log, self.assertRaises(Stop):
            d.watch_power()
        self.assertEqual(sleeps, [self.mod.POWER_LOOK] * 3)
        self.assertEqual(log.call_count, 1)

    def test_found_later_it_is_read(self):
        d = object.__new__(self.mod.Device)
        dev = self.t / "event2"
        dev.write_bytes(b"")                       # read gives nothing: the device went away
        finds = iter([None, dev])
        with mock.patch.object(self.mod, "find_power_button", side_effect=lambda: next(finds)), \
                mock.patch.object(self.mod.time, "sleep", side_effect=[None, Stop]), \
                mock.patch.object(self.mod, "log") as log, self.assertRaises(Stop):
            d.watch_power()
        self.assertIn(f"Power button on {dev}", [c.args[0] for c in log.call_args_list])


class Gpio(unittest.TestCase):
    def setUp(self):
        self.mod = load("pi_gpio", "device/armonico_pi.py")
        self.d = object.__new__(self.mod.Device)

    def fake(self, button):
        exc = types.ModuleType("gpiozero.exc")
        pkg = types.ModuleType("gpiozero")
        pkg.Button, pkg.exc = button, exc
        return mock.patch.dict(sys.modules, {"gpiozero": pkg, "gpiozero.exc": exc})

    def test_a_busy_pin_skips_only_its_key(self):
        busy = self.mod.KEY_PINS[3]

        def button(pin, **kw):
            if pin == busy:
                raise RuntimeError("GPIO busy")
            return mock.Mock()
        with self.fake(button), mock.patch.object(self.mod, "log") as log:
            self.d.start_gpio()
        self.assertEqual(len(self.d.keys), 12)
        self.assertEqual(len(self.d.buttons), len(self.mod.BUTTON_PINS))
        self.assertTrue(any("key 3" in c.args[0] and "GPIO busy" in c.args[0] for c in log.call_args_list))

    def test_no_pin_factory_does_not_end_the_service(self):
        class BadPinFactory(Exception):
            pass
        calls = []

        def button(pin, **kw):
            calls.append(pin)
            raise BadPinFactory("Unable to load any default pin factory!")
        with self.fake(button), mock.patch.object(self.mod, "log") as log:
            self.d.start_gpio()                      # no exception
        self.assertEqual((self.d.keys, self.d.buttons), ([], []))
        self.assertEqual(len(calls), 1, "a missing pin factory is not tried on every pin")
        self.assertTrue(any("no pin factory" in c.args[0] for c in log.call_args_list))

    def test_any_import_error_is_logged(self):
        bad = types.ModuleType("gpiozero")
        with mock.patch.dict(sys.modules, {"gpiozero": bad}), mock.patch.object(self.mod, "log") as log:
            self.d.start_gpio()
        self.assertTrue(log.called)


class StopWithoutBroker(unittest.TestCase):
    def setUp(self):
        self.mod = load("pi_stop", "device/armonico_pi.py")
        d = self.d = object.__new__(self.mod.Device)
        d.sampler, d.port, d.pub = mock.Mock(), mock.Mock(), mock.Mock()
        d.port.here, d.port.there = True, False
        d.octave, d.keys_on, d.kb = 4, True, {"mode": "internal", "name": "", "out": "both"}
        d.held, d.alarm, d.song, d.testing, d.swallowed = {}, False, False, False, None
        d.mqtt = mock.Mock()
        d.mqtt.is_connected.return_value = False
        self.t = Path(tempfile.mkdtemp())
        self.run, self.data = self.t / "run", self.t / "data"
        self.data.mkdir()
        self.p = [mock.patch.object(self.mod, "RUN_DIR", self.run), mock.patch.object(self.mod, "DATA_DIR", self.data),
                  mock.patch.object(self.mod, "load_settings", return_value={"stop_button": "key:0"})]
        for p in self.p:
            p.start()
            self.addCleanup(p.stop)

    def req(self):
        f = self.data / "stop-request"
        return f.read_text() if f.exists() else None

    def test_the_stop_key_stops_the_alarm_the_bridge_plays_with_no_broker(self):
        self.run.mkdir()
        (self.run / "piano_alarm_active").touch()
        self.d.key(0, True)
        self.assertEqual(self.req(), "Armonico Pi stop key\n")
        self.d.sampler.note_on.assert_not_called()          # the press was the stop, nothing else
        self.d.pub.assert_not_called()                     # no publish to a broker that is not there

    def test_nothing_playing_the_key_is_a_key(self):
        self.run.mkdir()
        self.d.key(0, True)
        self.assertIsNone(self.req())
        self.d.sampler.note_on.assert_called_once()

    def test_flags_that_cannot_be_read_still_ask_the_bridge(self):
        self.d.key(0, True)                                 # no bridge folder here
        self.assertEqual(self.req(), "Armonico Pi stop key\n")
        self.d.sampler.note_on.assert_called_once()         # and the key still plays

    def test_with_the_broker_up_nothing_changes(self):
        self.run.mkdir()
        (self.run / "piano_alarm_active").touch()
        self.d.mqtt.is_connected.return_value = True
        self.d.key(0, True)                                 # the bridge said nothing plays
        self.assertIsNone(self.req())
        self.d.sampler.note_on.assert_called_once()

    def test_the_stop_button_too(self):
        self.run.mkdir()
        (self.run / "piano_song_active").touch()
        with mock.patch.object(self.mod, "load_settings", return_value={"stop_button": "ok"}):
            self.d.button("ok")
        self.assertEqual(self.req(), "Armonico Pi stop button\n")
        self.assertEqual(self.d.swallowed, "ok")
        self.d.pub.assert_not_called()


class SoundOutput(unittest.TestCase):
    def test_a_lost_output_is_opened_again(self):
        mod = load("pi_out", "device/armonico_pi.py")
        s = object.__new__(mod.Sampler)
        s.mixer = mock.Mock()
        old = s.device = mock.Mock()
        old.alive.return_value = False
        new = mock.Mock()
        with mock.patch.object(mod, "SdlOut", return_value=new) as out, \
                mock.patch.object(mod.time, "sleep"), mock.patch.object(mod, "log"):
            s._watch_output(rounds=1)
        old.close.assert_called_once()
        out.assert_called_once_with(s.mixer.block)
        self.assertIs(s.device, new)

    def test_a_live_output_is_left_alone(self):
        mod = load("pi_out2", "device/armonico_pi.py")
        s = object.__new__(mod.Sampler)
        s.mixer, s.device = mock.Mock(), mock.Mock()
        s.device.alive.return_value = True
        with mock.patch.object(mod, "SdlOut") as out, mock.patch.object(mod.time, "sleep"):
            s._watch_output(rounds=3)
        out.assert_not_called()

    def test_alive_reads_the_status_and_the_callback(self):
        mod = load("pi_out3", "device/armonico_pi.py")
        lib = mock.Mock()
        lib.SDL_InitSubSystem.return_value = 0
        lib.SDL_OpenAudioDevice.return_value = 2
        lib.SDL_GetAudioDeviceStatus.return_value = 1        # playing
        with mock.patch("ctypes.CDLL", return_value=lib):
            out = mod.SdlOut(lambda n: bytes(n))
        self.assertTrue(out.alive())
        lib.SDL_GetAudioDeviceStatus.return_value = 0        # stopped by SDL after a write error
        self.assertFalse(out.alive())
        lib.SDL_GetAudioDeviceStatus.return_value = 1
        out.last -= out.STALL + 1                            # no block asked for: stuck
        self.assertFalse(out.alive())


class NoDisplay(unittest.TestCase):
    def setUp(self):
        self.mod = load("screen_nodisplay", "device/screen.py")
        self.t = Path(tempfile.mkdtemp())

    def test_a_miss_is_counted_and_ends_with_its_own_code(self):
        env = {"ARMONICO_SESSION": "1", "XDG_RUNTIME_DIR": str(self.t)}
        with mock.patch.dict(os.environ, env), mock.patch.object(self.mod, "log"):
            for n in (1, 2):
                with self.assertRaises(SystemExit) as e:
                    self.mod.no_display("kmsdrm not available")
                self.assertEqual(e.exception.code, self.mod.NO_DISPLAY_EXIT)
                self.assertEqual(self.mod.no_display_file().read_text(), f"{n}\n")

    def test_outside_the_session_it_ends_as_before(self):
        with mock.patch.dict(os.environ, {"ARMONICO_SESSION": ""}), self.assertRaises(SystemExit) as e:
            self.mod.no_display("x")
        self.assertEqual(e.exception.code, "No display: x")

    def session_part(self, misses, after):
        text = (ROOT / "device" / "session.sh").read_text()
        part = text[text.index('NO_DISPLAY="$XDG_RUNTIME_DIR'):]
        part = part[:part.index("    exec sleep infinity\nfi\n") + len("    exec sleep infinity\nfi\n")]
        part = part.replace("exec sleep infinity", "echo IDLE; exit 0").replace("NO_DISPLAY_AFTER=90", f"NO_DISPLAY_AFTER={after}")
        if misses is not None:
            (self.t / "armonico-screen.nodisplay").write_text(f"{misses}\n")
        r = subprocess.run(["sh", "-c", part + "\necho START"], env=dict(os.environ, XDG_RUNTIME_DIR=str(self.t), APP="armonico"),
                           capture_output=True, text=True, timeout=10)
        return r.stdout

    def test_the_session_waits_after_a_few_misses(self):
        self.assertIn("IDLE", self.session_part(3, 0))
        self.assertIn("START", self.session_part(2, 0))
        self.assertIn("START", self.session_part(None, 0))
        self.assertIn("START", self.session_part(9, 10 ** 9), "a panel found late at boot gets its chance")

    def test_mark_exit_says_on_purpose(self):
        ready = self.t / "ready"
        ready.write_text(f"{os.getpid()} kmsdrm\n")
        with mock.patch.dict(os.environ, {"ARMONICO_READY_FILE": str(ready)}):
            self.mod.mark_exit()
        self.assertEqual(ready.read_text(), f"exit {os.getpid()}\n")


class ScreenWatch(unittest.TestCase):
    def setUp(self):
        self.mod = load("pi_screen_watch", "device/armonico_pi.py")
        self.d = object.__new__(self.mod.Device)
        self.d.alarm, self.d.song = True, False
        self.d.stop_now = mock.Mock()
        self.t = Path(tempfile.mkdtemp())
        self.ready = self.t / "armonico-screen.ready"
        p = mock.patch.object(self.mod, "SCREEN_READY", self.ready)
        p.start()
        self.addCleanup(p.stop)

    def watch(self, steps):
        """steps: one function per look, run before it."""
        it = iter(steps)

        def sleep(_):
            next(it)()
        with mock.patch.object(self.mod.time, "sleep", side_effect=sleep), mock.patch.object(self.mod, "log"):
            self.d.watch_screen(rounds=len(steps))

    def test_a_screen_that_dies_while_the_alarm_rings_stops_it(self):
        p = subprocess.Popen(["sleep", "30"])
        self.ready.write_text(f"{p.pid} kmsdrm\n")

        def kill():
            p.kill()
            p.wait()
        self.watch([lambda: None, kill])
        self.d.stop_now.assert_called_once_with("Screen stopped")

    def test_a_screen_that_ends_on_purpose_does_not(self):
        p = subprocess.Popen(["sleep", "30"])
        self.ready.write_text(f"{p.pid} kmsdrm\n")

        def end():
            self.ready.write_text(f"exit {p.pid}\n")
            p.kill()
            p.wait()
        self.watch([lambda: None, end])
        self.d.stop_now.assert_not_called()

    def test_nothing_playing_nothing_is_read(self):
        self.d.alarm = False
        with mock.patch.object(self.mod, "local_playing", return_value=False), \
                mock.patch.object(self.mod, "read_screen_ready") as rd:
            self.watch([lambda: None, lambda: None])
        rd.assert_not_called()


class SlowPoll(unittest.TestCase):
    def test_quiet_waits_a_second_and_a_wake_cuts_it_short(self):
        mod = load("screen_poll", "device/screen.py")
        e = object.__new__(mod.Engine)
        e.slow = lambda: True
        slept = []
        with mock.patch.object(mod.time, "sleep", side_effect=slept.append):
            e._pause()
        self.assertAlmostEqual(sum(slept), mod.Engine.POLL_SLOW)
        e.slow, slept[:] = (lambda: False), []
        with mock.patch.object(mod.time, "sleep", side_effect=slept.append):
            e._pause()
        self.assertEqual(slept, [mod.Engine.POLL_FAST])
        state = iter([True, True, False])
        e.slow, slept[:] = (lambda: next(state)), []
        with mock.patch.object(mod.time, "sleep", side_effect=slept.append):
            e._pause()
        self.assertEqual(len(slept), 3)


class AutomaticStop(unittest.TestCase):
    """The bridge's part: a lost broker while a song plays, and a watchdog that ends."""
    setUp = tb.Bridge.setUp
    sh = tb.Bridge.sh
    pubs = tb.Bridge.pubs

    def stub(self, name, body):
        f = self.t / "bin" / name
        f.write_text("#!/bin/bash\n" + body + "\n")
        f.chmod(0o755)

    def test_a_lost_broker_stops_the_song(self):
        self.stub("mosquitto_sub", "exit 1")
        self.sh('aseqdump -p 24 >/dev/null & BROKER_LOST=2; BROKER_PROBE=1\n'
                'touch "$FLAG_SONG"; echo s > "$CURRENT_SONG"\n'
                'run_watchdog 24 & w=$!; sleep 5; touch "$WATCHDOG_ENDING"; kill $w; wait $w\n'
                'pkill -f "sleep 30"; wait')
        self.assertFalse((self.run_dir / "piano_song_active").exists())
        self.assertIn("-t piano/play/stopped_by -m Broker lost -r", self.pubs())

    def test_a_broker_that_answers_keeps_it_playing(self):
        self.stub("mosquitto_sub", "exit 0")
        self.sh('aseqdump -p 24 >/dev/null & BROKER_LOST=2; BROKER_PROBE=1\n'
                'touch "$FLAG_SONG"; echo s > "$CURRENT_SONG"\n'
                'run_watchdog 24 & w=$!; sleep 5; touch "$WATCHDOG_ENDING"; kill $w; wait $w\n'
                'pkill -f "sleep 30"; wait')
        self.assertTrue((self.run_dir / "piano_song_active").exists())

    def test_a_watchdog_killed_from_outside_stops_the_alarm(self):
        self.stub("mosquitto_sub", "exit 0")
        self.sh('aseqdump -p 24 >/dev/null &\n'
                'touch "$FLAG_ALARM"; echo s > "$CURRENT_ALARM_SONG"\n'
                'run_watchdog 24 & w=$!; sleep 1.5; kill $w; wait $w\n'
                'pkill -f "sleep 30"; wait')
        self.assertFalse((self.run_dir / "piano_alarm_active").exists())

    def test_a_watchdog_ended_on_purpose_does_not(self):
        self.stub("mosquitto_sub", "exit 0")
        self.sh('aseqdump -p 24 >/dev/null &\n'
                'touch "$FLAG_ALARM"\n'
                'run_watchdog 24 & WATCHDOG_PID=$!; sleep 1.5; kill_tree "$WATCHDOG_PID"; wait $WATCHDOG_PID\n'
                'pkill -f "sleep 30"; wait')
        self.assertTrue((self.run_dir / "piano_alarm_active").exists())

    def test_a_service_that_ends_while_playing_asks_for_the_stop(self):
        def run():
            subprocess.run([str(tb.BRIDGE), "--stop-if-playing", "Lesson engine stopped"], env=self.env,
                           capture_output=True, timeout=10, check=True)
        run()
        self.assertFalse((self.data / "stop-request").exists(), "nothing plays: nothing asked")
        (self.run_dir / "piano_alarm_active").touch()
        run()
        self.assertEqual((self.data / "stop-request").read_text(), "Lesson engine stopped\n")


if __name__ == "__main__":
    unittest.main()
