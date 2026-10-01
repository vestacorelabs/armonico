#!/usr/bin/env python3
"""Looks for Home Assistant, so the setup can fill in the MQTT address by itself.

Home Assistant publishes itself on the network under the mDNS service
'_home-assistant._tcp.local.', and its TXT record carries the version, the
location name and the internal URL, so the address comes from the answer and
no name has to be resolved. The Mosquitto broker app does not publish itself, so
the broker is looked for by opening port 1883 on the address that was found.

Nothing here is required: every failure answers "found no", never an error,
and the setup then asks the questions it always asked. The answer is written
as "key<tab>value" lines on stdout, the same shape install.sh already reads
back from the setup screen, so bash reads it with a plain read loop.

    found\tyes
    host\t192.168.1.10
    where\tnetwork
    version\t2026.9.1
    name\tHome
    broker\tyes

When more than one Home Assistant answers (a test instance beside the real one, two homes on one
network), the first is given as above and each of the others on one line of its own, its fields
apart by tabs, so the setup can let the person choose:

    other\t192.168.1.20\tnetwork\t2026.10.0\tTest\thttp://192.168.1.20:8123\tno
"""

import socket
import sys
import threading
import time

HA_SERVICE = "_home-assistant._tcp.local."
HA_PORT = 8123
MQTT_PORT = 1883
BROWSE_SECONDS = 4.0        # long enough for a busy Pi to answer, short enough not to stall the setup
MORE_SECONDS = 1.5          # after the first answer: how long the others have to answer too
PROBE_SECONDS = 1.5


def flat(v):
    """A tab and a newline cannot appear in a value, so any that arrived in a TXT record are
    dropped; a single quote would break config.env, where the address ends up."""
    return "".join(c for c in str(v) if c not in "\t\r\n'")[:120]


def answer(found, host="", where="", version="", name="", broker=False, url="", others=()):
    """The answer and out: the first one found, then one "other" line for each of the rest."""
    for key, value in (("found", "yes" if found else "no"), ("host", host),
                       ("where", where), ("version", version), ("name", name), ("url", url),
                       ("broker", "yes" if broker else "no")):
        sys.stdout.write(f"{key}\t{flat(value)}\n")
    for o in others:
        fields = (o["host"], o["where"], o["version"], o["name"], o["url"], "yes" if o["broker"] else "no")
        sys.stdout.write("other\t" + "\t".join(flat(f) for f in fields) + "\n")
    sys.exit(0)


def port_open(host, port, timeout=PROBE_SECONDS):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def txt(props, key):
    """A TXT value arrives as bytes; an empty or missing one comes back as ''."""
    value = props.get(key.encode()) or props.get(key)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").strip()
        except Exception:
            return ""
    return (value or "").strip() if isinstance(value, str) else ""


def on_this_machine():
    """Home Assistant in a container on this machine answers on 8123 of localhost."""
    return port_open("127.0.0.1", HA_PORT, 0.7)


def on_the_network():
    """Every instance that answers on mDNS: the search waits for the first, then a moment more for
    the others, so two instances on one network are both offered."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []

    found = []
    arrived = threading.Event()

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if info and info.parsed_addresses():
                found.append(info)
                arrived.set()

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            pass

    zc = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, HA_SERVICE, Listener())
        if arrived.wait(BROWSE_SECONDS):   # the first answer, then a short while for the others
            time.sleep(MORE_SECONDS)
    except Exception:
        return list(found)
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    return list(found)


def described(info):
    host = info.parsed_addresses()[0]
    props = info.properties or {}
    # the address Home Assistant itself publishes is the one to open, with its real port
    url = txt(props, "base_url") or txt(props, "internal_url")
    if not url.startswith(("http://", "https://")):
        url = f"http://{host}:{info.port or HA_PORT}"
    return {"host": host, "where": "network", "version": txt(props, "version"), "url": url,
            "name": txt(props, "location_name"), "broker": port_open(host, MQTT_PORT)}


def main():
    seen = []
    if on_this_machine():
        seen.append({"host": "127.0.0.1", "where": "local", "version": "", "name": "",
                     "url": f"http://127.0.0.1:{HA_PORT}", "broker": port_open("127.0.0.1", MQTT_PORT)})
    for info in on_the_network():
        try:
            d = described(info)
        except (IndexError, AttributeError):
            continue
        # one instance announces itself again on every change, and the local one answers mDNS too
        if any(d["host"] == s["host"] or (d["url"] == s["url"]) for s in seen):
            continue
        if seen and seen[0]["where"] == "local" and d["host"] in local_addresses():
            continue
        seen.append(d)
    if not seen:
        answer(False)
    first, others = seen[0], seen[1:]
    answer(True, **first, others=others)


def local_addresses():
    try:
        return set(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        return set()


if __name__ == "__main__":
    try:
        main()
    except Exception:                             # a failed search is never a failed setup
        answer(False)
