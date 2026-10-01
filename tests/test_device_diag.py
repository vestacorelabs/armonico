"""The device's full diagnosis: the IPv4 address, the displays, the update check, the power
button check, the fix request, and that a run always ends in time."""
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


diag = load("armonico_diag_test", "device/diag.py")


def fake_out(table):
    """run_out that answers by the command's first words."""
    def run(*cmd, timeout=2):
        for key, out in table.items():
            if tuple(cmd[:len(key)]) == key:
                return out
        return ""
    return run


class Ipv4(unittest.TestCase):
    def test_default_route_interface_wins(self):
        out = {("ip", "-4", "-o", "addr"): "3: wlan0    inet 10.0.0.7/24 brd 10.0.0.255 scope global dynamic wlan0\n"
                                           "2: eth0    inet 192.168.10.25/24 brd 192.168.10.255 scope global dynamic eth0\n",
               ("ip", "-4", "route"): "default via 192.168.10.1 dev eth0 proto dhcp metric 100\n",
               ("hostname", "-I"): "fd00::5 10.0.0.7 192.168.10.25"}
        with mock.patch.object(diag, "run_out", fake_out(out)):
            self.assertEqual(diag.ipv4(), "192.168.10.25")

    def test_skips_link_local_and_loopback(self):
        out = {("ip", "-4", "-o", "addr"): "2: eth0    inet 169.254.3.4/16 scope global eth0\n"
                                           "4: lo    inet 127.0.0.2/8 scope global lo\n"
                                           "3: wlan0    inet 10.0.0.7/24 scope global wlan0\n",
               ("ip", "-4", "route"): "default via 169.254.0.1 dev eth0\n"}
        with mock.patch.object(diag, "run_out", fake_out(out)):
            self.assertEqual(diag.ipv4(), "10.0.0.7")

    def test_hostname_fallback_never_ipv6(self):
        out = {("hostname", "-I"): "2a02:6680:1::5 fe80::1 169.254.9.9 127.0.1.1 192.168.1.40 "}
        with mock.patch.object(diag, "run_out", fake_out(out)):
            self.assertEqual(diag.ipv4(), "192.168.1.40")

    def test_nothing_gives_empty(self):
        out = {("hostname", "-I"): "fe80::1 ::1"}
        with mock.patch.object(diag, "run_out", fake_out(out)):
            self.assertEqual(diag.ipv4(), "")

    def test_screen_says_no_network(self):
        screen = load("armonico_screen_diag_test", "device/screen.py")
        import sys
        with mock.patch.dict(sys.modules, {"diag": mock.Mock(ipv4=lambda: "")}):
            self.assertEqual(screen.lan_ip(), "no network")
        with mock.patch.dict(sys.modules, {"diag": mock.Mock(ipv4=lambda: "192.168.1.40")}):
            self.assertEqual(screen.lan_ip(), "192.168.1.40")


class Displays(unittest.TestCase):
    def make(self, root, name, status, modes):
        d = Path(root) / name
        d.mkdir()
        (d / "status").write_text(status + "\n")
        (d / "modes").write_text("".join(m + "\n" for m in modes))

    def test_dsi_and_hdmi(self):
        with tempfile.TemporaryDirectory() as root:
            self.make(root, "card1-DSI-1", "connected", ["800x480"])
            self.make(root, "card1-HDMI-A-1", "connected", ["1920x1080", "1280x720", "3840x2160"])
            self.make(root, "card1-HDMI-A-2", "disconnected", [])
            self.make(root, "card1-Writeback-1", "unknown", [])
            got = diag.displays(root)
        # HDMI off (DSI only): the HDMI ports are left out
        # self.assertEqual(got[:2], [{"name": "DSI-1", "connected": True, "mode": (800, 480)},
        #                            {"name": "HDMI-A-1", "connected": True, "mode": (1920, 1080)}])
        # self.assertEqual(got[2], {"name": "HDMI-A-2", "connected": False, "mode": None})
        # self.assertEqual(len(got), 3)
        self.assertEqual(got, [{"name": "DSI-1", "connected": True, "mode": (800, 480)}])

    def test_missing_tree(self):
        self.assertEqual(diag.displays("/nonexistent/drm"), [])


class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "update-check.json"
        diag._update_memo.update(at=0.0, value=None)
        self.patches = [mock.patch.object(diag, "UPDATE_CACHE", self.cache), mock.patch.object(diag, "VERSION", "1.1")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        diag._update_memo.update(at=0.0, value=None)

    def test_newer_release(self):
        rel = {"tag_name": "v1.2.0", "body": "The screen dims\n"}
        with mock.patch.object(diag, "_get_json", return_value=rel) as get:
            self.assertEqual(diag.update_available(), ("1.2", "The screen dims"))
            self.assertIn("/releases/latest", get.call_args[0][0])
        self.assertEqual(json.loads(self.cache.read_text())["tag"], "v1.2.0")

    def test_same_or_older_is_none(self):
        for tag in ("v1.1.0", "1.0.9"):
            diag._update_memo.update(at=0.0, value=None)
            self.cache.unlink(missing_ok=True)
            with mock.patch.object(diag, "_get_json", return_value={"tag_name": tag}):
                self.assertIsNone(diag.update_available())

    def test_falls_back_to_tags(self):
        def get(url, timeout):
            if url.endswith("/releases/latest"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return [{"name": "v1.1.3"}, {"name": "v1.10.0"}, {"name": "nightly"}, {"name": "v1.9"}]
        with mock.patch.object(diag, "_get_json", side_effect=get):
            self.assertEqual(diag.update_available(), ("1.10", ""))

    def test_offline_is_none_and_not_asked_again_at_once(self):
        with mock.patch.object(diag, "_get_json", side_effect=OSError("offline")) as get:
            self.assertIsNone(diag.update_available())
            self.assertIsNone(diag.update_available())
            self.assertEqual(get.call_count, 1)

    def test_cache_is_used_for_6_hours(self):
        self.cache.write_text(json.dumps({"at": time.time() - 3600, "tag": "v2.0", "notes": "n"}))
        with mock.patch.object(diag, "_get_json", side_effect=AssertionError("asked GitHub")):
            self.assertEqual(diag.update_available(), ("2.0", "n"))
        diag._update_memo.update(at=0.0, value=None)
        self.cache.write_text(json.dumps({"at": time.time() - 7 * 3600, "tag": "v2.0", "notes": "n"}))
        with mock.patch.object(diag, "_get_json", return_value={"tag_name": "v2.1"}):
            self.assertEqual(diag.update_available(), ("2.1", ""))


class BootConfig(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.txt"
            p.write_text(text)
            return diag.check_power_button({}, p), diag.check_config_txt({}, p)

    def test_gpio3(self):
        self.assertEqual(self.check("dtoverlay=gpio-shutdown,gpio_pin=3\n")[0].level, diag.OK)
        self.assertEqual(self.check("dtoverlay=gpio-shutdown\n")[0].level, diag.OK)      # 3 is the default

    def test_other_pin_cannot_wake(self):
        c = self.check("dtoverlay=gpio-shutdown,gpio_pin=21\n")[0]
        self.assertEqual(c.level, diag.NOTE)
        self.assertIn("GPIO21", c.detail_en)
        self.assertIn("GPIO21", c.detail_he)

    def test_missing_and_commented(self):
        self.assertEqual(self.check("#dtoverlay=gpio-shutdown\n")[0].level, diag.NOTE)

    def test_spi_i2c(self):
        c = self.check("dtparam=spi=on\ndtparam=i2c_arm=on\n")[1]
        self.assertEqual(c.level, diag.NOTE)
        self.assertIn("SPI", c.detail_he)


class Checks(unittest.TestCase):
    CFG = {"DEVICE": "yes", "MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1", "MQTT_LOCAL": "yes",
           "DATA_DIR": "/nonexistent", "AMIDI_DEVICE": "hw:7,0"}

    def test_every_check_has_both_languages(self):
        out = {("systemctl", "is-active", "mosquitto"): "inactive\n",
               ("systemctl", "is-active"): "active\nfailed\nactive\nactive\n",
               ("timedatectl",): "yes\n", ("vcgencmd", "get_throttled"): "throttled=0x50000\n",
               ("aplay",): "card 0: Headphones [bcm2835 Headphones]", ("amixer",): "Mono: Playback -6 [96%] [0.00dB]",
               ("amidi",): "IO  hw:7,0    Virtual Raw MIDI", ("aconnect",): "client 128: 'Armonico Pi'"}
        with mock.patch.object(diag, "run_out", fake_out(out)):
            got = diag.run_checks(cfg=dict(self.CFG))
        keys = [c.key for c in got]
        for k in ("services", "broker", "lesson_screen", "network", "time", "power", "disk",
                  "sound", "samples", "midi_out", "keys", "gpio", "display"):
            self.assertIn(k, keys)
        for c in got:
            self.assertIn(c.level, (0, 1, 2))
            self.assertTrue(c.label_he and c.detail_he and c.label_en and c.detail_en, c)
        by = {c.key: c for c in got}
        self.assertEqual(by["services"].fix, "services")
        self.assertIn("lessons", by["services"].detail_en)
        self.assertEqual(by["broker"].fix, "mosquitto")
        self.assertEqual(by["power"].level, diag.NOTE)
        self.assertEqual(by["time"].level, diag.OK)
        self.assertEqual(by["sound"].detail_en, "Headphones · 0.00dB")

    def test_a_hanging_check_does_not_hold_the_run(self):
        def stuck(*cmd, timeout=2):
            time.sleep(30)
            return ""
        with mock.patch.object(diag, "run_out", stuck), \
                mock.patch.object(diag, "_mqtt_login", side_effect=lambda *a, **k: time.sleep(30)), \
                mock.patch.object(diag, "ipv4", side_effect=lambda: time.sleep(30)):
            start = time.monotonic()
            got = diag.run_checks(timeout=0.5, cfg=dict(self.CFG))
            self.assertLess(time.monotonic() - start, 1.5)
        late = [c for c in got if "0.5" in c.detail_en]
        self.assertTrue(late)
        self.assertTrue(all(c.level == diag.NOTE and c.label_he for c in late))

    def test_a_failing_check_is_reported(self):
        with mock.patch.object(diag, "run_out", side_effect=RuntimeError("boom")):
            got = diag.run_checks(cfg=dict(self.CFG))
        self.assertTrue(any("boom" in c.detail_en for c in got))

    def test_broker_login(self):
        import socket
        import threading
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def broker():
            conn, _ = srv.accept()
            conn.recv(1024)
            conn.sendall(b"\x20\x02\x00\x00")
            conn.close()
        threading.Thread(target=broker, daemon=True).start()
        try:
            c = diag.check_broker({"MQTT_HOST": "127.0.0.1", "MQTT_PORT": str(port), "MQTT_USER": "u", "MQTT_PASS": "p"})
        finally:
            srv.close()
        self.assertEqual(c.level, diag.OK)
        self.assertTrue(c.detail_he.startswith("מחובר"))


class Fix(unittest.TestCase):
    def test_request_file_and_steps(self):
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "device-fix-request"
            seen = []

            def root_unit():
                while not req.exists():
                    time.sleep(0.05)
                seen.append(req.read_text().strip())
                req.unlink()
            import threading
            threading.Thread(target=root_unit, daemon=True).start()
            ok = diag.Check(0, "broker", "", "", "", "", "")
            with mock.patch.object(diag, "FIX_REQUEST", req), mock.patch.object(diag, "check_broker", return_value=ok):
                steps = diag.fix("mosquitto", cfg={}, timeout=5)
        self.assertEqual(seen, ["mosquitto"])
        self.assertEqual([s["ok"] for s in steps], [True, True])
        self.assertEqual(steps[0]["he"], "הפעלה מחדש של mosquitto")

    def test_nobody_takes_it(self):
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "device-fix-request"
            with mock.patch.object(diag, "FIX_REQUEST", req):
                steps = diag.fix("services", cfg={}, timeout=0.3)
            self.assertFalse(req.exists())
        self.assertEqual([s["ok"] for s in steps], [False, False])

    def test_unknown_action(self):
        self.assertEqual(diag.fix("rm -rf /"), [])

    def test_apply_only_knows_its_actions(self):
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "device-fix-request"
            req.write_text("reboot\n")
            with mock.patch.object(diag, "FIX_REQUEST", req), mock.patch.object(diag.subprocess, "run") as run:
                diag.apply()
            run.assert_not_called()
            self.assertFalse(req.exists())
            req.write_text("mosquitto\n")
            with mock.patch.object(diag, "FIX_REQUEST", req), mock.patch.object(diag.subprocess, "run") as run:
                diag.apply()
            self.assertEqual(run.call_args[0][0], ["systemctl", "restart", "mosquitto"])

    def test_apply_never_reads_through_a_link_or_waits_on_a_fifo(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "elsewhere"
            target.write_text("mosquitto\n")
            req = Path(d) / "device-fix-request"
            req.symlink_to(target)
            with mock.patch.object(diag, "FIX_REQUEST", req), mock.patch.object(diag.subprocess, "run") as run:
                diag.apply()
            run.assert_not_called()
            self.assertFalse(req.is_symlink())
            self.assertEqual(target.read_text(), "mosquitto\n")
            os.mkfifo(req)
            with mock.patch.object(diag, "FIX_REQUEST", req), mock.patch.object(diag.subprocess, "run") as run:
                diag.apply()                                   # returns at once, no writer needed
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
