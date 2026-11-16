"""The device does not wait for the network at boot: the bridge and the lessons start after network.target there,
after network-online.target on a general install (system/*.service, install.sh install_units)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class BootWaitTests(unittest.TestCase):
    def test_bridge_and_lessons_take_the_wait_from_the_installer(self):
        for name in ("bridge.service", "lessons.service"):
            text = (ROOT / "system" / name).read_text()
            self.assertNotIn("network-online", text, name)
            self.assertRegex(text, r"(?m)^Wants=@NET_ONLINE@ ", name)
            self.assertRegex(text, r"(?m)^After=@NET_ONLINE@ ", name)

    def test_the_installer_fills_it_by_the_kind_of_install(self):
        sh = (ROOT / "install.sh").read_text()
        body = sh[sh.index("install_units() {"):sh.index("stop_services() {")]
        self.assertIn('local net_online="network-online.target"', body)
        self.assertIn('[ "$DEVICE" = "yes" ] && net_online="network.target"', body)
        self.assertIn('-e "s|@NET_ONLINE@|$net_online|g"', body)
        self.assertLess(body.index("net_online="), body.index("@NET_ONLINE@"))

    def test_no_unit_keeps_a_placeholder_the_installer_does_not_fill(self):
        sh = (ROOT / "install.sh").read_text()
        body = sh[sh.index("install_units() {"):sh.index("stop_services() {")]
        filled = set(re.findall(r"s\|(@[A-Z_]+@)\|", body)) | {"@VIDEO_GROUP@"}
        for unit in (ROOT / "system").glob("*.service"):
            for ph in set(re.findall(r"@[A-Z_]+@", unit.read_text())):
                self.assertIn(ph, filled, (unit.name, ph))

    def test_cloud_init_is_switched_off_on_the_pi_and_back_on_uninstall(self):
        sh = (ROOT / "install.sh").read_text()
        on = sh.index("change_add CLOUD_INIT_DISABLED yes once")
        self.assertLess(sh.rindex('if [ "$DEVICE_KIND" = pi ]; then', 0, on), on)
        self.assertIn(": > /etc/cloud/cloud-init.disabled", sh[on:on + 200])
        self.assertIn('[ ! -e /etc/cloud/cloud-init.disabled ]', sh[on - 300:on])     # one made by someone else stays theirs
        back = sh[sh.index("revert_system_changes() {"):]
        self.assertIn('[ "$(change_get CLOUD_INIT_DISABLED | tail -1)" = "yes" ] && rm -f /etc/cloud/cloud-init.disabled', back)


if __name__ == "__main__":
    unittest.main()
