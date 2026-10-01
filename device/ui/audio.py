"""audio.py - the sound output, through PipeWire (the sound server of Raspberry Pi OS's desktop).

Every program on the device plays to PipeWire's default output: the keys, the rest video, the
test tone. Choosing an output only changes that default, and PipeWire moves what is playing at once.
"both" is the armonico_both output of device/pipewire/80-armonico.conf (AUX and HDMI, never a USB
device); "off" mutes the default.

HDMI off: the device runs on its own DSI screen only, the BIGTREETECH PiTFT50 the case is built
for. Every HDMI part below (the HDMI output, "both", the HDMI profile) is kept as comments, marked
"HDMI off (DSI only)", and can be put back by removing them. A USB sound device (headphones, a sound card, a speaker) is the
setting "usb:<its PipeWire name>", a Bluetooth one (headphones, a speaker: A2DP through BlueZ) "bt:<its
PipeWire name>", and any other set of outputs together is "mix": a combine sink
made with pactl (module-combine-sink of pipewire-pulse) from the names in the setting sound_mix.
The screen runs as the same user as PipeWire, so no root is needed.
"""
import json
import subprocess
from pathlib import Path

# OUTPUTS = ("aux", "hdmi", "both", "off")    # HDMI off (DSI only)
OUTPUTS = ("aux", "off")
MIX = "armonico_mix"        # the combine sink of the "mix" setting
USB = "usb:"                # the setting of one USB sound device: "usb:" and its PipeWire node name
BT = "bt:"                  # the same for a Bluetooth sound device (node bluez_output.AA_BB_CC_DD_EE_FF.1)
HDMI_LEVEL = "1.0"          # full: at 0.7 the speakers on the screen's jack were far too quiet
DRM = Path("/sys/class/drm")


def _run(*cmd, timeout=5):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


_SEEN = {"sig": None, "found": []}


def _signature():
    """The outputs in a few bytes: the id and name of each, from pactl's short list. Far cheaper than pw-dump
    (every object of the graph as JSON), so sinks() asks pw-dump only when this changed. None when unreadable."""
    try:
        r = _run("pactl", "list", "short", "sinks", timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return tuple(tuple(line.split("\t")[:2]) for line in (r.stdout or "").splitlines() if line.strip())


def sinks(dump=None):
    """PipeWire's outputs: [{id, name, card, desc}]. dump is pw-dump's JSON text, for tests.
    The same outputs as the last look (by pactl's short list): the last answer again, without pw-dump."""
    if dump is None:
        sig = _signature()
        if sig is not None and sig == _SEEN["sig"]:
            return [dict(s) for s in _SEEN["found"]]
        try:
            dump = _run("pw-dump").stdout
        except (OSError, subprocess.SubprocessError):
            return []
        out = _parse_sinks(dump)
        if sig is not None and out:                 # an empty answer (PipeWire restarting) is never kept
            _SEEN.update(sig=sig, found=[dict(s) for s in out])
        return out
    return _parse_sinks(dump)


def _parse_sinks(dump):
    try:
        objs = json.loads(dump or "[]")
    except ValueError:
        return []
    out = []
    for o in objs:
        props = ((o.get("info") or {}).get("props") or {})
        if o.get("type") != "PipeWire:Interface:Node" or props.get("media.class") != "Audio/Sink":
            continue
        s = {"id": o.get("id"), "name": props.get("node.name", ""),
             "card": props.get("alsa.card_name") or props.get("api.alsa.card.name") or "",
             "desc": props.get("node.description") or props.get("node.nick") or ""}
        if props.get("api.bluez5.codec") or props.get("api.bluez5.profile"):
            # a Bluetooth output: the codec it plays with (SBC, SBC-XQ, AAC ...) and its profile (a2dp-sink for music,
            # headset-head-unit for calls: mono and narrow), shown on the Output page
            s["codec"] = str(props.get("api.bluez5.codec") or "").replace("_", "-").upper()
            s["bt_profile"] = str(props.get("api.bluez5.profile") or "")
        out.append(s)
    return out


def bt_quality(found):
    """{key: (codec, call)} of the Bluetooth outputs there now: the codec in capitals, and whether it is on a
    headset (call) profile, which sounds far worse than music (A2DP)."""
    return {BT + s["name"]: (s.get("codec", ""), s.get("bt_profile", "").startswith(("headset", "hfp", "hsp")))
            for s in found if str(s.get("name", "")).startswith("bluez_output.")}


def hdmi_port():
    """0 or 1: the HDMI port a screen is on (HDMI-A-1 is port 0, beside the power socket)."""
    def on(n):
        for c in DRM.glob(f"card*-HDMI-A-{n}"):
            try:
                if (c / "status").read_text().strip() == "connected":
                    return True
            except OSError:
                pass
        return False
    return 1 if on(2) and not on(1) else 0


def hdmi_connected():
    """Whether a screen is connected to either HDMI port: the Pi's HDMI sound needs one.
    HDMI off (DSI only): always False, so no HDMI output is offered or chosen anywhere."""
    # HDMI off (DSI only):
    # for c in DRM.glob("card*-HDMI-A-*"):
    #     try:
    #         if (c / "status").read_text().strip() == "connected":
    #             return True
    #     except OSError:
    #         pass
    return False


def pick(out, found=None, port=None):
    """The output's PipeWire id for a setting, or None when PipeWire has no such output (yet)."""
    found = sinks() if found is None else found
    if out.startswith(USB):
        return next((s["id"] for s in found if s["name"] == out[len(USB):]), None)
    if out.startswith(BT):
        return next((s["id"] for s in found if s["name"] == out[len(BT):]), None)
    if out == "mix":
        return next((s["id"] for s in found if s["name"] == MIX), None)
    # HDMI off (DSI only):
    # if out == "both":
    #     return next((s["id"] for s in found if s["name"] == "armonico_both"), None)
    # if out == "hdmi":
    #     port = hdmi_port() if port is None else port
    #     hdmi = [s for s in found if "hdmi" in (s["card"] + s["name"]).lower()]
    #     mine = [s for s in hdmi if s["card"].lower().endswith(f"hdmi-{port}") or f"hdmi{port}" in s["card"].lower()]
    #     return (mine or hdmi or [{"id": None}])[0]["id"]
    if out in ("both", "hdmi"):
        return None
    # aux, and off (muted on the jack)
    return next((s["id"] for s in found if not is_usb(s) and not is_bt(s)
                 and ("headphones" in s["card"].lower() or "bcm2835" in s["name"].lower())
                 and "hdmi" not in (s["card"] + s["name"]).lower()), None)    # HDMI off (DSI only)


def is_usb(s):
    """A sink of a USB sound device: ALSA names its nodes after the bus they are on."""
    return s["name"].startswith("alsa_output.usb-")


def is_bt(s):
    """A sink of a Bluetooth device: WirePlumber names its nodes bluez_output.<address>.<n>."""
    return s["name"].startswith("bluez_output.")


def is_ext(key):
    """A key of a device that comes and goes: a USB sound device or a Bluetooth one."""
    return isinstance(key, str) and key.startswith((USB, BT))


def bt_mac(key):
    """The address inside a Bluetooth output's key or node name (AA:BB:CC:DD:EE:FF), or None."""
    import re
    m = re.search(r"bluez_output\.((?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2})", key or "")
    return m.group(1).replace("_", ":").upper() if m else None


def usb_dev(key):
    """A USB output's device, without the profile at the end of its node name: the same headphones are
    "...H340-00.analog-stereo" on one profile and "...H340-00.iec958-stereo" on another."""
    if key.startswith(BT):
        key = key[len(BT):]                   # bluez_output.<address>.<n>: the device is the address
        return key.rsplit(".", 1)[0] if key.startswith("bluez_output.") else key
    name = key[len(USB):] if key.startswith(USB) else key
    return name.rsplit(".", 1)[0] if name.startswith("alsa_output.usb-") else name


def usb_label(s):
    """A USB device's name as the screen shows it: PipeWire's description without the profile's words."""
    d = (s.get("desc") or s.get("card") or s["name"]).strip()
    for tail in (" Analog Stereo", " Digital Stereo (IEC958)", " Digital Stereo", " Mono", " Stereo"):
        if d.endswith(tail) and len(d) > len(tail):
            d = d[: -len(tail)]
            break
    return d[:40]


def keys_now(found=None, hdmi=None):
    """The outputs there now, as setting keys with their names: [(key, label)]. AUX first, then HDMI
    while a screen is on it, then each USB sound device."""
    # AUX is always there, and HDMI whenever a screen is on it: their outputs may be missing from
    # PipeWire for a moment (just after boot, or the HDMI profile still off), which apply() puts right
    found = sinks() if found is None else found
    hdmi = hdmi_connected() if hdmi is None else hdmi
    # out = [("aux", "AUX")] + ([("hdmi", "HDMI")] if hdmi else [])    # HDMI off (DSI only)
    out = [("aux", "AUX")]
    out += [(USB + s["name"], usb_label(s)) for s in found if is_usb(s)]
    out += [(BT + s["name"], usb_label(s)) for s in found if is_bt(s)]
    return out


def bt_a2dp(text=None):
    """A Bluetooth device left on its headset profile (mono, with the microphone) is set back to its best A2DP
    profile. Nothing here asks for the headset profile (bluetooth.autoswitch-to-headset-profile is off), but
    WirePlumber gives a device back the profile it had last, set by another program or an older version, and on it
    the device plays a phone-call sound or nothing the mix can use. text: pactl list cards, for tests. Returns the
    cards that were set."""
    import re
    if text is None:
        try:
            text = _run("pactl", "list", "cards", timeout=5).stdout or ""
        except (OSError, subprocess.SubprocessError):
            return []
    done = []
    for block in re.split(r"\n(?=Card #)", text):
        name = re.search(r"^\s*Name:\s*(bluez_card\.\S+)", block, re.M)
        act = re.search(r"^\s*Active Profile:\s*(\S+)", block, re.M)
        if not name or not act or not act.group(1).startswith(("headset", "handsfree", "off")):
            continue
        best = None
        for m in re.finditer(r"^\s*(a2dp[-_]sink\S*):.*?priority:\s*(\d+).*?available:\s*(\w+)", block, re.M):
            if m.group(3) != "no" and (best is None or int(m.group(2)) > best[1]):
                best = (m.group(1), int(m.group(2)))
        if best:
            try:
                _run("pactl", "set-card-profile", name.group(1), best[0], timeout=5)
                done.append(name.group(1))
            except (OSError, subprocess.SubprocessError):
                pass
    return done


def ext_ids(found=None):
    """The node id of each USB and Bluetooth output there now, by its setting key: {key: id}. A device that went
    and came back has the same key and a new id."""
    found = sinks() if found is None else found
    return {(BT if is_bt(s) else USB) + s["name"]: s["id"] for s in found if is_bt(s) or is_usb(s)}


def node_name(key, found, port=None):
    """The PipeWire node name behind a single output's key, or None when it is not there."""
    sid = pick(key, found, port)
    return next((s["name"] for s in found if s["id"] == sid), None) if sid is not None else None


def mix_unload():
    """Takes away the combine sink of an earlier "mix", if pactl made one."""
    try:
        r = _run("pactl", "list", "short", "modules")
    except (OSError, subprocess.SubprocessError):
        return
    for line in (r.stdout or "").splitlines():
        f = line.split("\t")
        if len(f) >= 3 and f[1] == "module-combine-sink" and f"sink_name={MIX}" in f[2]:
            try:
                _run("pactl", "unload-module", f[0])
            except (OSError, subprocess.SubprocessError):
                pass


def mix_load(names):
    """A combine sink named MIX over these PipeWire sinks. True when pactl took it."""
    mix_unload()
    try:
        r = _run("pactl", "load-module", "module-combine-sink", f"sink_name={MIX}", "sinks=" + ",".join(names))
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def hdmi_profile_on(dump=None, port=None):
    """Turns on the stereo profile of the HDMI port a screen is on, when PipeWire left it off.

    The Pi's HDMI sound exists only while its screen is lit. A screen that was dark when PipeWire
    looked at the card (at boot, or lit later) leaves that card on the "off" profile, and then there is
    no HDMI output at all: the sound stayed on AUX whatever was chosen. True when a profile was set."""
    if dump is None:
        try:
            dump = _run("pw-dump").stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        objs = json.loads(dump or "[]")
    except ValueError:
        return False
    port = hdmi_port() if port is None else port
    done = False
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Device":
            continue
        info = o.get("info") or {}
        props = info.get("props") or {}
        card = (props.get("api.alsa.card.name") or props.get("alsa.card_name") or "").lower()
        if "hdmi" not in card or not (card.endswith(f"hdmi-{port}") or f"hdmi{port}" in card):
            continue
        profiles = (info.get("params") or {}).get("EnumProfile") or []
        now = ((info.get("params") or {}).get("Profile") or [{}])[0].get("name", "")
        if now.startswith("output:"):
            continue
        good = [p for p in profiles if str(p.get("name", "")).startswith("output:hdmi-stereo")] or \
               [p for p in profiles if str(p.get("name", "")).startswith("output:")]
        if good:
            try:
                _run("wpctl", "set-profile", str(o.get("id")), str(good[0].get("index")))
                done = True
            except (OSError, subprocess.SubprocessError):
                pass
    return done


def valid(out, mix=()):
    return out in OUTPUTS or (out.startswith(USB) and len(out) > len(USB)) or \
        (out.startswith(BT) and len(out) > len(BT)) or (out == "mix" and len(mix) >= 2)


def apply(out, mix=()):
    """Makes the setting PipeWire's default output. mix: the keys of a "mix". (ok, message)."""
    import time
    if not valid(out, mix):
        return False, "snd.fail"
    # HDMI off (DSI only):
    # if (out in ("hdmi", "both") or (out == "mix" and "hdmi" in mix)) and hdmi_profile_on():
    #     time.sleep(1.0)                              # the HDMI output appears a moment after its profile
    clock = None                                     # the Pi's output a Bluetooth device alone plays beside (bt_clock)
    if out == "mix":
        found = sinks()
        names = [node_name(k, found) for k in mix]
        if None in names:
            return False, "snd.none"
        if not mix_load(names):
            return False, "snd.fail"
        sid = _wait_mix()
    else:
        clock = bt_clock(out) if out.startswith(BT) else None
        sid = None
        if clock is not None:
            sid = _wait_mix()
            if sid is None:
                clock = None
        if sid is None:
            sid = pick(out)
    if sid is None:
        return False, "snd.none"
    try:
        r = _run("wpctl", "set-default", str(sid))
        if r.returncode:
            return False, "snd.fail"
        _run("wpctl", "set-mute", str(sid), "1" if out == "off" else "0")
        if out != "mix" and clock is None:
            mix_unload()                             # an earlier mix is not left playing to nobody
        # every built-in output at full level: the device's own volume sets what is heard. WirePlumber
        # keeps a card's last level, and an old 40% left the small speakers faint and muffled. The harsh
        # sound on HDMI was its buffer running dry, not the level (80-armonico-wireplumber.conf). A USB
        # device keeps its own level: headphones at full could be far too loud in the ears.
        for s in sinks():
            if is_usb(s) or is_bt(s) or s["name"] == MIX:
                continue
            hdmi = "hdmi" in (s["card"] + s["name"]).lower()
            if hdmi:
                continue                             # HDMI off (DSI only): its output is left alone
            # _run("wpctl", "set-volume", str(s["id"]), HDMI_LEVEL if hdmi else "1.0")    # HDMI off (DSI only)
            _run("wpctl", "set-volume", str(s["id"]), "1.0")
            if out != "off" and s["id"] != sid:
                _run("wpctl", "set-mute", str(s["id"]), "0")     # muted as such a clock before: heard again
        if clock is not None:
            # the clock for the Bluetooth device, silent: the mix's own stream into it is muted, never the output
            # itself. With the AUX output muted the Bluetooth device in the same mix played nothing either, and
            # Bluetooth had sound only with AUX chosen beside it.
            _mute_streams_on(clock["name"])
        if out.startswith(BT) or (out == "mix" and any(k.startswith(BT) for k in mix)):
            for s in sinks():
                if is_bt(s):
                    _run("wpctl", "set-mute", str(s["id"]), "0")  # a Bluetooth output left muted from before
        # the AUX jack's own mixer back at 0 dB: its top is +4 dB, where it distorts. The WirePlumber
        # rule keeps PipeWire off that mixer; this puts it right if anything moved it anyway.
        _run("amixer", "-c", "Headphones", "-q", "sset", "PCM", "0dB")
    except (OSError, subprocess.SubprocessError):
        return False, "snd.fail"
    return True, ""


def _mute_streams_on(sink_name, tries=10, step=0.1):
    """Mutes every stream that plays straight into this output: while it is the clock of a Bluetooth device alone,
    everything plays to the mix, so those are the mix's own streams. They show up a moment after the mix: looked for
    at most 10 times in 1 s. True when one was muted."""
    import time
    for _ in range(tries):
        try:
            idx = {f[1]: f[0] for f in (line.split("\t") for line in
                   (_run("pactl", "list", "short", "sinks", timeout=3).stdout or "").splitlines()) if len(f) >= 2}
            want = idx.get(sink_name)
            ins = [f[0] for f in (line.split("\t") for line in
                   (_run("pactl", "list", "short", "sink-inputs", timeout=3).stdout or "").splitlines())
                   if len(f) >= 2 and want is not None and f[1] == want]
        except (OSError, subprocess.SubprocessError):
            return False
        if ins:
            for i in ins:
                try:
                    _run("pactl", "set-sink-input-mute", i, "1")
                except (OSError, subprocess.SubprocessError):
                    pass
            return True
        time.sleep(step)
    return False


def _wait_mix(tries=10, step=0.2):
    """The combine sink's id: it shows up a moment after pactl made it. Looked for at most 10 times in 2 s, each
    look the short pactl list (sinks() asks pw-dump only once it is there)."""
    import time
    for i in range(tries):
        sid = pick("mix")
        if sid is not None or i == tries - 1:
            return sid
        time.sleep(step)
    return None


def bt_clock(out):
    """A Bluetooth device alone: played through a mix with one of the Pi's own outputs (AUX, else HDMI), which is
    kept silent (apply). The sink, or None when there is none and the device plays alone.

    On the device a Bluetooth output alone stayed silent (the keys, the video, everything), while the same device
    in a mix with AUX or HDMI played: alone it has to drive PipeWire's graph with its own clock, and in a mix it
    follows the Pi's sound card, which the device has always run on. So it always plays in a mix, the Pi's output
    silent beside it: the mix's stream into it is muted (apply), the output itself stays open and unmuted."""
    found = sinks()
    bt = node_name(out, found)
    cid = pick("aux", found)
    # HDMI off (DSI only):
    # if cid is None:
    #     cid = pick("hdmi", found)
    clock = next((s for s in found if s["id"] == cid), None) if cid is not None else None
    if not bt or clock is None or not mix_load([bt, clock["name"]]):
        return None
    return clock


def default_id():
    """PipeWire's default output now, as its id, or None."""
    try:
        r = _run("wpctl", "inspect", "@DEFAULT_AUDIO_SINK@")
    except (OSError, subprocess.SubprocessError):
        return None
    first = (r.stdout or "").split(",", 1)[0].split()
    return first[1] if r.returncode == 0 and len(first) == 2 and first[0] == "id" else None


