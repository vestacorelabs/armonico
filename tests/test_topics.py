"""The roots of the topics (lessons/piano_topics.py): piano/ on a regular install, pianoDevice/ on the
Armonico Pi and pianoAndroid/ in the app, so the three are three pianos on one broker. Telegram has one
list of commands for all of them, and a word after a command names the piano."""
import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lessons"))
import piano_topics as T  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Roots(unittest.TestCase):
    def test_each_install_has_its_root(self):
        self.assertEqual(T.root({}), "piano")
        self.assertEqual(T.root({"DEVICE": "no"}), "piano")
        self.assertEqual(T.root({"DEVICE": "yes"}), "pianoDevice")
        self.assertEqual(T.root({"ARMONICO_ANDROID": "yes"}), "pianoAndroid")

    def test_one_list_of_commands_and_the_answers_name_the_piano(self):
        dev = {"DEVICE": "yes"}
        self.assertEqual(T.commands_in("Stop it first: /pianostop, then /lessons or /lessonstop", dev),
                         "Stop it first: /pianostop device, then /lessons device or /lessonstop device")
        self.assertEqual(T.commands_in("/seqadd name 60 62", {"ARMONICO_ANDROID": "yes"}), "/seqadd android name 60 62")
        self.assertEqual(T.commands_in("see /pianoplay and a/lesson path", {}), "see /pianoplay and a/lesson path")
        self.assertEqual(T.commands_in("/lessonsX /pianoplayer", dev), "/lessonsX /pianoplayer")
        self.assertEqual(T.split_target("Device ode_to_joy"), ("device", "ode_to_joy"))
        self.assertEqual(T.split_target("ode_to_joy"), ("", "ode_to_joy"))

    def test_the_bridge_names_the_device_the_same_way(self):
        src = (ROOT / "bridge" / "piano_bridge.sh").read_text()
        func = src[src.index("device_commands() {"):]
        func = func[:func.index("\n}\n") + 3]
        text = "Use /pianoplay x, /seqlearn a, /lessons, /lessonstop and /getmidi"
        out = subprocess.run(["bash", "-c", func + "device_commands"], input=text, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), T.commands_in(text, {"DEVICE": "yes"}))
        self.assertNotIn('"piano/', src, "every topic of the bridge starts with $TOPICS")
        self.assertIn('if [ "${DEVICE:-}" = "yes" ]; then TOPICS="pianoDevice"; else TOPICS="piano"; fi', src)


class HomeAssistant(unittest.TestCase):
    def setUp(self):
        self.ha = load("ha_setup_topics", ROOT / "setup" / "ha_setup.py")
        self.base = (ROOT / "home-assistant" / "piano.yaml").read_text()

    def test_the_regular_package_is_piano_yaml_itself(self):
        self.assertEqual(self.ha.variant_text(self.base, "piano"), self.base)
        self.assertIn("mine: \"{{ target in ['', 'piano'] }}\"", self.base)

    def test_the_device_package_is_its_own(self):
        text = self.ha.variant_text(self.base, "pianoDevice")
        self.assertNotIn("piano/", text)
        # the big piano's sensors only where it asks whether the big piano is there (who answers a command)
        self.assertEqual([l.strip()[:5] for l in text.splitlines() if "sensor.piano_" in l], ["mine:"])
        self.assertIn("'/pianostatus'", text)            # the same commands as the big piano's
        self.assertIn('state_topic: "pianoDevice/status"', text)
        self.assertIn("unique_id: armonico_pi_status", text)
        self.assertIn("!secret armonico_device_admin_secret", text)
        try:
            import yaml  # noqa: F401  (the CI installs only requirements.txt: no PyYAML there)
        except ImportError:
            self.skipTest("PyYAML is missing")
        pkg = self.ha.load_package("s", top="pianoDevice")
        self.assertTrue(all(a["id"].startswith("armonico_pi_") for a in pkg["automation"]))
        self.assertTrue(all(a["alias"].startswith("Armonico Pi: ") for a in pkg["automation"]))
        cmds = next(a for a in pkg["automation"] if a["id"].endswith("telegram_commands"))
        self.assertEqual(cmds["conditions"], ["{{ mine }}"])

    def test_a_named_command_reaches_only_its_piano(self):
        """The packages' templates, rendered with jinja2 as Home Assistant renders them."""
        try:
            import jinja2
        except ImportError:
            self.skipTest("jinja2 is missing")
        env = jinja2.Environment()

        def mine(top, words, have):
            text = self.ha.variant_text(self.base, top)
            env.globals["states"] = lambda e: "on" if e in have else "unknown"
            v = {"words": words}
            for key in ("target", "args", "mine"):
                expr = re.search(rf'^      {key}: "(.*)"$', text, re.M).group(1)
                v[key] = env.from_string(expr).render(**v)
            return v["mine"] == "True", v["args"]

        both = {"sensor.piano_status", "sensor.armonico_pi_status"}   # a piano is there when its status sensor is
        self.assertEqual(mine("piano", ["ode"], both), (True, "ode"))
        self.assertEqual(mine("pianoDevice", ["ode"], both), (False, "ode"))
        self.assertEqual(mine("piano", ["device", "ode"], both), (False, "ode"))
        self.assertEqual(mine("pianoDevice", ["device", "ode"], both), (True, "ode"))
        only_device = {"sensor.armonico_pi_status"}
        self.assertEqual(mine("pianoDevice", ["ode"], only_device), (True, "ode"))
        self.assertEqual(mine("pianoAndroid", ["ode"], only_device), (False, "ode"))
        self.assertEqual(mine("pianoAndroid", ["android"], both), (True, ""))

    def test_the_files_for_a_copy_by_hand_are_up_to_date(self):
        for top, v in self.ha.VARIANTS.items():
            self.assertEqual((ROOT / "home-assistant" / v["file"]).read_text(), self.ha.variant_text(self.base, top),
                             f"{v['file']}: python3 setup/ha_setup.py variants")
            for f in sorted((ROOT / "home-assistant").glob("blueprint_*.yaml")):
                self.assertEqual((ROOT / "home-assistant" / v["blueprints"] / f.name).read_text(),
                                 self.ha.variant_text(f.read_text(), top), f.name)


class Installer(unittest.TestCase):
    def test_the_broker_rule_and_the_clearing_follow_the_root(self):
        text = (ROOT / "install.sh").read_text()
        self.assertIn('echo "topic readwrite $TOPICS/#"', text)
        self.assertIn("ensure_acl_root", text)
        self.assertNotIn("'piano/#'", text)
        self.assertIn('HA_UID="armonico_pi_"', text)


if __name__ == "__main__":
    unittest.main()


class ClearExternalBroker(unittest.TestCase):
    """From the external broker to the local one (cli.clear_broker): everything of the device goes from the external
    broker, its retained topics and its sensors in Home Assistant, and nothing of a big piano on the same broker."""

    def test_only_the_device_is_cleared(self):
        import socket
        import time
        sys.path.insert(0, str(ROOT / "android" / "app" / "src" / "main" / "python"))
        from armonico_android.broker import Broker
        import paho.mqtt.client as mqtt
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        b = Broker(port=port, log=lambda *a: None)
        b.start()
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="t-pub")
            c.connect("127.0.0.1", port)
            c.loop_start()
            for t in ("pianoDevice/status", "pianoDevice/device/octave", "piano/status",
                      "homeassistant/sensor/armonico_pi_status/config", "homeassistant/sensor/piano_status/config"):
                c.publish(t, "x", qos=1, retain=True).wait_for_publish(5)
            c.loop_stop()
            c.disconnect()
            cli = load("armonico_cli_clear", ROOT / "cli" / "armonico.py")
            n = cli.clear_broker({"MQTT_HOST": "127.0.0.1", "MQTT_PORT": str(port), "DEVICE": "yes"}, wait=1.0)
            self.assertEqual(n, 3)
            time.sleep(0.3)
            self.assertEqual(sorted(b.retained), ["homeassistant/sensor/piano_status/config", "piano/status"])
        finally:
            b.stop()


class OldDeviceTopics(unittest.TestCase):
    def test_a_branch_clears_only_what_is_under_it(self):
        """The update's one-time clearing of piano/device/ (install.sh clear_old_device_topics): the big piano's
        piano/ and every sensor stay."""
        import socket
        import time
        sys.path.insert(0, str(ROOT / "android" / "app" / "src" / "main" / "python"))
        from armonico_android.broker import Broker
        import paho.mqtt.client as mqtt
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        b = Broker(port=port, log=lambda *a: None)
        b.start()
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="t-pub2")
            c.connect("127.0.0.1", port)
            c.loop_start()
            for t in ("piano/device/octave", "piano/device/keyboards", "piano/status", "piano/devices",
                      "homeassistant/sensor/piano_status/config"):
                c.publish(t, "x", qos=1, retain=True).wait_for_publish(5)
            c.loop_stop()
            c.disconnect()
            cli = load("armonico_cli_branch", ROOT / "cli" / "armonico.py")
            self.assertEqual(cli.clear_broker({"MQTT_HOST": "127.0.0.1", "MQTT_PORT": str(port)}, wait=1.0,
                                              branch="piano/device"), 2)
            time.sleep(0.3)
            self.assertEqual(sorted(b.retained), ["homeassistant/sensor/piano_status/config", "piano/devices", "piano/status"])
        finally:
            b.stop()

    def test_the_installer_clears_through_the_command(self):
        text = (ROOT / "install.sh").read_text()
        self.assertIn('cli/armonico.py" clear-broker "$f" piano/device', text)
        self.assertIn('cli/armonico.py" clear-broker "$cfg"', text)
        self.assertNotIn('client_id="piano-purge"', text, "one clearing, in the CLI")
