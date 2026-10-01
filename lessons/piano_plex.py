"""piano_plex.py - the TV card: a Plex library picked from a list, played on the TV through Home Assistant.

Off unless the flag file is there (the engine's "plexfun" command, PlexFun10 / PlexFun10Off), and only with
Home Assistant: an external broker (allowed). With the local broker nothing could turn the TV on.

Connecting needs no token to be found by hand: "Sign in with Plex" asks plex.tv for a short code, the code
is entered at plex.tv/link (or the link is opened, or its QR scanned), and the token and the server come
back by themselves (link_start). Typing the address and a token stays as the other way.

The engine plays the items itself (Player): for each one it finds what to play (a series: the episode Plex
has under Continue Watching, or the first one, the specials left out), sends one MQTT message on tv/plex,
"open|N|URI", and waits on the server's sessions until it has started and then ended. Home Assistant only
does what the TV needs: an automation from home-assistant/private/tv_plex.yaml, which setup/ha_setup.py
("plex") puts in by itself with the Home Assistant token, as it does the rest of the piano's automations.

  on                 the TV goes on
  open|N|URI         the TV opens the item (N above 1: Back first, then 2 seconds)
  pause / resume     pause and go on
  stop / off         stop the playing / turn the TV off
  idle               the TV has stayed paused for an hour: the automation's notification, if it has one

A key is the item's ratingKey on the Plex server. The screens never show it; they show names.

Kept in plex.json in the data folder: the server's address, its token (never sent back to a screen), the
server's id, this piano's client id for plex.tv, and the reward: what plays after a good lesson.
"""
import json
import os
import uuid
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

TOPIC = "tv/plex"
TIMEOUT = 6
LEAF = ("movie", "episode", "clip", "video")     # what plays as it is
MAX_ITEMS = 300                                   # a section bigger than this is cut, by title
MAX_QUEUE = 8
REWARD_MIN = (60, 70, 80, 90, 100)
PLEX_TV = "https://plex.tv"
LINK_SECONDS = 900                                # a code from plex.tv lasts 15 minutes
START_WAIT, START_POLL, END_POLL, IDLE_AFTER = 120, 5, 10, 3600

_LOCK = threading.Lock()


class PlexError(Exception):
    """Why the server could not be read, in words for the screen: "address", "token", "net", "server"."""


def flag(data_dir):
    return Path(data_dir) / "plex-fun"


def is_on(data_dir):
    return flag(data_dir).exists()


def set_on(data_dir, on):
    f = flag(data_dir)
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    else:
        f.unlink(missing_ok=True)


def _file(data_dir):
    return Path(data_dir) / "plex.json"


def load(data_dir):
    try:
        got = json.loads(_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    if not isinstance(got, dict):
        got = {}
    r = got.get("reward") if isinstance(got.get("reward"), dict) else {}
    return {"url": str(got.get("url") or ""), "token": str(got.get("token") or ""),
            "server_id": str(got.get("server_id") or ""), "client": str(got.get("client") or ""),
            "reward": {"on": bool(r.get("on")), "items": _items(r.get("items")), "off": bool(r.get("off", True)),
                       "min": r.get("min") if r.get("min") in REWARD_MIN else 80, "daily": bool(r.get("daily", True)),
                       "last": str(r.get("last") or "")}}


def save(data_dir, cfg):
    f = _file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    with _LOCK:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)                       # the token is a password to the whole library
        os.replace(tmp, f)


def allowed(mqtt_host):
    """The card needs Home Assistant: an external broker. With the local one (this machine only) no
    automation could hear tv/plex, so the card is not offered at all."""
    return (mqtt_host or "").strip().lower() not in ("", "localhost", "127.0.0.1", "::1")


def public(data_dir):
    """What a screen may see: everything but the token."""
    cfg = load(data_dir)
    return {"on": is_on(data_dir), "url": cfg["url"], "token": bool(cfg["token"]), "reward": cfg["reward"],
            "link": link_state()}


def _items(raw):
    """A list of picked items, each {"key": "123", "title": "..."}; anything else is dropped."""
    out = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict) and str(it.get("key", "")).isdigit():
            out.append({"key": str(it["key"]), "title": str(it.get("title") or "")[:120]})
    return out[:MAX_QUEUE]


def norm_url(url):
    """'192.168.1.5' and 'nas.local:32400' become http://...:32400; an https address stays as it is."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise PlexError("address")
    if p.port is None and p.scheme == "http":
        url = f"{p.scheme}://{p.hostname}:32400{p.path or ''}"
    return url.rstrip("/")


def _get(url, token, path, method="GET", client="armonico"):
    req = Request(url + path, method=method, headers={"Accept": "application/json", "X-Plex-Token": token,
                                                      "X-Plex-Product": "Armonico", "X-Plex-Client-Identifier": client})
    try:
        with urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(4_000_000) or b"{}")
    except HTTPError as e:
        raise PlexError("token" if e.code in (401, 403) else "server") from e
    except (URLError, OSError, TimeoutError) as e:
        raise PlexError("net") from e
    except ValueError as e:
        raise PlexError("server") from e


def check(url, token):
    """The server answers with this token: its name. PlexError otherwise."""
    if not url:
        raise PlexError("address")
    if not token:
        raise PlexError("token")
    got = _get(url, token, "/")
    mc = got.get("MediaContainer") or {}
    _get(url, token, "/library/sections")          # / answers some things without a token; this does not
    return mc.get("friendlyName") or urlparse(url).hostname


def _row(m):
    kind = m.get("type", "")
    sub = ""
    if kind == "episode":
        s, e = m.get("parentIndex"), m.get("index")
        sub = (f"S{s}·E{e}" if s is not None and e is not None else "") + (f" · {m['grandparentTitle']}" if m.get("grandparentTitle") else "")
    elif kind == "season":
        sub = m.get("parentTitle") or ""
    elif kind in ("movie", "show") and m.get("year"):
        sub = str(m["year"])
    if m.get("duration"):
        sub = (sub + " · " if sub else "") + f"{round(int(m['duration']) / 60000)} min"
    return {"key": str(m.get("ratingKey", "")), "title": m.get("title") or "", "type": kind, "sub": sub,
            "leaf": kind in LEAF, "pick": kind in LEAF or kind == "show", "open": kind in ("show", "season")}


def browse(data_dir, key=""):
    """The libraries (key ""), a library's items ("s:<id>"), or what is under a series or a season (its key).
    Each row: key, title, type, sub, pick (can be chosen), open (has more under it)."""
    cfg = load(data_dir)
    url, token = cfg["url"], cfg["token"]
    if not url or not token:
        raise PlexError("address" if not url else "token")
    if not key:
        mc = _get(url, token, "/library/sections").get("MediaContainer") or {}
        return [{"key": "s:" + str(d.get("key")), "title": d.get("title") or "", "type": "section",
                 "sub": d.get("type") or "", "pick": False, "open": True} for d in mc.get("Directory") or []]
    if key.startswith("s:"):
        sid = key[2:]
        if not sid.isdigit():
            raise PlexError("server")
        mc = _get(url, token, f"/library/sections/{sid}/all").get("MediaContainer") or {}
    elif key.isdigit():
        mc = _get(url, token, f"/library/metadata/{quote(key)}/children").get("MediaContainer") or {}
    else:
        raise PlexError("server")
    rows = [_row(m) for m in (mc.get("Metadata") or [])[:MAX_ITEMS]]
    return [r for r in rows if r["key"].isdigit()]


# ---- finding the server on the network, as the setup finds Home Assistant
GDM = ("239.0.0.250", 32414)          # Plex's own discovery (GDM): a multicast M-SEARCH, each server answers "200 OK"
MDNS_SERVICE = "_plexmediasvr._tcp.local."    # the older Bonjour name, which still crosses an mDNS repeater
FIND_SECONDS = 2.5


def _gdm():
    found = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("B", 2))
        sock.sendto(b"M-SEARCH * HTTP/1.0\r\n\r\n", GDM)
        end = time.monotonic() + FIND_SECONDS
        while time.monotonic() < end:
            try:
                raw, (host, _) = sock.recvfrom(2048)
            except socket.timeout:
                continue
            lines = raw.decode("utf-8", "replace").splitlines()
            if not lines or "200 OK" not in lines[0]:
                continue
            d = {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ":" in ln)}
            if d.get("Content-Type", "plex/media-server") != "plex/media-server":
                continue
            found.append({"name": d.get("Name") or host, "url": f"http://{host}:{d.get('Port') or 32400}"})
    except OSError:
        pass
    finally:
        sock.close()
    return found


def _mdns():
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []
    found = []

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info and info.parsed_addresses():
                found.append({"name": name.split(".")[0], "url": f"http://{info.parsed_addresses()[0]}:{info.port or 32400}"})

        def update_service(self, zc, type_, name):
            pass

        def remove_service(self, zc, type_, name):
            pass
    zc = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, MDNS_SERVICE, Listener())
        time.sleep(FIND_SECONDS)
    except Exception:
        pass
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    return found


def discover():
    """The Plex servers that answer on this network, by GDM and by mDNS at once (a few seconds):
    [{"name", "url"}], one per address. Nothing found is an empty list, never an error."""
    out = {}
    res = {}
    ts = [threading.Thread(target=lambda k=k, f=f: res.__setitem__(k, f()), daemon=True) for k, f in (("gdm", _gdm), ("mdns", _mdns))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(FIND_SECONDS + 2.5)
    for item in res.get("gdm", []) + res.get("mdns", []):
        out.setdefault(item["url"], item)
    return list(out.values())


# ---- signing in with Plex: a short code instead of a token found by hand
_LINK = {"state": "", "code": "", "url": "", "server": "", "gen": 0}


def _client(data_dir):
    """This piano's id at plex.tv, made once and kept: plex.tv lists it under the account's devices."""
    cfg = load(data_dir)
    if not cfg["client"]:
        cfg["client"] = "armonico-" + uuid.uuid4().hex[:16]
        save(data_dir, cfg)
    return cfg["client"]


def link_state():
    """{"state": "" | "waiting" | "done" | "expired" | "failed" | "noserver", "code", "url", "server"}."""
    with _LOCK:
        return {k: _LINK[k] for k in ("state", "code", "url", "server")}


def link_start(data_dir, tv=PLEX_TV, poll=2.0):
    """Asks plex.tv for a code and waits for it in the background: the code is entered at plex.tv/link,
    or the link (url) is opened. When it is, the token comes back, the server is found and both are kept."""
    client = _client(data_dir)
    pin = _get(tv, "", "/api/v2/pins?strong=false", "POST", client)
    pid, code = pin.get("id"), str(pin.get("code") or "")
    if not pid or not code:
        raise PlexError("server")
    # short enough for the device's QR code (up to about 100 characters)
    url = f"https://app.plex.tv/auth#?clientID={quote(client)}&code={quote(code)}"
    with _LOCK:
        _LINK.update(state="waiting", code=code, url=url, server="", gen=_LINK["gen"] + 1)
        gen = _LINK["gen"]
    threading.Thread(target=_link_wait, args=(data_dir, tv, client, pid, gen, poll), daemon=True,
                     name="plex-link").start()
    return link_state()


def _link_set(gen, **kw):
    with _LOCK:
        if _LINK["gen"] == gen:
            _LINK.update(**kw)


def _link_wait(data_dir, tv, client, pid, gen, poll):
    end = time.time() + LINK_SECONDS
    while time.time() < end:
        if _LINK["gen"] != gen:
            return                                  # a newer code was asked for
        try:
            token = str(_get(tv, "", f"/api/v2/pins/{pid}", "GET", client).get("authToken") or "")
        except PlexError:
            token = ""
        if token:
            try:
                url, token, name, sid = find_server(tv, token, client)
            except PlexError:
                _link_set(gen, state="noserver")
                return
            cfg = load(data_dir)
            cfg.update(url=url, token=token, server_id=sid)
            save(data_dir, cfg)
            _link_set(gen, state="done", server=name)
            return
        time.sleep(poll)
    _link_set(gen, state="expired")


def find_server(tv, token, client="armonico"):
    """(url, token, name, id) of the account's own server that answers from here, with the token it took: plex.tv's list of its
    addresses, this network's first, then whatever GDM and mDNS find. PlexError("server") when none does."""
    try:
        res = _get(tv, token, "/api/v2/resources?includeHttps=1&includeRelay=0", "GET", client)
    except PlexError:
        res = []
    tries = []
    for r in res if isinstance(res, list) else []:
        if "server" not in str(r.get("provides", "")) or r.get("owned") is False:
            continue
        conns = sorted(r.get("connections") or [], key=lambda c: (not c.get("local"), bool(c.get("relay"))))
        for c in conns:
            if c.get("uri") and not c.get("relay"):
                tries.append((str(c["uri"]).rstrip("/"), r.get("accessToken") or token, r.get("name") or "",
                              str(r.get("clientIdentifier") or "")))
    for f in discover():
        tries.append((f["url"], token, f.get("name") or "", ""))
    for url, tok, name, sid in tries:
        try:
            got = check(url, tok)
        except PlexError:
            continue
        if not sid:
            try:
                sid = str((_get(url, tok, "/identity").get("MediaContainer") or {}).get("machineIdentifier") or "")
            except PlexError:
                sid = ""
        return url, tok, name or got, sid
    raise PlexError("server")


# ---- the playing: the engine goes through the items, Home Assistant only works the TV
def parse(msg):
    """("play|K|K|off") to (keys, off); anything else ([], False)."""
    parts = [p.strip() for p in str(msg or "").split("|") if p.strip()]
    if not parts or parts[0].lower() != "play":
        return [], False
    return [p for p in parts[1:] if p.isdigit()][:MAX_QUEUE], parts[-1].lower() == "off"


def uri(server_id, key):
    """What the Plex app on the TV opens for one item."""
    return ("plex://watch/video?uri=server%3A%2F%2F" + quote(server_id, safe="")
            + "%2Fcom.plexapp.plugins.library%2Flibrary%2Fmetadata%2F" + quote(str(key), safe=""))


def resolve(url, token, key):
    """The key that plays for a picked item: itself, or for a series the episode under Continue Watching,
    or else its first episode with the specials (season 0) left out."""
    mc = _get(url, token, f"/library/metadata/{quote(key)}?includeOnDeck=1").get("MediaContainer") or {}
    meta = (mc.get("Metadata") or [{}])[0]
    if meta.get("type") != "show":
        return key
    deck = ((meta.get("OnDeck") or {}).get("Metadata") or [{}])[0].get("ratingKey")
    if deck:
        return str(deck)
    leaves = (_get(url, token, f"/library/metadata/{quote(key)}/allLeaves").get("MediaContainer") or {}).get("Metadata") or []
    first = next((str(m.get("ratingKey")) for m in leaves if m.get("parentIndex") not in (0, "0") and m.get("ratingKey")), "")
    return first or key


def sessions(url, token):
    """{ratingKey: "playing" | "paused" | ...} of what the server is playing now."""
    mc = _get(url, token, "/status/sessions").get("MediaContainer") or {}
    return {str(m.get("ratingKey")): str((m.get("Player") or {}).get("state") or "") for m in mc.get("Metadata") or []}


class Player:
    """One run at a time: a new one or stop() ends the one before. pub(topic, text) sends on MQTT."""
    def __init__(self):
        self.gen = 0
        self.lock = threading.Lock()

    def stop(self):
        with self.lock:
            self.gen += 1

    def play(self, data_dir, keys, off, pub, sleep=time.sleep, clock=time.time):
        keys = [str(k) for k in keys if str(k).isdigit()][:MAX_QUEUE]
        if not keys:
            return False
        with self.lock:
            self.gen += 1
            gen = self.gen
        threading.Thread(target=self._run, args=(data_dir, keys, off, pub, gen, sleep, clock), daemon=True,
                         name="plex-play").start()
        return True

    def _run(self, data_dir, keys, off, pub, gen, sleep, clock):
        live = lambda: self.gen == gen
        cfg = load(data_dir)
        url, token, sid = cfg["url"], cfg["token"], cfg["server_id"]
        try:
            if not sid:
                sid = str((_get(url, token, "/identity").get("MediaContainer") or {}).get("machineIdentifier") or "")
                if sid:
                    cfg["server_id"] = sid
                    save(data_dir, cfg)
            for n, key in enumerate(keys, 1):
                if not live():
                    return
                play = resolve(url, token, key)
                pub(TOPIC, f"open|{n}|{uri(sid, play)}")
                waited = 0
                while live() and waited < START_WAIT and play not in sessions(url, token):
                    sleep(START_POLL)
                    waited += START_POLL
                paused_at, told = None, False
                while live():
                    sleep(END_POLL)
                    now = sessions(url, token)
                    if play not in now:
                        break
                    if now[play] == "paused":
                        paused_at = paused_at or clock()
                        if not told and clock() - paused_at > IDLE_AFTER:
                            pub(TOPIC, "idle")
                            told = True
                    else:
                        paused_at, told = None, False
            if off and live():
                pub(TOPIC, "off")
        except PlexError:
            return                                  # the server went away: the TV stays as it is


PLAYER = Player()


def message(keys, off):
    """The reward kept as one line, play|KEY|KEY[|off]: the engine reads it back with parse() and plays it."""
    keys = [str(k) for k in keys if str(k).isdigit()][:MAX_QUEUE]
    if not keys:
        return ""
    return "play|" + "|".join(keys) + ("|off" if off else "")


def reward_due(data_dir, score, stopped, secs, today=None):
    """After a lesson: the message to send, or "" when no reward is due. A good lesson is one that was not
    stopped, ran at least a minute, and scored at least the minimum (a lesson with no score counts)."""
    if not is_on(data_dir):
        return ""
    cfg = load(data_dir)
    r = cfg["reward"]
    if not r["on"] or not r["items"] or stopped or secs < 60:
        return ""
    if score is not None and score < r["min"]:
        return ""
    today = today or time.strftime("%Y-%m-%d")
    if r["daily"] and r["last"] == today:
        return ""
    msg = message([it["key"] for it in r["items"]], r["off"])
    if msg:
        cfg["reward"]["last"] = today
        save(data_dir, cfg)
    return msg
