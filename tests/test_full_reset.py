"""The device's full reset (device/full_reset.sh), run for real against stand-ins for the system's
commands and an installer that only writes down how it was called: without a network nothing is
deleted and the device's screen comes back; with one the packages are downloaded first, the
installer's --purge deletes, the install runs and is tried again when it stops, and nothing of
the reset is left at the end. Also the reset's screen in its new states, and the packages the
screen's broker install records for --purge."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "device" / "full_reset.sh"

STUBS = {
    # every call goes to calls.log; what fails is set by files in the stub folder
    "systemctl": 'echo "systemctl $*" >> "$T/calls.log"',
    "journalctl": 'echo "journalctl $*" >> "$T/calls.log"',
    "sleep": '[ "$1" = infinity ] && { echo "stopped" >> "$T/calls.log"; kill -TERM "$PPID"; }; exit 0',
    "apt-get": textwrap.dedent('''\
        echo "apt-get $*" >> "$T/calls.log"
        case " $* " in *" update "*) [ -e "$T/no-net" ] && exit 100 ;; esac
        exit 0'''),
    "curl": textwrap.dedent('''\
        echo "curl $*" >> "$T/calls.log"
        case "$*" in *pypi.org*) [ -e "$T/no-net" ] && exit 6; exit 0 ;; esac
        case "$*" in *releases/latest*) [ -e "$T/tag" ] && { printf '{"tag_name": "%s"}' "$(cat "$T/tag")"; exit 0; } ;; esac
        case "$*" in *releases/download*)          # the release and its SHA256SUMS, when the test made them
            [ -e "$T/release.zip" ] || exit 22
            out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
            case "$*" in *SHA256SUMS*) cp "$T/sha256" "$out" ;; *) cp "$T/release.zip" "$out" ;; esac
            exit 0 ;; esac
        exit 22                                  # GitHub does not answer: the device's own copy'''),
}

INSTALLER = textwrap.dedent('''\
    #!/bin/bash
    echo "install.sh $* reset=${ARMONICO_FULL_RESET:-}" >> "$T/calls.log"
    echo "files $(ls "$(dirname "$0")" | tr '\\n' ' ')" >> "$T/calls.log"
    if [ "$1" = "--device" ] && [ -e "$T/fail-installs" ]; then
        n=$(cat "$T/fail-installs"); [ "$n" -gt 0 ] && { echo $((n - 1)) > "$T/fail-installs"; exit 1; }
    fi
    exit 0
''')


class FullReset(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, ignore_errors=True)
        bin_ = self.t / "bin"
        bin_.mkdir()
        for name, body in STUBS.items():
            f = bin_ / name
            f.write_text("#!/bin/bash\n" + body + "\n")
            f.chmod(0o755)
        self.opt, self.data, self.etc = self.t / "opt", self.t / "data", self.t / "etc"
        (self.opt / "device").mkdir(parents=True)
        (self.opt / "install.sh").write_text(INSTALLER)
        (self.opt / "VERSION").write_text("1.1\n")
        (self.opt / "CHANGELOG.md").write_text("# Changelog\n")
        (self.data / "device").mkdir(parents=True)
        (self.data / "device" / "settings.json").write_text('{"lang": "he"}')
        (self.data / "backups").mkdir()
        (self.data / "backups" / "old.zip").write_text("x")
        self.etc.mkdir()
        (self.etc / "installed-packages").write_text("ffmpeg\nmosquitto\nbad name;rm\n")
        self.cache = self.t / "pipcache"
        self.cache.mkdir()
        self.self_copy = self.t / "self.sh"
        shutil.copy(SCRIPT, self.self_copy)
        self.env = dict(os.environ, T=str(self.t), PATH=f"{bin_}:{os.environ['PATH']}",
                        ARMONICO_RESET_OPT=str(self.opt), ARMONICO_RESET_DATA=str(self.data),
                        ARMONICO_RESET_ETC=str(self.etc), ARMONICO_RESET_WORK=str(self.t / "work"),
                        ARMONICO_RESET_SELF=str(self.self_copy), ARMONICO_RESET_LOCK=str(self.t / "lock"),
                        ARMONICO_RESET_PIP_CACHE=str(self.cache))

    def run_reset(self):
        subprocess.run(["bash", str(self.self_copy)], env=self.env, timeout=60, capture_output=True)
        log = self.t / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def status(self):
        return json.loads((self.t / "work" / "ui" / "status.json").read_text())

    def test_no_network_deletes_nothing_and_gives_the_screen_back(self):
        (self.t / "no-net").touch()
        calls = self.run_reset()
        self.assertTrue((self.data / "backups" / "old.zip").exists())
        self.assertTrue((self.etc / "installed-packages").exists())
        self.assertTrue(self.cache.exists())
        self.assertFalse([c for c in calls if c.startswith("install.sh")])
        self.assertIn("systemctl start armonico-screen", calls)
        self.assertNotIn("systemctl reboot", calls)
        st = self.status()
        self.assertEqual((st["state"], st["step"], st["lang"]), ("nonet", 1, "he"))

    def test_purges_everything_then_installs_and_leaves_nothing(self):
        calls = self.run_reset()
        dl = [c for c in calls if "--download-only" in c]
        self.assertEqual(len(dl), 1)
        self.assertIn("ffmpeg mosquitto", dl[0])
        self.assertNotIn("bad", dl[0])                              # only package names from the list
        inst = [c for c in calls if c.startswith("install.sh")]
        self.assertEqual(inst, ["install.sh --purge --yes reset=yes", "install.sh --device reset="])
        # the packages are downloaded before the purge
        self.assertLess(calls.index(dl[0]), calls.index(inst[0]))
        self.assertIn("journalctl --vacuum-time=1s", calls)
        self.assertEqual(calls[-1], "systemctl reboot")
        for gone in (self.data, self.etc, self.cache, self.t / "work", self.self_copy):
            self.assertFalse(gone.exists(), gone)

    def not_taken(self, tag):                                       # the device has 1.1
        (self.t / "tag").write_text(tag)
        calls = self.run_reset()
        self.assertFalse([c for c in calls if "releases/download" in c], tag)
        self.assertEqual(calls[-1], "systemctl reboot")

    def test_older_github_release_is_not_taken(self):
        self.not_taken("v1.0")

    def test_same_github_release_is_not_taken(self):
        self.not_taken("v1.1")

    def test_newer_github_release_is_fetched(self):
        (self.t / "tag").write_text("v1.2")
        calls = self.run_reset()
        self.assertTrue([c for c in calls if "releases/download/v1.2/armonico-v1.2.zip" in c])

    def test_the_install_from_the_devices_own_copy_has_every_file_the_installer_needs_beside_it(self):
        """The installer stops with "The repository is incomplete" when a file of its REQUIRED_FILES is missing next
        to it. The copy the reset installs from had install.sh and VERSION only, and CHANGELOG.md is in that list:
        the install stopped 3 times and the reset with it, after everything was deleted."""
        calls = self.run_reset()
        listed = [ln for ln in calls if ln.startswith("files ")]
        self.assertTrue(listed)
        for ln in listed:
            for name in ("install.sh", "VERSION", "CHANGELOG.md"):
                self.assertIn(name, ln.split(), ln)

    def test_every_root_file_the_installer_requires_is_copied_by_the_reset(self):
        import re
        text = (ROOT / "install.sh").read_text()
        block = re.search(r"REQUIRED_FILES=\((.*?)\n\)", text, re.S).group(1)
        roots = [f for f in block.split() if "/" not in f and not f.startswith("#")]
        reset = SCRIPT.read_text()
        for f in roots:
            self.assertIn(f, reset, f"{f} is required beside install.sh and the reset does not copy it")

    def test_versions_compare_as_the_updates_do(self):
        """sort -V on the raw text had v1.0.0 and 1.0.0.0 newer than 1.0: the same version taken again."""
        script = ("source <(sed -n '/^vkey() {/,/^}/p;/^newer() {/,/^}/p' \"$1\"); shift; "
                  'for p in "$@"; do set -- $p; newer "$1" "$2" && echo yes || echo no; done')
        cases = {"v1.0 1.0": "no", "v1.0.0 1.0": "no", "1.0.0.0 1.0": "no", "v1.0.0.0 v1.0": "no",
                 "v0.9.9 1.0": "no", "v1.0.1 1.0": "yes", "v1.10 1.9": "yes", "v1.1 1.1-rc1": "yes",
                 "v1.1-rc1 1.1": "no", "v1.1 unknown": "no"}
        out = subprocess.run(["bash", "-c", script, "x", str(SCRIPT), *cases], capture_output=True, text=True,
                             timeout=20).stdout.split()
        self.assertEqual(dict(zip(cases, out)), cases)

    def test_same_version_with_more_zeros_is_not_taken(self):
        self.not_taken("v1.1.0")

    def test_same_version_with_four_parts_is_not_taken(self):
        self.not_taken("v1.1.0.0")

    def make_release(self, tag, device):
        import zipfile
        top = f"armonico-{tag}/"
        required = "REQUIRED_FILES=(\n    VERSION\n" + ("    device/armonico_pi.py\n    device/full_reset.sh\n"
                                                         "    device/root_actions.py\n    device/diag.py\n" if device else "") + ")\n"
        rel = self.t / "release.zip"
        with zipfile.ZipFile(rel, "w") as z:
            z.writestr(top + "install.sh", INSTALLER + "\n" + required)
            z.writestr(top + "VERSION", tag.lstrip("v") + "\n")
            z.writestr(top + "FROM_GITHUB", "")
            if device:
                for f in ("armonico_pi.py", "full_reset.sh", "root_actions.py", "diag.py"):
                    z.writestr(top + "device/" + f, "#")
        import hashlib
        (self.t / "sha256").write_text(f"{hashlib.sha256(rel.read_bytes()).hexdigest()}  armonico-{tag}.zip\n")
        (self.t / "tag").write_text(tag)

    def installed_from_github(self, calls):
        listed = [ln for ln in calls if ln.startswith("files ")]
        self.assertTrue(listed)
        return all("FROM_GITHUB" in ln.split() for ln in listed)

    def test_newer_release_without_the_device_files_is_not_installed(self):
        """The published v1.0 has no device/ folder: reinstalling from it left a device without its program."""
        self.make_release("v1.2", device=False)
        calls = self.run_reset()
        self.assertTrue([c for c in calls if "releases/download/v1.2" in c])
        self.assertFalse(self.installed_from_github(calls))
        self.assertEqual(calls[-1], "systemctl reboot")

    def test_newer_release_with_the_device_files_is_installed(self):
        self.make_release("v1.2", device=True)
        calls = self.run_reset()
        self.assertTrue(self.installed_from_github(calls))
        self.assertEqual(calls[-1], "systemctl reboot")

    def test_install_that_stops_is_tried_again(self):
        (self.t / "fail-installs").write_text("2")
        calls = self.run_reset()
        self.assertEqual(sum(c.startswith("install.sh --device") for c in calls), 3)
        self.assertEqual(calls[-1], "systemctl reboot")

    def test_install_that_keeps_stopping_stops_the_reset_with_its_log(self):
        (self.t / "fail-installs").write_text("9")
        calls = self.run_reset()
        self.assertEqual(sum(c.startswith("install.sh --device") for c in calls), 3)
        self.assertIn("stopped", calls)
        self.assertNotIn("systemctl reboot", calls)
        self.assertEqual(self.status()["state"], "failed")
        self.assertTrue((self.t / "work" / "src" / "install.sh").exists())   # for the install by hand


try:
    import pygame                                   # noqa: F401
except ImportError:
    pygame = None


@unittest.skipIf(pygame is None, "pygame is not installed")
class ResetScreen(unittest.TestCase):
    def test_every_state_draws(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        for lang in ("he", "en"):
            for step, state in ((1, "nonet"), (0, "cancelled"), (2, "running"), (3, "failed"), (4, "done")):
                (t / "status.json").write_text(json.dumps({"lang": lang, "step": step, "state": state,
                                                           "line": "x", "source": "Armonico 1.1"}))
                out = t / f"{lang}-{state}.png"
                r = subprocess.run([sys.executable, str(ROOT / "device" / "reset_screen.py"), str(t), "--png", str(out)],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertGreater(out.stat().st_size, 1000)


class BrokerPackages(unittest.TestCase):
    def test_mosquitto_from_the_screen_goes_on_the_purge_list(self):
        t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, t, ignore_errors=True)
        lst = t / "installed-packages"
        lst.write_text("ffmpeg\n")
        sys.path.insert(0, str(ROOT / "device"))
        import root_actions as ra
        with mock.patch.object(ra, "PKG_LIST", lst), \
             mock.patch.object(ra, "installed_packages", return_value={"ffmpeg", "mosquitto", "libmosquitto1"}):
            ra.record_packages({"ffmpeg"})
        self.assertEqual(lst.read_text(), "ffmpeg\nlibmosquitto1\nmosquitto\n")
        self.assertEqual(oct(lst.stat().st_mode & 0o777), "0o600")



class HandOver(unittest.TestCase):
    def test_script_copy_is_outside_the_private_tmp(self):
        """The root unit runs with PrivateTmp=yes: a copy in its /tmp or /var/tmp is invisible to the
        reset's own unit, which then ended at once and the reset did nothing."""
        sys.path.insert(0, str(ROOT / "device"))
        import root_actions as ra
        unit = (ROOT / "system" / "device-request.service").read_text()
        if "PrivateTmp=yes" in unit:
            self.assertFalse(ra.FULL_RESET_COPY.startswith(("/tmp/", "/var/tmp/")), ra.FULL_RESET_COPY)
        self.assertIn(f'SELF="${{ARMONICO_RESET_SELF:-{ra.FULL_RESET_COPY.replace("armonico", "$APP")}}}"',
                      SCRIPT.read_text())


class Shipped(unittest.TestCase):
    def test_text_defaults_is_json(self):
        """The wording a release carries: a file that does not parse was skipped without a word."""
        d = json.loads((ROOT / "lessons" / "text_defaults.json").read_text())
        self.assertEqual(set(d), {"en", "he"})


if __name__ == "__main__":
    unittest.main()
