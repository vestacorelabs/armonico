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
    host\t192.168.1.50
    where\tnetwork
    version\t2026.9.1
    name\tHome
    broker\tyes
"""

import socket
import sys
import threading

HA_SERVICE = "_home-assistant._tcp.local."
HA_PORT = 8123
MQTT_PORT = 1883
BROWSE_SECONDS = 4.0        # long enough for a busy Pi to answer, short enough not to stall the setup
PROBE_SECONDS = 1.5


def answer(found, host="", where="", version="", name="", broker=False, url=""):
    """The answer and out. A tab and a newline cannot appear in a value, so any
    that arrived in a TXT record are dropped before the line is written."""
    def flat(v):
        # a single quote would break config.env, where the address ends up
        return "".join(c for c in str(v) if c not in "\t\r\n'")[:120]

    for key, value in (("found", "yes" if found else "no"), ("host", host),
                       ("where", where), ("version", version), ("name", name), ("url", url),
                       ("broker", "yes" if broker else "no")):
        sys.stdout.write(f"{key}\t{flat(value)}\n")
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
    """The first instance that answers on mDNS. A home has one, and the setup
    asks about the address anyway, so the first answer is enough."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return None

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
        arrived.wait(BROWSE_SECONDS)       # returns as soon as one answers
    except Exception:
        return None
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    return found[0] if found else None


def main():
    if on_this_machine():
        answer(True, host="127.0.0.1", where="local", url=f"http://127.0.0.1:{HA_PORT}",
               broker=port_open("127.0.0.1", MQTT_PORT))

    info = on_the_network()
    if info is None:
        answer(False)

    host = info.parsed_addresses()[0]
    props = info.properties or {}
    # the address Home Assistant itself publishes is the one to open, with its real port
    url = txt(props, "base_url") or txt(props, "internal_url")
    if not url.startswith(("http://", "https://")):
        url = f"http://{host}:{info.port or HA_PORT}"
    answer(True, host=host, where="network", version=txt(props, "version"), url=url,
           name=txt(props, "location_name"), broker=port_open(host, MQTT_PORT))


if __name__ == "__main__":
    try:
        main()
    except Exception:                             # a failed search is never a failed setup
        answer(False)
