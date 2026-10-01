"""The bridge (bridge/piano_bridge.sh), its functions run for real in bash against stand-ins for
mosquitto_pub, aconnect, aplaymidi and aseqdump: the stop that needs no broker, the stop lock, a
stop key that does not wait on a dead broker, the guarded reset, the port lookup, the PID files,
an alarm that cannot start, shortcuts while the alarm rings, and the status coming back online
after the broker connection dropped (against a real Mosquitto, when it is installed).

Every run gets a session of its own (setsid), and pkill and pgrep only look inside it, so the
bridge's clean-ups never reach a process outside the test."""
import getpass
import os
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "bridge" / "piano_bridge.sh"

ACONNECT = textwrap.dedent("""\
    client 0: 'System' [type=kernel]
        0 'Timer           '
        1 'Announce        '
    client 14: 'Midi Through' [type=kernel]
        0 'Midi Through Port-0'
    client 24: 'Digital Keyboard' [type=kernel,card=1]
        0 'Digital Keyboard MIDI 1'
""")

STUBS = {
    # a dead broker: every publish hangs until the bridge's own timeout ends it
    "mosquitto_pub": 'echo "$*" >> "$T/pub.log"; [ -e "$T/dead" ] && sleep 30; exit 0',
    "aconnect": 'cat "$T/aconnect"',
    "aplaymidi": 'echo "APLAY $*" >> "$T/pub.log"; sleep "${APLAY_SECS:-0.1}"; exit "${APLAY_EXIT:-0}"',
    "aseqdump": 'cat "$T/keys" 2>/dev/null; sleep 30',
}


def key(n):
    return f" 24:0   Note on                 0, note {n}, velocity 80\n"


class Bridge(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, ignore_errors=True)
        bin_ = self.t / "bin"
        bin_.mkdir()
        for name, body in STUBS.items():
            f = bin_ / name
            f.write_text("#!/bin/bash\n" + body + "\n")
            f.chmod(0o755)
        for name in ("pkill", "pgrep"):         # the whole bridge too only reaches its own session
            f = bin_ / name
            f.write_text(f'#!/bin/bash\nexec {shutil.which(name)} -s 0 "$@"\n')
            f.chmod(0o755)
        for d in ("run", "data", "songs"):
            (self.t / d).mkdir()
        (self.t / "aconnect").write_text(ACONNECT)
        (self.t / "songs" / "wake_up.mid").write_bytes(b"MThd")
        text = BRIDGE.read_text()
        (self.t / "lib.sh").write_text(text[:text.index("# Entry point")])
        self.env = dict(os.environ, T=str(self.t), PATH=f"{bin_}:{os.environ['PATH']}",
                        MQTT_HOST="127.0.0.1", MQTT_PORT="1", MIDI_DEVICE="Digital Keyboard",
                        RUN_DIR=str(self.t / "run"), DATA_DIR=str(self.t / "data"),
                        SONGS_DIR=str(self.t / "songs"), STOP_KEY="36", UI_LANG="en")
        self.env.pop("MQTT_ADMIN_SECRET", None)
        self.run_dir, self.data = self.t / "run", self.t / "data"

    def sh(self, code, timeout=30, **env):
        script = f'source "$T/lib.sh"\ntrap - EXIT SIGTERM SIGINT\n{code}'
        r = subprocess.run(["setsid", "bash", "-c", script], env=dict(self.env, **env),
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout

    def pubs(self):
        f = self.t / "pub.log"
        return f.read_text() if f.exists() else ""


class LocalStop(Bridge):
    def test_the_request_file_stops_the_alarm_with_its_reason(self):
        (self.data / "stop-request").write_text("Armonico Pi stop button\n")
        self.sh('touch "$FLAG_ALARM"; echo wake_up > "$CURRENT_ALARM_SONG"\n'
                'STOP_IN_BG=1 check_stop_request; wait')
        self.assertFalse((self.run_dir / "piano_alarm_active").exists())
        self.assertFalse((self.data / "stop-request").exists())
        self.assertIn("-t piano/alarm/stopped_by -m Armonico Pi stop button -r", self.pubs())

    def test_a_left_over_request_stops_nothing(self):
        req = self.data / "stop-request"
        req.write_text("old\n")
        os.utime(req, (time.time() - 120, time.time() - 120))
        self.sh('touch "$FLAG_ALARM"; check_stop_request')
        self.assertTrue((self.run_dir / "piano_alarm_active").exists())
        self.assertFalse(req.exists())

    def test_it_stops_at_once_with_a_dead_broker(self):
        (self.t / "dead").touch()
        (self.data / "stop-request").write_text("x\n")
        out = self.sh('touch "$FLAG_ALARM"; s=$EPOCHREALTIME; STOP_IN_BG=1 check_stop_request >/dev/null 2>&1\n'
                      '[ -f "$FLAG_ALARM" ] && echo STILL; echo "took $(( (${EPOCHREALTIME/./} - ${s/./}) / 1000 ))"\n'
                      'pkill -f "sleep 30"')
        self.assertNotIn("STILL", out)
        self.assertLess(int(out.split("took ")[1]), 2000, out)


class StopLock(Bridge):
    def test_an_alarm_that_started_after_a_stop_is_still_stopped(self):
        """A song was stopped, the alarm started within 3 seconds: its stop used to be dropped."""
        self.sh('touch "$FLAG_SONG"; echo s > "$CURRENT_SONG"; stop_now "first"\n'
                'sleep 0.05; touch "$FLAG_ALARM"; stop_now "second"')
        self.assertFalse((self.run_dir / "piano_alarm_active").exists())
        self.assertIn("-t piano/alarm/stopped_by -m second -r", self.pubs())

    def test_a_second_stop_of_the_same_alarm_is_dropped(self):
        out = self.sh('touch "$FLAG_ALARM"; sleep 0.05; touch "$FLAG_STOP_LOCK"\n'
                      'stop_locked && echo LOCKED; stop_now "dup"; [ -f "$FLAG_ALARM" ] && echo STILL')
        self.assertIn("LOCKED", out)
        self.assertIn("STILL", out)

    def test_a_stop_while_a_song_waits_for_the_order_lock_keeps_it_from_playing(self):
        """The order lock is held by the last stop's publishes: a stop in that wait found nothing to
        kill, and the song then played to its end with "playing" left on the broker."""
        self.sh('( exec 8>>"$PUB_ORDER_LOCK"; flock 8; sleep 1.5 ) &\n'
                'sleep 0.2; APLAY_SECS=2 start_song "wake_up" & sleep 0.5; stop_now "while waiting"; wait')
        self.assertNotIn("wake_up.mid", self.pubs())          # the panic it sends is fine, the song is not
        self.assertNotIn("piano/play/status -m playing", self.pubs())

    def test_a_stop_while_the_alarm_waits_for_the_order_lock_keeps_it_from_ringing(self):
        self.sh('( exec 8>>"$PUB_ORDER_LOCK"; flock 8; sleep 1.5 ) &\n'
                'sleep 0.2; start_alarm "wake_up" & sleep 0.5; stop_now "while waiting"; wait')
        self.assertNotIn("wake_up.mid", self.pubs())          # the panic it sends is fine, the song is not
        self.assertNotIn("piano/alarm/status -m on", self.pubs())

    def test_a_stop_with_nothing_playing_takes_no_lock(self):
        self.sh('stop_now "nothing"; stop_key_pressed')
        self.assertFalse((self.run_dir / "piano_stop_lock").exists())


class KeyListener(Bridge):
    def listen(self, keys, setup="", wait="sleep 1.5"):
        (self.t / "keys").write_text("".join(key(k) for k in keys))
        return self.sh(f'{setup}\nrun_key_listener 24 & kl=$!\n{wait}\nkill_tree "$kl"')

    def test_the_stop_key_stops_the_alarm_at_once_with_a_dead_broker(self):
        """Every note was published first, 5 seconds each with a broker that does not answer."""
        (self.t / "dead").touch()
        out = self.listen([60, 62, 36], setup='touch "$FLAG_ALARM"; s=$EPOCHREALTIME',
                          wait='for _ in $(seq 50); do [ -f "$FLAG_ALARM" ] || break; sleep 0.1; done\n'
                               'echo "took $(( (${EPOCHREALTIME/./} - ${s/./}) / 1000 ))"')
        self.assertFalse((self.run_dir / "piano_alarm_active").exists())
        self.assertLess(int(out.split("took ")[1]), 2500, out)

    def test_the_stop_key_and_the_ringing_alarm_make_no_shortcut(self):
        (self.data / "shortcuts.conf").write_text("36 60 62 64|lights\n")
        self.listen([36, 60, 62, 64], setup='touch "$FLAG_ALARM"')
        self.assertNotIn("piano/shortcut -m lights", self.pubs())

    def test_the_same_keys_make_the_shortcut_when_nothing_plays(self):
        (self.data / "shortcuts.conf").write_text("36 60 62 64|lights\n")
        self.listen([36, 60, 62, 64])
        self.assertIn("piano/shortcut -m lights", self.pubs())

    def test_a_long_shortcut_is_not_found_in_the_middle_of_other_keys(self):
        (self.data / "shortcuts.conf").write_text("60 62 64 65|night\n")
        self.listen([50, 60, 62, 64, 65])
        self.assertNotIn("piano/shortcut -m night", self.pubs())


class Commands(Bridge):
    def test_reset_needs_the_admin_secret(self):
        self.sh("handle_shortcut_cmd 'reset|42|'")
        self.sh("handle_shortcut_cmd 'reset|42|secret:wrong'", MQTT_ADMIN_SECRET="s3cret")
        self.assertFalse((self.data / "reset-request").exists())
        self.assertIn("🚫", self.pubs())
        self.sh("handle_shortcut_cmd 'reset|42|secret:s3cret'", MQTT_ADMIN_SECRET="s3cret")
        self.assertEqual((self.data / "reset-request").read_text().strip(), "42")

    def test_home_assistant_sends_reset_with_the_secret(self):
        self.assertIn('payload: "reset|{{ chat }}|secret:{{ admin }}"',
                      (ROOT / "home-assistant" / "piano.yaml").read_text())

    def test_learning_is_refused_during_a_lesson(self):
        self.sh('echo $$ > "$FLAG_GAME"; handle_shortcut_cmd "learn|42|tune"')
        self.assertFalse((self.run_dir / "piano_learn").exists())
        self.assertIn("A lesson is running", self.pubs())

    def test_two_long_shortcuts_that_start_the_same_way_are_refused(self):
        conf = self.data / "shortcuts.conf"
        self.sh("shortcut_save night '60 62 64 65' 1; shortcut_save late '60 62 64 65 67' 1; "
                "shortcut_save early '60 62 64' 1; shortcut_save other '60 62 64 67' 1")
        self.assertEqual(conf.read_text(), "60 62 64 65|night\n60 62 64|early\n60 62 64 67|other\n")
        self.assertIn("start the same way", self.pubs())

    def test_a_name_with_a_backslash_is_replaced_and_deleted(self):
        conf = self.data / "shortcuts.conf"
        self.sh(r"shortcut_save 'a\b' '60 62' 1; shortcut_save 'a\b' '64 65' 1")
        self.assertEqual(conf.read_text(), "64 65|a\\b\n")
        self.sh(r"shortcut_delete 'a\b' 1")
        self.assertEqual(conf.read_text(), "")


class Port(Bridge):
    def port(self, listing):
        (self.t / "aconnect").write_text(listing)
        return self.sh("get_port").strip()

    def test_the_client_line_counts_not_a_port_line(self):
        listing = ("client 0: 'System' [type=kernel]\n    1 'Digital Keyboard Announce'\n"
                   "client 20: 'Digital Keyboard Pro' [type=kernel,card=2]\n    0 'Digital Keyboard Pro MIDI 1'\n"
                   "client 24: 'Digital Keyboard' [type=kernel,card=1]\n    0 'Digital Keyboard MIDI 1'\n")
        self.assertEqual(self.port(listing), "24")
        self.assertEqual(self.port(listing.split("client 24")[0]), "20")      # by the start of the name
        self.assertEqual(self.port("client 0: 'System' [type=kernel]\n    1 'Digital Keyboard'\n"), "")

    def test_a_new_port_is_published(self):
        (self.t / "aconnect").write_text(ACONNECT.replace("client 24", "client 25"))
        self.sh('bash -c \'exec -a "aseqdump -p 25" sleep 30\' & run_watchdog 24 & wd=$!\n'
                'sleep 1.6; kill_tree "$wd"; pkill -f "aseqdump -p"')
        self.assertIn("-t piano/port -m 25 -r", self.pubs())


class Playback(Bridge):
    def test_a_song_that_ends_at_once_leaves_no_pid_file(self):
        self.sh("start_song 'wake_up|Test'; wait", APLAY_SECS="0", APLAY_EXIT="1")
        self.assertFalse((self.run_dir / "piano_song_pid").exists())

    def test_the_player_starts_before_the_status_and_the_length_are_published(self):
        # the length of the file (mido) is worked out beside the player, in the background, not before it
        self.sh("start_song 'wake_up|Test'; wait", APLAY_SECS="1.5")
        pubs = self.pubs()
        lines = pubs.splitlines()
        play = next(i for i, l in enumerate(lines) if l.startswith("APLAY"))
        status = next(i for i, l in enumerate(lines) if "piano/play/status -m playing" in l)
        self.assertLess(play, status + 1)
        self.assertTrue(any("piano/play/duration -m" in l and "-n" not in l.split() for l in lines))

    def test_an_alarm_loop_that_ends_removes_its_pid_file(self):
        self.sh("start_alarm 'wake_up|Test'; sleep 0.3; rm -f \"$FLAG_ALARM\"; wait")
        self.assertFalse((self.run_dir / "piano_alarm_pid").exists())

    def test_a_pid_that_is_not_the_bridge_is_not_killed(self):
        out = self.sh('sleep 30 & p=$!; echo "$p" > "$PID_FILE_SONG"; kill_pid_file "$PID_FILE_SONG"\n'
                      'sleep 0.1; kill -0 "$p" && echo ALIVE; kill "$p"')
        self.assertIn("ALIVE", out)

    def test_an_alarm_with_no_song_says_so(self):
        self.sh("start_alarm 'wake_upp|Alarm clock'")
        self.assertIn("-t piano/alarm/error -m Song not found: wake_upp -r", self.pubs())
        self.assertIn("-t piano/alarm/status -m failed -r", self.pubs())

    def test_an_alarm_with_the_keyboard_off_says_so(self):
        (self.t / "aconnect").write_text("client 0: 'System' [type=kernel]\n")
        self.sh("start_alarm 'wake_up|Alarm clock'")
        self.assertIn("-t piano/alarm/error -m Keyboard not connected -r", self.pubs())

    def test_a_ringing_alarm_is_not_marked_failed(self):
        self.sh("touch \"$FLAG_ALARM\"; start_alarm 'nothing_here'")
        self.assertNotIn("failed", self.pubs())

    def test_the_blueprint_ends_on_a_missing_song(self):
        text = (ROOT / "home-assistant" / "blueprint_alarm.yaml").read_text()
        self.assertIn("startswith('Song not found')", text)
        self.assertIn("- stop:", text)


class Teardown(Bridge):
    def test_kill_tree_ends_the_pipeline_under_a_subshell(self):
        # gone or a zombie: in a container nothing may be there to reap what was orphaned
        out = self.sh('( sleep 31 | while read -r _; do :; done ) & p=$!; sleep 0.2; s=$(pgrep -f "sleep 31")\n'
                      'kill_tree "$p"; sleep 0.3; case "$(ps -o stat= -p "$s")" in ""|Z*) echo GONE ;; esac')
        self.assertIn("GONE", out)

    def test_cleanup_ends_in_time_with_a_dead_broker(self):
        (self.t / "dead").touch()
        start = time.time()
        self.sh("cleanup")
        self.assertLess(time.time() - start, 6)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(shutil.which("mosquitto") and shutil.which("mosquitto_sub"), "Mosquitto is not installed")
class BrokerReconnect(Bridge):
    """A retained "offline" (the last will of a dropped connection) is set right again at once."""

    def setUp(self):
        super().setUp()
        (self.t / "bin" / "mosquitto_pub").unlink()          # the real one, against a real broker
        self.port = free_port()
        conf = self.t / "mosquitto.conf"
        conf.write_text(f"listener {self.port} 127.0.0.1\nallow_anonymous true\nuser {getpass.getuser()}\n")
        self.broker = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        self.addCleanup(self.broker.wait)
        self.addCleanup(self.broker.terminate)
        self.env["MQTT_PORT"] = str(self.port)
        time.sleep(0.3)

    def status(self):
        return subprocess.run(["mosquitto_sub", "-h", "127.0.0.1", "-p", str(self.port), "-t", "piano/status",
                               "-C", "1", "-W", "2"], capture_output=True, text=True).stdout.strip()

    def wait_for(self, want, secs=15):
        end = time.time() + secs
        while time.time() < end:
            if self.status() == want:
                return True
            time.sleep(0.2)
        return False

    def test_offline_is_turned_back_to_online(self):
        bridge = subprocess.Popen(["setsid", "bash", str(BRIDGE)], env=self.env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_for("online"))
            subprocess.run(["mosquitto_pub", "-h", "127.0.0.1", "-p", str(self.port), "-t", "piano/status",
                            "-m", "offline", "-r"], check=True)
            time.sleep(1.5)
            self.assertEqual(self.status(), "online")
        finally:
            bridge.terminate()
            bridge.wait(20)
        self.assertEqual(self.status(), "offline")
        left = subprocess.run(["pgrep", "-f", f"mosquitto_sub .*-p {self.port}"], capture_output=True, text=True)
        self.assertEqual(left.stdout.strip(), "")

    def test_an_alarm_cut_by_a_restart_is_ended_not_rung_again(self):
        # the bridge went down while the alarm rang (a power cut leaves the mark on disk)
        data = self.t / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "alarm-ringing").write_text("")
        bridge = subprocess.Popen(["setsid", "bash", str(BRIDGE)], env=self.env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(self.wait_for("online"))
            got = subprocess.run(["mosquitto_sub", "-h", "127.0.0.1", "-p", str(self.port), "-t", "piano/alarm/stopped_by",
                                  "-C", "1", "-W", "2"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(got, "Restart", "Home Assistant's alarm loop ends on it, as on a stop")
            self.assertFalse((data / "alarm-ringing").exists())
        finally:
            bridge.terminate()
            bridge.wait(20)


if __name__ == "__main__":
    unittest.main()
