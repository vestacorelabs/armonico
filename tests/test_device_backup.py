"""The device's backup and restore (device/backup.py): the file and its manifest, the checks that
refuse a wrong file, restore with its automatic way back, undo, the root request, and the transports."""
import base64
import ftplib
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = load("armonico_device_backup", "device/backup.py")
MIDI = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"


class FakeServices:
    """Stands in for systemd: counts the calls, and says "up" or "down" as told."""

    def __init__(self, checks=(True,)):
        self.checks = list(checks)
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")

    def check(self):
        self.calls.append("check")
        ok = self.checks.pop(0) if self.checks else True
        return ok, "" if ok else "not running: armonico-lessons"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.etc, self.data, self.app = t / "etc" / "armonico", t / "data", t / "app"
        for d in (self.etc / "modes", self.data / "songs", self.data / "device" / "samples",
                  self.data / "profiles" / "p12345678", self.app):
            d.mkdir(parents=True)
        (self.app / "VERSION").write_text("1.1\n")
        (self.etc / "device").write_text("DEVICE_INSTALL='yes'\n")
        (self.etc / "config.env").write_text(
            "# Armonico settings\nMQTT_HOST='127.0.0.1'\nMQTT_PORT='1883'\nMQTT_USER='piano'\nMQTT_PASS='secret'\n"
            "MQTT_LOCAL='yes'\nLESSON_UI_PORT='8099'\nDEVICE='yes'\n")
        (self.etc / "modes" / "standalone.env").write_text("MQTT_HOST='127.0.0.1'\nMQTT_LOCAL='yes'\n")
        (self.data / "lessons.json").write_text('{"ode": 3}')
        (self.data / "history.json").write_text("[]")
        (self.data / "profiles.json").write_text(json.dumps({"active": "main", "list": [{"id": "main"}, {"id": "p12345678"}]}))
        (self.data / "profiles" / "p12345678" / "lessons.json").write_text('{"ode": 1}')
        (self.data / "songs" / "ode.mid").write_bytes(MIDI)
        (self.data / "device" / "settings.json").write_text('{"theme": "sky"}')
        (self.data / "device" / "samples" / "60.wav").write_bytes(b"RIFF" * 100)
        (self.data / "screen_code").write_text("123456\n")
        (self.data / "update-request").write_text("x")
        self.paths = backup.Paths(self.data, self.etc, self.data / "songs", self.app)
        self.out = t / "out"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self):
        return Path(backup.create(self.out, paths=self.paths))

    def rewrite(self, src, dst, change=None, manifest=None):
        """A copy of a backup with one file or the manifest changed."""
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(dst, "w") as out:
            for n in z.namelist():
                data = z.read(n)
                if n == "manifest.json" and manifest is not None:
                    m = json.loads(data)
                    manifest(m)
                    data = json.dumps(m).encode()
                elif change and n in change:
                    data = change[n]
                out.writestr(n, data)
        return dst


class Create(Base):
    def test_file_name_manifest_and_contents(self):
        steps = []
        f = Path(backup.create(self.out, lambda k, en, he: steps.append(k), paths=self.paths))
        self.assertRegex(f.name, r"^armonico-backup-\d{4}-\d{2}-\d{2}-\d{4}\.armonico$")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)          # it holds passwords
        with zipfile.ZipFile(f) as z:
            names = set(z.namelist())
            man = json.loads(z.read("manifest.json"))
            for e in man["files"]:
                self.assertEqual(hashlib.sha256(z.read(e["path"])).hexdigest(), e["sha256"])
        self.assertEqual((man["format"], man["format_version"], man["version"], man["install"]),
                         ("armonico-backup", 1, "1.1", "device"))
        self.assertTrue(man["created"] and man["hostname"])
        for n in ("etc/config.env", "etc/modes/standalone.env", "data/lessons.json", "data/profiles.json",
                  "data/profiles/p12345678/lessons.json", "data/songs/ode.mid", "data/device/settings.json",
                  "data/screen_code"):
            self.assertIn(n, names)
        self.assertNotIn("data/device/samples/60.wav", names)           # rendered again by "prepare"
        self.assertNotIn("data/update-request", names)
        self.assertEqual(names - {"manifest.json"}, {e["path"] for e in man["files"]})
        self.assertEqual(steps[:1], ["pack"])
        self.assertIn("verify", steps)

    def test_backups_and_links_are_left_out(self):
        (self.data / "backups").mkdir()
        (self.data / "backups" / "old.armonico").write_text("x")
        os.symlink(self.etc / "config.env", self.data / "link.json")
        with zipfile.ZipFile(self.make()) as z:
            self.assertFalse([n for n in z.namelist() if "backups" in n or "link.json" in n])

    def test_same_minute_gets_a_second_name(self):
        a, b = self.make(), self.make()
        self.assertNotEqual(a, b)
        self.assertTrue(b.name.endswith("-2.armonico"))

    def test_songs_folder_elsewhere(self):
        songs = Path(self.tmp.name) / "songs-elsewhere"
        songs.mkdir()
        (songs / "far.mid").write_bytes(MIDI)
        self.paths.songs = songs
        with zipfile.ZipFile(self.make()) as z:
            self.assertIn("data/songs/far.mid", z.namelist())
            self.assertNotIn("data/songs/ode.mid", z.namelist())


class Validate(Base):
    def test_good_file_and_what_is_inside(self):
        chk = backup.validate(self.make(), self.paths)
        self.assertTrue(chk.ok)
        self.assertEqual(chk.reason_key, "ok")
        i = chk.info
        self.assertEqual((i["version"], i["type"], i["profiles"], i["songs"], i["broker"], i["partial"]),
                         ("1.1", "device", 2, 1, "local", False))
        self.assertTrue(i["date"])

    def test_not_a_zip(self):
        f = self.out / "notes.armonico"
        f.write_text("hello")
        chk = backup.validate(f, self.paths)
        self.assertFalse(chk.ok)
        self.assertEqual(chk.reason_key, "not_zip")
        self.assertTrue(chk.reason_he)

    def test_missing_file(self):
        self.assertEqual(backup.validate(self.out / "nope.armonico", self.paths).reason_key, "missing")

    def test_no_manifest(self):
        f = self.out / "notes.zip"
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("notes.txt", "x")
        chk = backup.validate(f, self.paths)
        self.assertFalse(chk.ok)
        self.assertEqual(chk.reason_en, "Not an Armonico backup: no manifest.json inside")
        self.assertEqual(chk.reason_he, "לא קובץ גיבוי של Armonico: אין בו manifest.json")

    def test_wrong_format(self):
        f = self.rewrite(self.make(), self.out / "w.zip", manifest=lambda m: m.update(format="something-else"))
        self.assertEqual(backup.validate(f, self.paths).reason_key, "no_manifest")

    def test_old_lesson_screen_backup_is_not_ours(self):
        f = self.out / "armonico-backup-main-2026-10-01.zip"
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("backup.json", json.dumps({"app": "armonico", "format": 2}))
            z.writestr("lessons.json", "{}")
        self.assertEqual(backup.validate(f, self.paths).reason_key, "no_manifest")

    def test_bad_checksum(self):
        f = self.rewrite(self.make(), self.out / "bad.armonico", change={"data/lessons.json": b'{"ode": 99}'})
        chk = backup.validate(f, self.paths)
        self.assertFalse(chk.ok)
        self.assertEqual(chk.reason_key, "checksum")

    def test_file_not_in_manifest(self):
        src = self.make()
        f = self.out / "extra.armonico"
        self.rewrite(src, f)
        with zipfile.ZipFile(f, "a") as z:
            z.writestr("data/extra.json", "{}")
        self.assertEqual(backup.validate(f, self.paths).reason_key, "checksum")

    def test_newer_format(self):
        f = self.rewrite(self.make(), self.out / "new.armonico", manifest=lambda m: m.update(format_version=2))
        chk = backup.validate(f, self.paths)
        self.assertFalse(chk.ok)
        self.assertEqual(chk.reason_key, "newer")

    def test_regular_install_backup_is_partial(self):
        f = self.rewrite(self.make(), self.out / "reg.armonico", manifest=lambda m: m.update(install="regular"))
        chk = backup.validate(f, self.paths)
        self.assertTrue(chk.ok)
        self.assertEqual(chk.reason_key, "regular")
        self.assertTrue(chk.info["partial"])
        self.assertIn("only progress and songs", chk.reason_en)

    def _with(self, name, data):
        f = self.out / "x.armonico"
        man = {"format": "armonico-backup", "format_version": 1, "install": "device",
               "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}]}
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("manifest.json", json.dumps(man))
            z.writestr(name, data)
        return backup.validate(f, self.paths)

    def test_unsafe_names_and_config(self):
        self.assertEqual(self._with("data/../../evil", b"x").reason_key, "unsafe")
        self.assertEqual(self._with("/data/x", b"x").reason_key, "unsafe")
        self.assertEqual(self._with("other/x", b"x").reason_key, "unsafe")
        self.assertEqual(self._with("etc/shadow", b"x").reason_key, "unsafe")
        self.assertEqual(self._with("etc/config.env", b"MQTT_HOST=$(reboot)\n").reason_key, "unsafe")
        self.assertTrue(self._with("etc/config.env", b"# ok\nMQTT_HOST='a b'\n").ok)


class Restore(Base):
    def test_restore_brings_back_the_saved_state_and_undo_goes_back(self):
        f = self.make()
        (self.data / "lessons.json").write_text('{"ode": 7}')
        (self.data / "songs" / "new.mid").write_bytes(MIDI)
        (self.etc / "config.env").write_text("MQTT_HOST='10.0.0.5'\nMQTT_LOCAL='no'\n")
        svc, steps = FakeServices(), []
        r = backup.restore(f, lambda k, en, he: steps.append(k), self.paths, svc)
        self.assertTrue(r["ok"])
        self.assertEqual(r["key"], "restored")
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 3}')
        self.assertFalse((self.data / "songs" / "new.mid").exists())           # a full restore is the saved state
        self.assertIn("MQTT_HOST='127.0.0.1'", (self.etc / "config.env").read_text())
        self.assertEqual(svc.calls, ["stop", "start", "check"])
        self.assertEqual(steps, ["check", "snapshot", "restore", "restore_connection", "restart"])
        self.assertRegex(Path(r["before"]).name, r"^before-restore-.*\.armonico$")
        self.assertEqual(Path(r["before"]).parent, self.data / "backups")
        st = backup.last_restore(self.paths)
        self.assertTrue(st["can_undo"])
        self.assertEqual(st["file"], f.name)

        u = backup.undo_last(None, self.paths, FakeServices())
        self.assertTrue(u["ok"])
        self.assertEqual(u["key"], "undone")
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 7}')
        self.assertTrue((self.data / "songs" / "new.mid").exists())
        self.assertIn("10.0.0.5", (self.etc / "config.env").read_text())
        self.assertFalse(backup.last_restore(self.paths)["can_undo"])
        with self.assertRaises(backup.BackupError) as e:
            backup.undo_last(None, self.paths, FakeServices())
        self.assertEqual(e.exception.key, "no_undo")

    def test_services_fail_and_it_goes_back_by_itself(self):
        f = self.make()
        (self.data / "lessons.json").write_text('{"ode": 7}')
        svc, steps = FakeServices([False, True]), []
        r = backup.restore(f, lambda k, en, he: steps.append(k), self.paths, svc)
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(r["key"], "rolled_back")
        self.assertIn("rollback", steps)
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 7}')
        self.assertEqual(svc.calls, ["stop", "start", "check", "stop", "start", "check"])
        self.assertFalse(backup.last_restore(self.paths)["can_undo"])

    def test_refused_file_changes_nothing(self):
        f = self.rewrite(self.make(), self.out / "bad.armonico", change={"data/lessons.json": b"{}"})
        (self.data / "lessons.json").write_text('{"ode": 7}')
        svc = FakeServices()
        with self.assertRaises(backup.BackupError) as e:
            backup.restore(f, None, self.paths, svc)
        self.assertEqual(e.exception.key, "checksum")
        self.assertEqual(svc.calls, [])
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 7}')
        self.assertFalse((self.data / "backups").exists() and list((self.data / "backups").glob("before-*")))

    def test_regular_backup_restores_only_progress_and_songs(self):
        f = self.rewrite(self.make(), self.out / "reg.armonico", manifest=lambda m: m.update(install="regular"))
        (self.data / "lessons.json").write_text('{"ode": 7}')
        (self.data / "device" / "settings.json").write_text('{"theme": "classic"}')
        (self.etc / "config.env").write_text("MQTT_HOST='10.0.0.5'\n")
        (self.data / "songs" / "mine.mid").write_bytes(MIDI)
        r = backup.restore(f, None, self.paths, FakeServices())
        self.assertTrue(r["ok"])
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 3}')
        self.assertEqual((self.data / "device" / "settings.json").read_text(), '{"theme": "classic"}')
        self.assertEqual((self.etc / "config.env").read_text(), "MQTT_HOST='10.0.0.5'\n")
        self.assertTrue((self.data / "songs" / "mine.mid").exists())            # partial adds, never removes

    def test_config_keeps_data_dir_and_drops_unknown_names(self):
        (self.etc / "config.env").write_text("MQTT_HOST='a'\nDATA_DIR='/var/lib/armonico'\nPATH='/tmp/evil'\nVENV='/tmp/v'\n")
        f = self.make()
        (self.etc / "config.env").write_text("MQTT_HOST='b'\nDATA_DIR='/srv/armonico'\n")
        backup.restore(f, None, self.paths, FakeServices())
        text = (self.etc / "config.env").read_text()
        self.assertIn("MQTT_HOST='a'", text)
        self.assertIn("DATA_DIR='/srv/armonico'", text)
        self.assertNotIn("/var/lib/armonico", text)
        self.assertNotIn("PATH=", text)
        self.assertNotIn("VENV=", text)

    def test_old_before_restore_files_are_pruned(self):
        f = self.make()
        for _ in range(5):
            backup.restore(f, None, self.paths, FakeServices())
        self.assertLessEqual(len(list((self.data / "backups").glob("before-restore-*.armonico"))), backup.KEEP_BEFORE)


class Request(Base):
    """The screen's request file, carried out the way the root unit does (here without root)."""

    def test_create_on_sd_then_restore_and_undo(self):
        sd = self.data / "backups"
        backup.request("create", {"kind": "local"}, path=str(sd), paths=self.paths)
        self.assertEqual(self.paths.request.stat().st_mode & 0o777, 0o600)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "done", job)
        self.assertFalse(self.paths.request.exists())
        made = Path(job["result"]["file"])
        self.assertTrue(made.is_file())
        self.assertEqual(backup.job_status(self.paths)["state"], "done")
        self.assertTrue(all(s["state"] == "done" for s in job["steps"]))

        (self.data / "lessons.json").write_text('{"ode": 9}')
        backup.request("restore", {"kind": "local"}, file=str(made), paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "done", job)
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 3}')

        backup.request("undo", paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "done", job)
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 9}')

    def test_refused_file_ends_the_job_with_its_reason(self):
        f = self.data / "backups" / "notes.armonico"
        f.parent.mkdir()
        f.write_text("not a zip")
        backup.request("restore", file=str(f), paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["result"]["key"], "not_zip")
        self.assertTrue(job["result"]["he"])

    def test_a_file_outside_the_allowed_places_is_refused(self):
        backup.request("restore", file=str(self.etc / "config.env"), paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["result"]["key"], "outside")

    def test_no_request_does_nothing(self):
        self.assertIsNone(backup.run_request(self.paths, FakeServices()))

    def test_worker_process(self):
        """The child the root side starts as the service user: steps and the result as JSON lines."""
        env = {**os.environ, "DATA_DIR": str(self.data), "SONGS_DIR": str(self.data / "songs"),
               "ARMONICO_ETC_DIR": str(self.etc), "ARMONICO_APP_DIR": str(self.app)}
        r = subprocess.run([sys.executable, "-I", str(ROOT / "device" / "backup.py"), "_worker", "create", str(self.out)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(x) for x in r.stdout.splitlines()]
        self.assertTrue(any(x.get("step") == "pack" for x in lines))
        self.assertTrue(backup.validate(lines[-1]["result"], self.paths).ok)


class LocalTransport(Base):
    def test_list_put_get(self):
        t = backup.Local(self.paths, roots=[self.out])
        self.assertTrue(t.test())
        f = self.make()
        (self.out / "sub").mkdir()
        t.put(str(f), str(self.out / "sub"))
        names = [e.name for e in t.listdir(str(self.out / "sub"))]
        self.assertEqual(names, [f.name])
        listing = t.listdir(str(self.out))
        self.assertTrue(listing[0].is_dir)                                  # folders first
        got = Path(self.tmp.name) / "got.armonico"
        t.get(str(self.out / "sub" / f.name), str(got))
        self.assertEqual(got.read_bytes(), f.read_bytes())

    def test_outside_is_refused(self):
        t = backup.Local(self.paths, roots=[self.out])
        with self.assertRaises(backup.TransportError) as e:
            t.listdir(str(self.etc))
        self.assertEqual(e.exception.key, "outside")
        with self.assertRaises(backup.TransportError):
            t.listdir(str(self.out / ".." / "etc"))

    def test_create_through_a_transport_reads_it_back(self):
        t = backup.Local(self.paths, roots=[self.out])
        remote = backup.create(str(self.out), paths=self.paths, transport=t)
        self.assertTrue(backup.validate(remote, self.paths).ok)
        self.assertFalse(list(self.paths.work.glob("*")))                   # nothing left behind


class FakeFTP:
    """ftplib.FTP over a dictionary."""
    files = {}

    def __init__(self, *a, **k):
        self.cwd = "/"

    def connect(self, host, port, timeout=None):
        if host == "down":
            raise ConnectionRefusedError()
        self.timeout = timeout

    def login(self, user, password):
        if (user, password) != ("u", "p"):
            raise ftplib.error_perm("530 Login incorrect")

    def set_pasv(self, on):
        pass

    def pwd(self):
        return "/"

    def mlsd(self, path="", facts=()):
        base = "/" + path.strip("/") + "/" if path.strip("/") else "/"
        yield ".", {"type": "cdir"}
        seen = set()
        for name, data in self.files.items():
            if name.startswith(base):
                rest = name[len(base):]
                if "/" in rest:
                    d = rest.split("/")[0]
                    if d not in seen:
                        seen.add(d)
                        yield d, {"type": "dir"}
                else:
                    yield rest, {"type": "file", "size": str(len(data)), "modify": "20261023183000"}

    def retrbinary(self, cmd, cb):
        name = cmd.split(" ", 1)[1]
        if name not in self.files:
            raise ftplib.error_perm("550 No such file")
        cb(self.files[name])

    def storbinary(self, cmd, f):
        self.files[cmd.split(" ", 1)[1]] = f.read()

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)

    def delete(self, name):
        self.files.pop(name, None)

    def quit(self):
        pass

    def close(self):
        pass


class FtpTransport(Base):
    def setUp(self):
        super().setUp()
        FakeFTP.files = {"/backups/old.armonico": b"x" * 10}
        p = mock.patch.object(ftplib, "FTP", FakeFTP)
        p.start()
        self.addCleanup(p.stop)

    def test_list_put_get(self):
        t = backup.Ftp("nas", "u", "p")
        self.assertTrue(t.test())
        self.assertEqual([(e.name, e.is_dir) for e in t.listdir("")], [("backups", True)])
        f = self.make()
        t.put(str(f), "/backups/" + f.name)
        entries = {e.name: e for e in t.listdir("/backups")}
        self.assertEqual(entries[f.name].size, f.stat().st_size)
        self.assertNotIn(f.name + ".part", entries)
        got = Path(self.tmp.name) / "got"
        t.get("/backups/" + f.name, str(got))
        self.assertEqual(got.read_bytes(), f.read_bytes())

    def test_errors_in_both_languages(self):
        with self.assertRaises(backup.TransportError) as e:
            backup.Ftp("nas", "u", "wrong").test()
        self.assertEqual(e.exception.key, "login")
        self.assertTrue(e.exception.he)
        with self.assertRaises(backup.TransportError) as e:
            backup.Ftp("down", "u", "p").test()
        self.assertEqual(e.exception.key, "unreachable")
        with self.assertRaises(backup.TransportError) as e:
            backup.Ftp("nas", "u", "p").get("/nope", str(Path(self.tmp.name) / "x"))
        self.assertEqual(e.exception.key, "not_found")

    def test_backup_to_ftp_and_restore_from_it(self):
        payload = {"kind": "ftp", "host": "nas", "user": "u", "password": "p", "port": 21}
        backup.request("create", payload, path="/backups", paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "done", job)
        remote = job["result"]["file"]
        self.assertIn(remote, FakeFTP.files)
        (self.data / "lessons.json").write_text('{"ode": 9}')
        backup.request("restore", payload, file=remote, paths=self.paths)
        job = backup.run_request(self.paths, FakeServices())
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["steps"][0]["key"], "download")
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 3}')


class HttpTransport(Base):
    def setUp(self):
        super().setUp()
        body = self.make().read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, with_body):
                if self.path.startswith("/private") and self.headers.get("Authorization") != \
                        "Basic " + base64.b64encode(b"u:p").decode():
                    self.send_response(401)
                    self.end_headers()
                    return
                if not self.path.endswith(".armonico"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Last-Modified", "Thu, 23 Oct 2026 18:30:00 GMT")
                self.end_headers()
                if with_body:
                    self.wfile.write(body)

            def do_GET(self):
                self._answer(True)

            def do_HEAD(self):
                self._answer(False)

        self.body = body
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_download_and_check(self):
        t = backup.Http(self.base + "/files/armonico-backup-2026-10-23-1830.armonico")
        self.assertTrue(t.test())
        [e] = t.listdir("")
        self.assertEqual((e.name, e.size, e.is_dir), ("armonico-backup-2026-10-23-1830.armonico", len(self.body), False))
        self.assertGreater(e.mtime, 0)
        got = Path(self.tmp.name) / "dl.armonico"
        t.get("", str(got))
        self.assertTrue(backup.validate(got, self.paths).ok)

    def test_basic_auth(self):
        with self.assertRaises(backup.TransportError) as e:
            backup.Http(self.base + "/private/b.armonico").test()
        self.assertEqual(e.exception.key, "login")
        self.assertTrue(backup.Http(self.base + "/private/b.armonico", "u", "p").test())

    def test_errors(self):
        with self.assertRaises(backup.TransportError) as e:
            backup.Http(self.base + "/nothing").test()
        self.assertEqual(e.exception.key, "not_found")
        with self.assertRaises(backup.TransportError) as e:
            backup.Http("ftp://example.com/x")
        self.assertEqual(e.exception.key, "bad_url")
        with self.assertRaises(backup.TransportError) as e:
            backup.Http(self.base + "/a.armonico").put("x", "y")
        self.assertEqual(e.exception.key, "download_only")


class Parsers(unittest.TestCase):
    def test_smbclient_listing(self):
        out = ("  .                                   D        0  Thu Oct 23 18:30:00 2026\n"
               "  ..                                  D        0  Thu Oct 23 18:30:00 2026\n"
               "  armonico                            D        0  Wed Oct  1 09:12:00 2026\n"
               "  armonico-backup-2026-10-23-1830.armonico      A  2400000  Thu Oct 23 18:30:00 2026\n"
               "  my notes.zip                        N   300000  Fri Sep 12 22:40:00 2026\n"
               "\n\t\t30428648 blocks of size 1024. 1000 blocks available\n")
        e = backup.parse_smb_ls(out)
        self.assertEqual([(x.name, x.is_dir, x.size) for x in e],
                         [("armonico", True, 0), ("armonico-backup-2026-10-23-1830.armonico", False, 2400000),
                          ("my notes.zip", False, 300000)])
        self.assertGreater(e[1].mtime, 0)

    def test_smbclient_shares(self):
        out = "Disk|backups|NAS backups\nDisk|ADMIN$|Remote Admin\nIPC|IPC$|IPC Service\nPrinter|hp|\n"
        self.assertEqual([x.name for x in backup.parse_smb_shares(out)], ["backups"])

    def test_long_listing(self):
        out = ("sftp> ls -ln\n"
               "drwxr-xr-x    2 1000     1000         4096 Oct 23 18:30 armonico\n"
               "-rw-r--r--    1 1000     1000      2400000 Oct  1  2025 armonico-backup-2025-10-01-0912.armonico\n"
               "lrwxrwxrwx    1 1000     1000           10 Oct  1  2025 last -> armonico\n")
        e = backup.parse_ls_l(out)
        self.assertEqual([(x.name, x.is_dir, x.size) for x in e],
                         [("armonico", True, 0), ("armonico-backup-2025-10-01-0912.armonico", False, 2400000),
                          ("last", False, 10)])

    def test_smb_host_with_share(self):
        t = backup.Smb("//192.168.1.10/share/armonico", "u", "p")
        self.assertEqual((t.host, t.share, t._split("old")), ("192.168.1.10", "share", ("share", "armonico/old")))
        t = backup.Smb("192.168.1.10")
        self.assertEqual(t._split("/backups/armonico"), ("backups", "armonico"))
        self.assertEqual(t._split("/"), ("", ""))

    def test_smb_and_scp_without_their_tools(self):
        with mock.patch.object(backup.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(backup.TransportError) as e:
                backup.Smb("//nas/share").test()
            self.assertEqual(e.exception.key, "tool")
            with self.assertRaises(backup.TransportError) as e:
                backup.Scp("pc", "u", "p", known_hosts="/tmp/kh", use_paramiko=False).test()
            self.assertEqual(e.exception.key, "tool")

    def test_smb_login_failure(self):
        r = subprocess.CompletedProcess([], 1, "", "session setup failed: NT_STATUS_LOGON_FAILURE")
        with mock.patch.object(backup.subprocess, "run", return_value=r):
            with self.assertRaises(backup.TransportError) as e:
                backup.Smb("//nas/share", "u", "bad").test()
        self.assertEqual(e.exception.key, "login")

    def test_names_that_would_break_a_command(self):
        with self.assertRaises(backup.TransportError) as e:
            backup.Smb("//nas/share").listdir('a"; rm x')
        self.assertEqual(e.exception.key, "bad_name")


if __name__ == "__main__":
    unittest.main()


class UsbDrives(unittest.TestCase):
    """Pi OS Lite mounts nothing: USB storage is read from lsblk, mounted or not."""

    LSBLK = json.dumps({"blockdevices": [
        {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "tran": None, "size": 32000000000,
         "children": [{"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part", "fstype": "ext4", "size": 1}]},
        {"name": "sda", "path": "/dev/sda", "type": "disk", "tran": "usb", "size": 16000000000,
         "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "exfat", "label": "STICK",
                       "size": 15990000000, "mountpoint": None}]},
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "tran": "usb", "size": 8000000000, "fstype": "hfsplus"},
    ]})

    def test_only_usb_and_what_is_readable(self):
        found = backup.usb_drives(self.LSBLK)
        self.assertEqual([d["dev"] for d in found], ["/dev/sda1", "/dev/sdb"])
        self.assertTrue(found[0]["usable"])
        self.assertEqual(found[0]["name"], "STICK")
        self.assertFalse(found[1]["usable"])            # a Mac disk: offered a format
        self.assertEqual(found[1]["disk"], "/dev/sdb")


class Sizes(Base):
    """No size limit of its own: the free space decides, before anything is written, and nothing
    that failed or was left out stays quiet or on disk."""

    def test_no_fixed_limits_left(self):
        for name in ("MAX_FILE", "MAX_TOTAL", "MAX_DOWNLOAD"):
            self.assertFalse(hasattr(backup, name), name)

    def test_no_room_is_said_before_anything_is_written(self):
        with mock.patch.object(backup, "_free", return_value=1024):
            with self.assertRaises(backup.BackupError) as e:
                backup.create(self.out, paths=self.paths)
        self.assertEqual(e.exception.key, "no_room")
        self.assertIn("MB", e.exception.he)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_backup_that_fails_its_check_is_deleted(self):
        bad = backup.Check(False, "checksum", "bad", "רע")
        with mock.patch.object(backup, "validate", return_value=bad):
            with self.assertRaises(backup.BackupError):
                backup.create(self.out, paths=self.paths)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_full_disk_while_writing_leaves_nothing(self):
        import errno

        def full(*a, **k):
            raise OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(backup, "write_backup", side_effect=full):
            with self.assertRaises(backup.BackupError) as e:
                backup.create(self.out, paths=self.paths)
        self.assertEqual(e.exception.key, "no_room")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_an_unreadable_file_is_named_on_the_screen(self):
        real_open, steps = os.open, []

        def deny(p, *a, **k):
            if str(p).endswith("history.json"):
                raise PermissionError(13, "Permission denied")
            return real_open(p, *a, **k)
        with mock.patch.object(backup.os, "open", side_effect=deny):
            f = backup.create(self.out, lambda k, en, he: steps.append((k, en, he)), paths=self.paths)
        warn = [s for s in steps if s[0] == "warn_left_out"]
        self.assertEqual(len(warn), 1)
        self.assertIn("history.json", warn[0][1])
        self.assertIn("history.json", warn[0][2])
        with zipfile.ZipFile(f) as z:
            self.assertEqual(json.loads(z.read("manifest.json"))["left_out"], ["data/history.json"])

    def test_the_warning_stays_in_the_job_as_not_done(self):
        job = backup.Job(self.paths, {"id": "1", "action": "create"})
        job.progress("pack", "a", "א")
        backup._warn(job.progress, "left_out", n=1, names="x")
        job.progress("verify", "b", "ב")
        job.finish(True, {"ok": True})
        self.assertEqual([s["state"] for s in job.data["steps"]], ["done", "failed", "done"])

    def test_validate_refuses_what_could_never_fit_and_reads_none_of_it(self):
        f = self.make()
        with mock.patch.object(backup, "_free", return_value=0), mock.patch.object(backup, "_size", return_value=10):
            chk = backup.validate(f, self.paths)
        self.assertFalse(chk.ok)
        self.assertEqual(chk.reason_key, "no_room")
        self.assertTrue(backup.validate(f, self.paths).ok)

    def test_a_large_backup_is_no_longer_refused_by_a_fixed_size(self):
        f = self.make()
        with mock.patch.object(backup, "_free", return_value=1 << 50):
            self.assertTrue(backup.validate(f, self.paths).ok)

    def test_a_snapshot_with_no_room_stops_the_restore_and_leaves_no_files(self):
        f = self.make()
        bk = self.data / "backups"
        bk.mkdir(exist_ok=True)
        for i in range(6):                                  # left by earlier failed restores
            p = bk / f"before-restore-2026-01-0{i + 1}-1200.armonico"
            p.write_text("x")
            os.utime(p, (1000 + i, 1000 + i))
        (bk / ".before-restore-2026-01-07-1200.armonico.part").write_text("x")
        (self.data / "lessons.json").write_text('{"ode": 7}')
        svc = FakeServices()
        real_free = backup._free
        with mock.patch.object(backup, "_free", side_effect=lambda p: 0 if Path(p) == bk else real_free(p)):
            with self.assertRaises(backup.BackupError) as e:
                backup.restore(f, None, self.paths, svc)
        self.assertEqual(e.exception.key, "no_room")
        self.assertEqual(svc.calls, [])
        self.assertEqual((self.data / "lessons.json").read_text(), '{"ode": 7}')
        left = sorted(p.name for p in bk.iterdir() if "before-restore" in p.name)
        self.assertEqual(len(left), backup.KEEP_BEFORE - 1)
        self.assertNotIn(".before-restore-2026-01-07-1200.armonico.part", left)

    def test_download_room_follows_the_free_space(self):
        src = self.data / "backups" / "big.armonico"
        src.parent.mkdir()
        src.write_bytes(b"x" * 4096)
        local = Path(self.tmp.name) / "in.armonico"
        with mock.patch.object(backup, "_free", return_value=backup.RESERVE + 1024):
            with self.assertRaises(backup.BackupError) as e:
                backup.Local(self.paths).get(str(src), str(local))
        self.assertEqual(e.exception.key, "too_large")
        backup.Local(self.paths).get(str(src), str(local))
        self.assertEqual(local.stat().st_size, 4096)


class Broker(Base):
    """A full restore puts the restored logins into the local broker's users file and logs in with them."""

    def setUp(self):
        super().setUp()
        self.mosq = Path(self.tmp.name) / "mosquitto"
        (self.mosq / "conf.d").mkdir(parents=True)
        (self.mosq / "conf.d" / "armonico.conf").write_text("listener 1883 127.0.0.1\n")
        (self.etc / "config.env").write_text(
            "MQTT_HOST='127.0.0.1'\nMQTT_PORT='1883'\nMQTT_USER='piano'\nMQTT_PASS='pw1'\nMQTT_LOCAL='yes'\n"
            "MQTT_REC_USER='piano-rec'\nMQTT_REC_PASS='pw2'\n")
        self.calls = []
        euid = mock.patch.object(backup.os, "geteuid", return_value=1000)   # no mosquitto user to chown to here
        euid.start()
        self.addCleanup(euid.stop)

    def fake_run(self, cmd, **k):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def test_users_file_is_written_and_mosquitto_reloads(self):
        self.assertEqual(backup.sync_broker(self.paths, self.mosq, self.fake_run), "")
        f = self.mosq / "armonico.passwd"
        self.assertEqual(f.read_text(), "piano:pw1\npiano-rec:pw2\n")          # mosquitto_passwd stubbed
        self.assertEqual(f.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.calls[0][:2], ["mosquitto_passwd", "-U"])
        self.assertIn(["systemctl", "reload-or-restart", "mosquitto"], self.calls)
        self.assertFalse([p for p in self.mosq.iterdir() if p.name.startswith(".")])

    def test_external_broker_keeps_the_local_profile_in_step_without_a_reload(self):
        (self.etc / "config.env").write_text("MQTT_HOST='10.0.0.5'\nMQTT_LOCAL='no'\nMQTT_USER='u'\nMQTT_PASS='p'\n")
        (self.etc / "modes" / "standalone.env").write_text(
            "MQTT_HOST='127.0.0.1'\nMQTT_LOCAL='yes'\nMQTT_USER='piano'\nMQTT_PASS='a'\n")
        self.assertEqual(backup.sync_broker(self.paths, self.mosq, self.fake_run), "")
        self.assertEqual((self.mosq / "armonico.passwd").read_text(), "piano:a\n")
        self.assertNotIn(["systemctl", "reload-or-restart", "mosquitto"], self.calls)

    def test_no_local_broker_here_is_said(self):
        (self.mosq / "conf.d" / "armonico.conf").unlink()
        self.assertIn("not set up", backup.sync_broker(self.paths, self.mosq, self.fake_run))

    def test_a_refused_login_is_reported(self):
        tried = []

        def login(h, p, u, pw):
            tried.append(u)
            return "not authorized" if u == "piano-rec" else ""
        fault = backup.broker_check(self.paths, self.mosq, self.fake_run, login, tries=2)
        self.assertEqual(fault, "piano-rec: not authorized")
        self.assertEqual(tried, ["piano", "piano-rec", "piano-rec"])

    def test_restore_says_the_broker_failed_instead_of_everything_came_up(self):
        f = self.make()
        svc = FakeServices()
        svc.broker = lambda: "piano: not authorized"
        steps = []
        r = backup.restore(f, lambda k, en, he: steps.append(k), self.paths, svc)
        self.assertTrue(r["ok"])
        self.assertEqual(r["key"], "restored_broker")
        self.assertIn("not authorized", r["en"])
        self.assertIn("not authorized", r["he"])
        self.assertIn("warn_broker", steps)
        self.assertLess(steps.index("restore_connection"), steps.index("warn_broker"))

    def test_mqtt_login_reads_the_connack(self):
        import socket as sk
        for code, want in ((0, ""), (5, "not authorized"), (4, "wrong user name or password")):
            srv = sk.socket()
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            got = {}

            def serve():
                c, _ = srv.accept()
                got["packet"] = c.recv(256)
                c.sendall(bytes([0x20, 2, 0, code]))
                c.recv(16)
                c.close()
            t = threading.Thread(target=serve)
            t.start()
            self.assertEqual(backup.mqtt_login("127.0.0.1", port, "piano", "pw"), want)
            t.join(5)
            srv.close()
            self.assertEqual(got["packet"][0], 0x10)
            self.assertIn(b"MQTT\x04\xc2", got["packet"])
            self.assertIn(b"piano", got["packet"])
