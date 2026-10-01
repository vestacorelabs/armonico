"""The installer's fixes for 1.1, run as the functions themselves: each is taken out of install.sh
and run in bash with stubs for the commands that would change the machine. Nothing here runs the
installer as a whole, and nothing touches the system outside a temporary folder."""
import contextlib
import http.client
import io
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
sys.path.insert(0, str(ROOT / "setup"))

PRELUDE = r"""
set -Eeuo pipefail
die()  { printf 'DIE %s\n' "$*" >&2; exit 1; }
ok()   { printf 'ok %s\n' "$*"; }
warn() { printf 'warn %s\n' "$*"; }
step() { printf '== %s\n' "$*"; }
wrap() { printf '%s\n' "$2"; }
busy() { :; }
trap 'die "line $LINENO: $BASH_COMMAND"' ERR
APP=armonico; APP_TITLE=Armonico
"""


def needs_root(test):
    """The installer's own functions give files to root (install -o root, chown root:...), which only root can do:
    run the tests with sudo (sudo -E python3 -m unittest discover -s tests), as CI does."""
    if os.geteuid() != 0:
        test.fail("This test runs the installer's functions, which give files to root: run the tests with sudo")


def functions(*names):
    """The bash source of the named functions, as they are in install.sh."""
    src = INSTALL.read_text()
    out = []
    for n in names:
        m = re.search(rf"^{re.escape(n)}\(\) \{{.*$", src, re.M)
        assert m, n
        if "; }" in m.group(0):                          # a one-line function
            out.append(m.group(0))
            continue
        end = src.index("\n}\n", m.start())
        out.append(src[m.start():end + 3])
    return "\n".join(out)


class Bash(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def stub(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)

    def run_bash(self, script, env=None, **kw):
        e = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", T=str(self.tmp))
        e.update(env or {})
        return subprocess.run(["bash", "-c", PRELUDE + script], env=e, capture_output=True, text=True,
                              timeout=60, **kw)


class LanIp(Bash):
    def test_no_route_and_no_address_does_not_stop_the_installer(self):
        self.stub("ip", "exit 2")                        # "ip route get" with no default route
        self.stub("hostname", 'echo "127.0.1.1 "')
        r = self.run_bash(functions("lan_ip") + '\nip="$(lan_ip)"; echo "reached [$ip]"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reached []", r.stdout)

    def test_the_address_is_still_found(self):
        self.stub("ip", "echo '1.1.1.1 via 192.0.2.1 dev eth0 src 192.0.2.7 uid 0'")
        r = self.run_bash(functions("lan_ip") + '\necho "[$(lan_ip)]"')
        self.assertIn("[192.0.2.7]", r.stdout)


class Links(Bash):
    """Root never sets the owner or mode of, or writes, what a link of the service user's points at."""

    def test_a_linked_folder_is_replaced_and_its_target_left_alone(self):
        target = self.tmp / "target"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        var = self.tmp / "var"
        var.mkdir()
        (var / "songs").symlink_to(target)
        if os.geteuid() == 0:            # as on a real machine: the data folder is the service user's
            os.chown(var, 65534, 65534)
            os.lchown(var / "songs", 65534, 65534)
        r = self.run_bash(functions("user_dirs") + '\nSERVICE_USER="$(id -un)"; user_dirs "$T/var/songs"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((var / "songs").is_symlink())
        self.assertTrue((var / "songs").is_dir())
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertIn("was a link", r.stdout)

    @unittest.skipUnless(os.geteuid() == 0, "needs root to make a root-owned link")
    def test_a_link_the_admin_made_in_a_root_folder_is_kept(self):
        """/var/lib/armonico linked to another disk: only root can make that name, so the link is the
        admin's own and the data behind it stays in use."""
        target = self.tmp / "ssd"
        target.mkdir()
        (target / "progress.json").write_text("{}")
        link = self.tmp / "armonico"
        link.symlink_to(target)
        r = self.run_bash(functions("user_dirs") + '\nSERVICE_USER="$(id -un)"; user_dirs "$T/armonico"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(link.is_symlink())
        self.assertTrue((link / "progress.json").exists())
        self.assertNotIn("was a link", r.stdout)

    def test_the_screen_code_is_not_written_through_a_link(self):
        victim = self.tmp / "victim"
        victim.write_text("keep\n")
        var = self.tmp / "var"
        var.mkdir()
        (var / "screen_code").symlink_to(victim)
        script = functions("lan_ip", "summary") + r"""
SERVICE_USER="$(id -un)"; VAR_DIR="$T/var"; SONGS_DIR="$T/var/songs"; CONFIG="$T/config.env"
LESSON_UI_PORT=8099; C_SCREEN_CODE=on; C_MQTT_LOCAL=no; C_MQTT_HOST=h; C_MQTT_PORT=1883; NEW_GROUPS=no
summary
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(victim.read_text(), "keep\n")
        code = var / "screen_code"
        self.assertFalse(code.is_symlink())
        self.assertRegex(code.read_text(), r"^\d{6}\n$")
        self.assertEqual(code.stat().st_mode & 0o777, 0o600)

    def test_the_setup_screens_defaults_are_written_before_the_folder_is_handed_over(self):
        body = functions("setup_web")
        self.assertLess(body.index('> "$defaults"'), body.index('chown "$SERVICE_USER:$SERVICE_USER" "$work"'))
        self.assertIn('[ -L "$result" ]', body)


class LocalBroker(Bash):
    def test_a_first_install_that_stopped_takes_its_own_broker_again(self):
        (self.tmp / "armonico.conf").write_text("listener 1883 127.0.0.1\n")
        script = functions("choose_broker", "resume_local_broker") + r"""
CONFIG="$T/none/config.env"; LOCAL_BROKER_CONF="$T/armonico.conf"; HA_FOUND=no; HA_BROKER=no
port_in_use() { return 0; }                       # our Mosquitto holds 1883
install_local_broker() { echo "INSTALL_LOCAL_BROKER"; C_MQTT_LOCAL=yes; }
choose_broker </dev/null; echo "local=$C_MQTT_LOCAL"
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("INSTALL_LOCAL_BROKER", r.stdout)
        self.assertIn("local=yes", r.stdout)
        self.assertNotIn("already listens", r.stdout)

    def test_another_broker_on_the_port_is_still_left_alone(self):
        script = functions("choose_broker", "resume_local_broker") + r"""
CONFIG="$T/none/config.env"; LOCAL_BROKER_CONF="$T/none/armonico.conf"; HA_FOUND=no; HA_BROKER=no
install_local_broker() { echo "INSTALL_LOCAL_BROKER"; }
choose_broker </dev/null
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("INSTALL_LOCAL_BROKER", r.stdout)

    def test_reconfigure_after_such_an_install_takes_it_too(self):
        body = functions("configure")
        self.assertIn('[ "$ACTION" = "reconfigure" ] && [ ! -f "$CONFIG" ] && [ -f "$LOCAL_BROKER_CONF" ]', body)


class Reconfigure(Bash):
    def test_a_setup_that_stopped_starts_the_services_again(self):
        self.stub("systemctl", 'echo "systemctl $*" >> "$T/calls"')
        script = functions("cleanup") + r"""
MUSIC_PID=""; record_packages() { :; }
trap cleanup EXIT
RESTORE_SERVICES=(armonico-bridge armonico-lessons)
die "The setup screen timed out"
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 1)
        self.assertIn("systemctl start armonico-bridge armonico-lessons", (self.tmp / "calls").read_text())

    def test_a_setup_that_finished_restarts_nothing_from_the_exit_trap(self):
        self.stub("systemctl", 'echo "systemctl $*" >> "$T/calls"')
        script = functions("cleanup") + r"""
MUSIC_PID=""; record_packages() { :; }
trap cleanup EXIT
RESTORE_SERVICES=()
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / "calls").exists())

    def test_the_list_is_emptied_once_the_setup_is_done(self):
        src = INSTALL.read_text()
        block = src[src.index('if [ "$ACTION" = "reconfigure" ]; then\n    [ -x'):]
        block = block[:block.index("exit 0")]
        self.assertLess(block.index("stop_services"), block.index("\n    configure\n"))
        self.assertLess(block.index("\n    configure\n"), block.index("RESTORE_SERVICES=()"))
        self.assertLess(block.index("RESTORE_SERVICES=()"), block.index("start_services"))


class Settings(Bash):
    """--reconfigure reads config.env as text and keeps what was added or moved by hand."""

    CONFIG = """# settings
MQTT_HOST='192.0.2.5'
MQTT_PORT="1883"
MQTT_USER=piano
MQTT_PASS='p$w"d'
MIDI_DEVICE='Digital Piano'
MQTT_LOCAL='no'
DATA_DIR='/srv/piano'
SONGS_DIR='/srv/piano/songs'
LESSON_UI_PORT='8109'
AMIDI_DEVICE="hw:1,0"
MQTT_RECORD_DAYS=30
this line is not bash at all
EVIL=$(touch "$T/ran")
"""

    def test_parsed_not_run_and_hand_keys_kept(self):
        needs_root(self)
        etc = self.tmp / "etc"
        etc.mkdir()
        (etc / "config.env").write_text(self.CONFIG)
        script = functions("cfg_line", "cfg_parse", "load_existing", "write_config") + r"""
ETC_DIR="$T/etc"; CONFIG="$ETC_DIR/config.env"; VAR_DIR=/var/lib/armonico; SONGS_DIR="$VAR_DIR/songs"
SERVICES=(a b); SERVICE_USER="$(id -un)"; LESSON_UI_PORT=8099
load_existing
echo "host=$C_MQTT_HOST port=$C_MQTT_PORT user=$C_MQTT_USER pass=$C_MQTT_PASS dev=$C_MIDI_DEVICE ui=$LESSON_UI_PORT songs=$SONGS_DIR"
C_KEY_LOWEST=21; C_KEY_HIGHEST=108; C_STOP_KEY=21
write_config
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / "ran").exists())
        self.assertIn("""host=192.0.2.5 port=1883 user=piano pass=p$w"d dev=Digital Piano ui=8109""", r.stdout)
        self.assertIn("songs=/var/lib/armonico/songs", r.stdout)       # the installer's own folder is untouched
        out = (etc / "config.env").read_text()
        self.assertIn("DATA_DIR='/srv/piano'\n", out)
        self.assertIn("SONGS_DIR='/srv/piano/songs'\n", out)
        self.assertIn('AMIDI_DEVICE="hw:1,0"\n', out)
        self.assertIn("MQTT_RECORD_DAYS=30\n", out)
        self.assertIn("MQTT_HOST='192.0.2.5'\n", out)
        self.assertEqual(out.count("MQTT_PORT="), 1)

    def test_a_new_install_writes_the_default_folders(self):
        needs_root(self)
        etc = self.tmp / "etc"
        etc.mkdir()
        script = functions("cfg_line", "cfg_parse", "load_existing", "write_config") + r"""
ETC_DIR="$T/etc"; CONFIG="$ETC_DIR/config.env"; VAR_DIR=/var/lib/armonico; SONGS_DIR="$VAR_DIR/songs"
SERVICES=(a b); SERVICE_USER="$(id -un)"; LESSON_UI_PORT=8099
load_existing
C_MQTT_HOST=h; C_MQTT_PORT=1883; C_MQTT_USER=""; C_MQTT_PASS=""; C_MQTT_LOCAL=no; C_MIDI_DEVICE=k
C_KEY_LOWEST=21; C_KEY_HIGHEST=108; C_STOP_KEY=21
write_config
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = (etc / "config.env").read_text()
        self.assertIn("DATA_DIR='/var/lib/armonico'\n", out)
        self.assertIn("SONGS_DIR='/var/lib/armonico/songs'\n", out)


class Purge(Bash):
    def _plan(self, removal_takes_more):
        (self.tmp / "installed-packages").write_text("mosquitto\nlibcjson1\n")
        extra = '[ "$p" = mosquitto ] && echo "Purg zigbee2mqtt [2.0]"' if removal_takes_more else ":"
        self.stub("apt-get", 'shift; shift\nfor p in "$@"; do echo "Purg $p [1.0]"; ' + extra + '; done')
        self.stub("dpkg-query", 'case "$*" in *Essential*) echo no;; *) printf "ii ";; esac')
        script = functions("plan_packages", "_apt_removals", "_names", "outside_removals", "outside_removals_of") + r"""
PKG_LIST="$T/installed-packages"; PKG_KEEP=(curl ca-certificates)
plan_packages; echo "GOES=$BROKER_GOES NEEDED=$BROKER_NEEDED REMOVE=(${PKG_REMOVE[*]})"
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_a_mosquitto_another_package_needs_stays_whole(self):
        out = self._plan(True)
        self.assertIn("GOES=no NEEDED=yes", out)
        self.assertNotIn("mosquitto", out.split("REMOVE=")[1])

    def test_a_mosquitto_nothing_needs_goes(self):
        self.assertIn("GOES=yes NEEDED=no REMOVE=(mosquitto libcjson1)", self._plan(False))

    def test_the_confirmation_says_mosquitto_stays(self):
        self.assertIn("another package needs it", functions("confirm_purge"))


class SystemChanges(Bash):
    """--uninstall and --purge undo what the installer recorded as changed by it, and nothing else."""

    def test_recorded_once_and_reverted(self):
        needs_root(self)
        etc = self.tmp / "etc"
        cache = self.tmp / "cache" / "pip"
        self.stub("id", 'if [ "$1" = -nG ]; then echo "alice sudo adm armonico"; else /usr/bin/id "$@"; fi')
        self.stub("gpasswd", 'echo "gpasswd $*" >> "$T/calls"')
        self.stub("systemctl", 'echo "systemctl $*" >> "$T/calls"')
        script = functions("no_quote", "change_get", "change_add", "revert_system_changes") + r"""
ETC_DIR="$T/etc"; CHANGES="$ETC_DIR/system-changes"; SERVICE_USER="$(id -un)"
change_add GROUP_ADDED alice:adm
change_add GROUP_ADDED alice:systemd-journal
change_add GROUP_ADDED alice:adm
change_add GREETD_WAS enabled once
change_add GREETD_WAS disabled once
change_add GLOBAL_ENABLED wireplumber.service
change_add PIP_CACHE "$T/cache/pip"
mkdir -p "$T/cache/pip/wheels"
cat "$CHANGES"
revert_system_changes
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("GROUP_ADDED='alice:adm'"), 1)
        self.assertIn("GREETD_WAS='enabled'", r.stdout)
        self.assertNotIn("GREETD_WAS='disabled'", r.stdout)
        self.assertEqual((etc / "system-changes").stat().st_mode & 0o777, 0o600)
        calls = (self.tmp / "calls").read_text()
        self.assertIn("gpasswd -d alice adm", calls)
        self.assertNotIn("systemd-journal", calls)                       # no longer a member: nothing to undo
        self.assertIn("systemctl --global disable wireplumber.service", calls)
        self.assertIn("systemctl enable greetd.service", calls)
        self.assertFalse(cache.exists())

    def test_fsck_repair_the_device_added_is_taken_off_again(self):
        needs_root(self)
        cmd = self.tmp / "cmdline.txt"
        cmd.write_text("console=tty1 root=PARTUUID=ab-02 rootwait fsck.repair=yes\n")
        (self.tmp / "cmdline.txt.armonico-bak").write_text("console=tty1 root=PARTUUID=ab-02 rootwait\n")
        script = functions("no_quote", "change_get", "change_add", "revert_system_changes") + r"""
ETC_DIR="$T/etc"; CHANGES="$ETC_DIR/system-changes"; SERVICE_USER="$(id -un)"
change_add FSCK_REPAIR_ADDED "$T/cmdline.txt" once
revert_system_changes
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(cmd.read_text(), "console=tty1 root=PARTUUID=ab-02 rootwait\n")
        self.assertFalse((self.tmp / "cmdline.txt.armonico-bak").exists())

    def test_nothing_recorded_nothing_undone(self):
        self.stub("gpasswd", 'echo "gpasswd $*" >> "$T/calls"')
        self.stub("systemctl", 'echo "systemctl $*" >> "$T/calls"')
        script = functions("no_quote", "change_get", "revert_system_changes") + r"""
CHANGES="$T/none/system-changes"; revert_system_changes; echo done
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / "calls").exists())

    def test_every_change_is_recorded_before_it_is_made(self):
        src = INSTALL.read_text()
        self.assertIn('change_add GROUP_ADDED "$SUDO_USER:$g"', functions("create_user"))
        dev = functions("device_setup")
        self.assertLess(dev.index("change_add GREETD_WAS"), dev.index('apt-get install -y -qq --no-install-recommends "${DEVICE_APT[@]}"'))
        self.assertLess(dev.index("ASOUND_STATE_MADE"), dev.index("alsactl store"))
        snd = functions("device_sound")
        self.assertLess(snd.index("change_add GLOBAL_ENABLED"), snd.index("systemctl --global enable"))
        venv = functions("build_venv")
        self.assertLess(venv.index("change_add PIP_CACHE"), venv.index('"$VENV/bin/pip" install'))
        un = functions("uninstall")
        self.assertLess(un.index("revert_system_changes"), un.index('rm -rf "${ETC_DIR:?}"'))
        self.assertIn("revert_system_changes() {", src)


class Units(Bash):
    def _install(self, device):
        repo = self.tmp / "repo"
        shutil.rmtree(repo, True)
        shutil.copytree(INSTALL.parent / "system", repo / "system")
        units = self.tmp / "units"
        shutil.rmtree(units, True)
        units.mkdir()
        (self.tmp / "systemctl").write_text("")
        self.stub("systemctl", 'echo "$@" >> "$T/systemctl"')
        self.stub("getent", "exit 2")                            # no video group on this machine
        self.stub("id", "echo 1000")
        r = self.run_bash(functions("install_units") + f'\nREPO_DIR="{repo}"; UNIT_DIR="{units}"; OPT_DIR=/o; ETC_DIR=/e; '
                          f'VAR_DIR=/v; SERVICE_USER=u; SERVICES=(); DEVICE={device}; ok() {{ :; }}\ninstall_units')
        self.assertEqual(r.returncode, 0, r.stderr)
        return units, (self.tmp / "systemctl").read_text()

    def test_boot_mark_is_installed_on_the_device_only(self):
        units, calls = self._install("no")
        self.assertFalse((units / "armonico-boot-mark.service").exists())
        self.assertNotIn("boot-mark", calls)
        units, calls = self._install("yes")
        self.assertTrue((units / "armonico-boot-mark.service").exists())

    def test_the_sampler_timer_is_installed_on_both(self):
        for device in ("no", "yes"):
            units, calls = self._install(device)
            self.assertTrue((units / "armonico-sampler.timer").exists())
            self.assertTrue((units / "armonico-sampler.service").exists())
            self.assertIn("enable --now armonico-sampler.timer", calls)
            self.assertNotIn("SupplementaryGroups", (units / "armonico-sampler.service").read_text().replace("# install.sh drops this line on a machine with no video group", ""))

    def test_device_enables_boot_mark_and_uninstall_removes_both(self):
        self.assertIn('enable --now "$APP-boot-mark.service"', functions("device_finish"))
        un = functions("uninstall")
        self.assertIn('"$APP-boot-mark.service" "$APP-sampler.timer"', un)
        self.assertIn('$UNIT_DIR/$APP-boot-mark.service', un)
        self.assertIn('$UNIT_DIR/$APP-sampler.service" "$UNIT_DIR/$APP-sampler.timer', un)


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("git") and shutil.which("runuser"),
                     "needs root, git and runuser")
class RepoDir(Bash):
    def _repo(self, owner):
        d = self.tmp / "armonico"
        (d / "lessons").mkdir(parents=True)
        for f in ("install.sh", "VERSION", "lessons/piano_game.py"):
            (d / f).write_text("x\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", str(d)], check=True, env=env)
        subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(d), "commit", "-qm", "x"], check=True, env=env)
        (d / "VERSION").write_text("changed\n")
        if owner != "root":
            subprocess.run(["chown", "-R", owner, str(d)], check=True)
            self.tmp.chmod(0o755)
        return d

    def _plan(self, d):
        script = functions("plan_repo_dir") + rf"""
REPO_DIR="{d}"; OPT_DIR=/opt/armonico; ETC_DIR=/etc/armonico
plan_repo_dir; echo "GOES=$REPO_GOES WHY=$REPO_KEEP_WHY"
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_changes_in_another_users_checkout_keep_it(self):
        self.assertIn("GOES=no WHY=it has local changes in git", self._plan(self._repo("nobody")))

    def test_changes_in_roots_checkout_keep_it(self):
        self.assertIn("GOES=no WHY=it has local changes in git", self._plan(self._repo("root")))

    def test_a_git_that_cannot_answer_keeps_it(self):
        d = self._repo("root")
        self.stub("git", "exit 128")
        self.assertIn("GOES=no WHY=it is a git checkout and git could not tell", self._plan(d))


class Integrations(Bash):
    def _refresh(self, fail_text):
        script = functions("ha_off_this_machine", "ha_local_broker_note", "run_refresh", "plex_flag_on", "plex_ha_lines") + rf"""
APP=armonico; VENV=/nonexistent; OPT_DIR=/nonexistent; C_MQTT_LOCAL=no
ha_run() {{ printf 'fail\t%s\n' "{fail_text}"; }}
run_refresh http://192.0.2.9:8123 TOKEN ""
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_a_refused_kept_token_points_to_forget_tokens(self):
        out = self._refresh("Home Assistant refused the token. Create a new one, and copy it whole")
        self.assertIn("sudo armonico forget-tokens, then sudo armonico integrations", out)

    def test_another_failure_points_to_integrations(self):
        out = self._refresh("Home Assistant did not answer")
        self.assertIn("Run: sudo armonico integrations", out)
        self.assertNotIn("forget-tokens", out)

    def test_the_tv_automation_only_with_the_card_on(self):
        script = functions("plex_flag_on", "plex_ha_lines") + r"""
ok() { echo "OK $*"; }; warn() { echo "WARN $*"; }
ha_run() { printf 'ok\tThe TV automation: remote.tv\n'; }
C_DATA_DIR="$T/data"; mkdir -p "$C_DATA_DIR"
plex_ha_lines; echo ---
touch "$C_DATA_DIR/plex-fun"; plex_ha_lines
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("---")[0].strip(), "", "nothing is said while the card is off")
        self.assertIn("OK The TV automation: remote.tv", r.stdout)

    def test_telegram_points_to_forget_tokens(self):
        src = (ROOT / "setup" / "telegram_setup.py").read_text()
        self.assertIn("does not accept the kept token: {e}. \"\n            \"Run: sudo armonico forget-tokens", src)

    def test_the_telegram_chats_are_no_longer_kept(self):
        needs_root(self)
        self.assertNotIn("TELEGRAM_CHATS", (ROOT / "setup" / "telegram_setup.py").read_text())
        (self.tmp / "secrets.env").write_text("HA_TOKEN='a'\nTELEGRAM_CHATS='1,2'\nTELEGRAM_TOKEN='b'\n")
        r = self.run_bash(functions("secret_drop") + '\nSECRETS="$T/secrets.env"; secret_drop TELEGRAM_CHATS; secret_drop NOTHING')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "secrets.env").read_text(), "HA_TOKEN='a'\nTELEGRAM_TOKEN='b'\n")


class HomeAssistantAndTheLocalBroker(Bash):
    def _note(self, url, local="yes"):
        self.stub("hostname", 'if [ "${1:-}" = -I ]; then echo "192.0.2.7 10.0.0.2"; else echo pianobox; fi')
        script = functions("ha_off_this_machine", "ha_local_broker_note") + rf"""
C_MQTT_LOCAL={local}
if ha_local_broker_note "{url}"; then echo SKIPPED; else echo GOES_ON; fi
"""
        r = self.run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_another_machine_is_skipped_with_a_reason(self):
        out = self._note("http://192.0.2.50:8123")
        self.assertIn("SKIPPED", out)
        self.assertIn("127.0.0.1", out)

    def test_this_machine_goes_on(self):
        for url in ("http://127.0.0.1:8123", "http://localhost:8123", "http://192.0.2.7:8123", "http://pianobox.local:8123"):
            self.assertIn("GOES_ON", self._note(url), url)

    def test_an_outside_broker_goes_on(self):
        self.assertIn("GOES_ON", self._note("http://192.0.2.50:8123", local="no"))

    def test_finish_and_refresh_ask_first(self):
        fin = functions("finish_home_assistant")
        self.assertLess(fin.index("ha_local_broker_note"), fin.index("ha_run package"))
        self.assertIn('! ha_local_broker_note "$1"', functions("run_refresh"))


class Music(Bash):
    def _run(self, config_exists):
        etc = self.tmp / "etc"
        etc.mkdir()
        if config_exists:
            (etc / "config.env").write_text("MQTT_HOST='h'\n")
        (self.tmp / "music.py").write_text("import sys\nprint('20:0\\tKeys') if sys.argv[1] == 'detect' else None\n")
        script = functions("start_music") + r"""
CONFIG="$T/etc/config.env"; DEVICE=no; MUSIC_PY="$T/music.py"; MUSIC_PORT=""; MUSIC_PID=""; MUSIC_END=stop
start_music; echo "END port=$MUSIC_PORT"
"""
        master, slave = pty.openpty()
        e = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", T=str(self.tmp))
        p = subprocess.Popen(["bash", "-c", PRELUDE + script], env=e, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        out = b""
        end = time.time() + 15
        while time.time() < end:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if b"[Y/n]" in out:
                os.write(master, b"n\n")
            if b"END" in out:
                break
        p.wait(10)
        os.close(master)
        return out.decode(errors="replace")

    def test_an_update_asks_nothing(self):
        out = self._run(True)
        self.assertNotIn("Play some classical music", out)
        self.assertIn("END port=", out)

    def test_a_first_install_offers_it(self):
        self.assertIn("Play some classical music", self._run(False))


try:
    import ha_setup
except Exception:               # pragma: no cover - the setup's packages are missing
    ha_setup = None


@unittest.skipUnless(ha_setup, "ha_setup.py needs its packages")
class BrokerFromHomeAssistant(unittest.TestCase):
    def _broker(self, reason):
        ha = mock.MagicMock()
        ha.url = "http://192.0.2.9:8123"
        ha.supervisor.return_value = {"state": "started", "version": "6", "network": {"1884/tcp": 1884}, "boot": "auto"}
        out = io.StringIO()
        with mock.patch.object(ha_setup, "provision_login", return_value=("armonicopiano", "pw", reason)), \
                mock.patch.object(ha_setup, "mqtt_entry_exists", return_value=True), \
                contextlib.redirect_stdout(out):
            rc = ha_setup.cmd_broker(ha)
        return rc, out.getvalue()

    def test_a_refused_login_is_not_handed_over(self):
        rc, out = self._broker("Not authorized")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("user\t", out)
        self.assertNotIn("pass\t", out)
        self.assertIn("refused\tyes", out)

    def test_an_accepted_login_is(self):
        rc, out = self._broker("ok")
        self.assertEqual(rc, 0)
        self.assertIn("user\tarmonicopiano\npass\tpw", out)

    def test_the_installer_asks_for_the_login_then(self):
        body = functions("connect_home_assistant")
        self.assertIn("refused) refused=\"yes\" ;;", body)
        i = body.index('if [ "$refused" = "yes" ]; then')
        self.assertIn('HA_DONE="package"', body[i:i + 400])
        self.assertLess(i, body.index('HA_DONE="yes"'))


try:
    import setup_web
except Exception:               # pragma: no cover
    setup_web = None


@unittest.skipUnless(setup_web, "setup_web.py needs mido and paho-mqtt")
class SetupScreen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "defaults").write_text("MQTT_HOST='192.0.2.5'\nMQTT_USER='piano'\nMQTT_PASS='saved'\n")
        args = mock.Mock(defaults=str(self.tmp / "defaults"), result=str(self.tmp / "result"), device="no",
                         title="Armonico", repo="r", lesson_port="8099", ha_host="", ha_where="", ha_version="",
                         ha_name="", ha_broker="no", ha_done="no", local_broker="no")
        self.tests = []
        patches = [mock.patch.dict(os.environ, {"SETUP_CODE": "123456"}),
                   mock.patch.object(setup_web, "mqtt_test", side_effect=self._mqtt),
                   mock.patch.object(setup_web, "midi_devices", return_value=["Keys"]),
                   mock.patch.object(setup_web.time, "sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        setup_web.Handler.state = setup_web.State(args)
        self.state = setup_web.Handler.state
        self.server = setup_web.ThreadingHTTPServer(("127.0.0.1", 0), setup_web.Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _mqtt(self, host, port, user, password):
        self.tests.append((host, port, user, password))
        return "OK"

    def req(self, path, body=None, cookie=None, source="127.0.0.1"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10, source_address=(source, 0))
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if cookie:
            h["Cookie"] = cookie
        c.request("POST" if body is not None else "GET", path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read().decode()
        c.close()
        return r.status, r.getheader("Set-Cookie"), data

    def login(self, source="127.0.0.1"):
        s, ck, _ = self.req("/api/login", {"code": "123456"}, source=source)
        self.assertEqual(s, 200)
        return ck.split(";")[0]

    def save(self, cookie, **over):
        v = {"MQTT_HOST": "192.0.2.5", "MQTT_PORT": "1883", "MQTT_USER": "piano", "MQTT_PASS": "",
             "MIDI_DEVICE": "Keys", "KEY_LOWEST": "21", "KEY_HIGHEST": "108", "STOP_KEY": "21", "UI_LANG": "en",
             "LESSON_HOSTS": "", "SCREEN_CODE": "on"}
        v.update(over)
        return json.loads(self.req("/api/save", v, cookie)[2])

    def test_wrong_codes_pause_only_the_address_that_sent_them(self):
        for _ in range(5):
            self.assertEqual(self.req("/api/login", {"code": "000000"}, source="127.0.0.2")[0], 403)
        self.assertEqual(self.req("/api/login", {"code": "123456"}, source="127.0.0.2")[0], 429)
        self.login("127.0.0.1")                          # the owner is not locked out

    def test_the_table_of_addresses_stays_small(self):
        for i in range(setup_web.MAX_CLIENTS + 20):
            self.state.fails[f"10.0.{i // 250}.{i % 250}"] = (1, 0.0)
        self.req("/api/login", {"code": "000000"}, source="127.0.0.3")
        self.assertLessEqual(len(self.state.fails), setup_web.MAX_CLIENTS)

    def test_saving_needs_a_test_that_passed_with_the_same_values(self):
        ck = self.login()
        self.assertEqual(self.save(ck)["error"], "Test the connection with these values first")
        self.req("/api/mqtt", {"host": "192.0.2.5", "port": "1883", "user": "piano", "password": ""}, ck)
        self.assertEqual(self.tests[-1], ("192.0.2.5", "1883", "piano", "saved"))
        self.assertEqual(self.save(ck, MQTT_HOST="192.0.2.99")["error"], "Test the connection with these values first")
        self.assertTrue(self.save(ck).get("ok"))
        result = (self.tmp / "result").read_text()
        self.assertIn("MQTT_PASS\tsaved\n", result)

    def test_the_saved_password_never_goes_to_another_address(self):
        ck = self.login()
        self.req("/api/mqtt", {"host": "attacker.example", "port": "1883", "user": "piano", "password": ""}, ck)
        self.assertEqual(self.tests[-1], ("attacker.example", "1883", "piano", ""))
        r = self.save(ck, MQTT_HOST="attacker.example")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("MQTT_PASS\t\n", (self.tmp / "result").read_text())

    def test_the_port_and_the_address_are_checked(self):
        ck = self.login()
        for host, port in (("192.0.2.5", "notaport"), ("192.0.2.5", "70000"), ("a b", "1883"), ("-x", "1883")):
            r = json.loads(self.req("/api/mqtt", {"host": host, "port": port, "user": "", "password": ""}, ck)[2])
            self.assertIn("error", r, (host, port))
            r = self.save(ck, MQTT_HOST=host, MQTT_PORT=port)
            self.assertIn("error", r, (host, port))
        self.assertEqual(self.tests, [])
        self.assertTrue(setup_web.broker_host_ok("fd00::5"))
        self.assertTrue(setup_web.broker_host_ok("homeassistant.local"))

    def test_the_keyboard_has_to_be_connected(self):
        ck = self.login()
        self.req("/api/mqtt", {"host": "192.0.2.5", "port": "1883", "user": "piano", "password": ""}, ck)
        self.assertEqual(self.save(ck, MIDI_DEVICE="Gone")["error"], "That keyboard is not connected anymore")

    def test_the_new_sentences_have_their_hebrew(self):
        src = (ROOT / "setup" / "setup_web.py").read_text()
        for s in ("The address has letters, digits, dots, hyphens and colons only",
                  "Test the connection with these values first"):
            self.assertIn(f"'{s}': '", src)
        self.assertIn("sudo armonico integrations</code> and paste it", src)
        self.assertNotIn("The token is not kept on the piano", src)


@unittest.skipUnless(setup_web, "setup_web.py needs mido and paho-mqtt")
class SetupScreenTimeout(unittest.TestCase):
    def test_the_timeout_counts_from_the_last_use(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        p = subprocess.Popen([sys.executable, str(ROOT / "setup" / "setup_web.py"), "--result", str(tmp / "r"),
                              "--port", str(port), "--timeout", "3"], env=dict(os.environ, SETUP_CODE="123456"))
        self.addCleanup(lambda: p.poll() is None and p.kill())
        start = time.time()
        for _ in range(4):                                   # used every 1.5 s, for 6 s: longer than the timeout
            time.sleep(1.5)
            for _ in range(20):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    c.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
                    c.getresponse().read()
                    c.close()
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertIsNone(p.poll(), "stopped while in use")
        self.assertEqual(p.wait(15), 3)                      # then unused: it stops
        self.assertGreater(time.time() - start, 8)


if __name__ == "__main__":
    unittest.main()
