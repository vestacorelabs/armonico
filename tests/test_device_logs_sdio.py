"""device/logs.py: the Wi-Fi's own MMC bus (a Pi 3's mmc1) is not read as the SD card's errors."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device"))
import logs  # noqa: E402

PI3 = """[    3.4] mmc1: error -110 whilst initialising SDIO card
[   11.7] brcmfmac_sdio: probe of mmc1:0001:1 failed with error -110
[   40.0] mmc1: Timeout waiting for hardware interrupt.
[   20.0] mmc0: timeout waiting for hardware interrupt.
[   30.0] I/O error, dev mmcblk0, sector 1234"""


class Sdio(unittest.TestCase):
    def test_the_wifi_bus_is_left_out(self):
        out = logs.io_lines(PI3, hosts={0})
        self.assertEqual(len(out), 2)
        self.assertTrue(all("mmc1" not in x for x in out))

    def test_unknown_buses_keep_every_line_but_sdio(self):
        out = logs.io_lines(PI3, hosts=set()) + logs.io_lines(PI3, hosts={0, 1})
        self.assertIn("[   40.0] mmc1: Timeout waiting for hardware interrupt.", logs.io_lines(PI3, hosts={0, 1}))
        self.assertTrue(all("SDIO" not in x and "brcmf" not in x for x in out))

    def test_disk_hosts_from_sysfs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "mmc0" / "mmc0:59b4" / "block").mkdir(parents=True)
            (Path(d) / "mmc1" / "mmc1:0001").mkdir(parents=True)
            self.assertEqual(logs.disk_hosts(d), {0})
        self.assertIsNone(logs.disk_hosts("/nonexistent/mmc_host"))


if __name__ == "__main__":
    unittest.main()
