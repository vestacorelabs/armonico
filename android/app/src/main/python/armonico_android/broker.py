"""broker.py - a small MQTT 3.1.1 broker inside the app, for a phone with no broker on the network.

The same broker the Linux install sets up with Mosquitto when there is no Home Assistant:
port 1883 answers the phone itself (the bridge, the lesson engine and the recorder), and
port 9001 answers the network over websockets, for the status screen in a browser. Two logins,
as there: "piano" acts and may touch piano/# only, "piano-rec" reads every topic and writes none.
Only the Python standard library, so nothing has to be built for Android.

What it does: logins (user and password), rules per login (read, write, readwrite, as
Mosquitto's acl_file), topics with + and # wildcards, retained messages (kept in a file, so they
survive a restart), last wills, QoS 0, 1 and 2 from publishers (delivered at most at QoS 1),
keepalive, one connection per client id (a second one replaces the first), and MQTT over
websockets (RFC 6455, subprotocol "mqtt"). What it does not: persistent sessions (every session
starts clean), QoS 2 delivery to subscribers, MQTT 5 and TLS. paho and mqtt.js work with that.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import threading
import time

MAX_PACKET = 1 << 20              # 1 MB: the bridge never sends more than a log line or a status


def topic_matches(flt, topic):
    """MQTT topic filter matching. A wildcard at the first level never matches a topic that
    starts with $ (the broker's own topics), as the standard says."""
    if topic.startswith("$") and flt[:1] in ("+", "#"):
        return False
    f = flt.split("/")
    t = topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)


def valid_filter(flt):
    if not flt:
        return False
    parts = flt.split("/")
    for i, p in enumerate(parts):
        if "#" in p and (p != "#" or i != len(parts) - 1):
            return False
        if "+" in p and p != "+":
            return False
    return True


def _str(b):
    return len(b).to_bytes(2, "big") + b


def _packet(ptype, flags, body):
    n = len(body)
    out = bytearray([(ptype << 4) | flags])
    while True:
        d = n % 128
        n //= 128
        out.append(d | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + body


class _Client:
    def __init__(self, broker, reader, writer):
        self.broker = broker
        self.reader = reader
        self.writer = writer
        self.cid = None
        self.subs = {}               # filter -> granted qos
        self.will = None             # (topic, payload, qos, retain)
        self.keepalive = 0
        self.next_id = 1
        self.alive = True
        self.inflight_qos2 = set()   # packet ids of QoS 2 publishes waiting for PUBREL
        self.peer = writer.get_extra_info("peername")
        self.user = None

    def send(self, data):
        if self.alive:
            try:
                self.writer.write(data)
            except Exception:
                self.alive = False

    def deliver(self, topic, payload, qos, retain):
        flags = (qos << 1) | (1 if retain else 0)
        body = _str(topic.encode())
        if qos:
            pid = self.next_id
            self.next_id = self.next_id % 65535 + 1
            body += pid.to_bytes(2, "big")
        self.send(_packet(3, flags, body + payload))

    async def read_packet(self):
        h = await self.reader.readexactly(1)
        mult, n = 1, 0
        for _ in range(4):
            b = (await self.reader.readexactly(1))[0]
            n += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        else:
            raise ValueError("bad length")
        if n > MAX_PACKET:
            raise ValueError("packet too large")
        body = await self.reader.readexactly(n) if n else b""
        return h[0] >> 4, h[0] & 0x0F, body


WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _WsReader:
    """The bytes of the binary frames of a websocket, read as one stream, so the MQTT code
    reads them as it reads a socket. Pings are answered, a close frame ends the stream."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.buf = bytearray()

    async def _frame(self):
        h = await self.reader.readexactly(2)
        op, masked, n = h[0] & 0x0F, h[1] & 0x80, h[1] & 0x7F
        if n == 126:
            n = int.from_bytes(await self.reader.readexactly(2), "big")
        elif n == 127:
            n = int.from_bytes(await self.reader.readexactly(8), "big")
        if n > MAX_PACKET + 16:
            raise ValueError("frame too large")
        key = await self.reader.readexactly(4) if masked else b""
        data = bytearray(await self.reader.readexactly(n)) if n else bytearray()
        if key:
            for i in range(len(data)):
                data[i] ^= key[i % 4]
        return op, bytes(data)

    async def readexactly(self, n):
        while len(self.buf) < n:
            op, data = await self._frame()
            if op in (0, 1, 2):
                self.buf += data
            elif op == 8:                                  # close
                try:
                    self.writer.write(bytes([0x88, 0]))
                except Exception:
                    pass
                raise asyncio.IncompleteReadError(bytes(self.buf), n)
            elif op == 9:                                  # ping: the same payload back
                self.writer.write(bytes([0x8A, len(data)]) + data if len(data) < 126 else b"")
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out


class _WsWriter:
    """Every write goes out as one binary frame. A server's frames are not masked."""

    def __init__(self, writer):
        self.raw = writer

    def write(self, data):
        n = len(data)
        if n < 126:
            head = bytes([0x82, n])
        elif n < 65536:
            head = bytes([0x82, 126]) + n.to_bytes(2, "big")
        else:
            head = bytes([0x82, 127]) + n.to_bytes(8, "big")
        self.raw.write(head + data)

    async def drain(self):
        await self.raw.drain()

    def close(self):
        try:
            self.raw.write(bytes([0x88, 0]))
        except Exception:
            pass
        self.raw.close()

    def get_extra_info(self, name, default=None):
        return self.raw.get_extra_info(name, default)


def acl_allows(rules, topic, want):
    """Mosquitto's acl_file for one login: rules are (filter, "read" | "write" | "readwrite").
    No rules at all (None) allows everything; an empty list allows nothing."""
    if rules is None:
        return True
    for flt, access in rules:
        if (access == "readwrite" or access == want) and topic_matches(flt, topic):
            return True
    return False


class Broker:
    def __init__(self, host="127.0.0.1", port=1883, users=None, retained_file=None, log=print,
                 acl=None, ws_host="0.0.0.0", ws_port=None):
        self.host = host
        self.port = port
        self.users = dict(users or {})
        self.acl = acl                 # user -> [(filter, access)], or None for no rules
        self.ws_host = ws_host
        self.ws_port = ws_port         # websockets for a browser, or None for none
        self.ws_server = None
        self.ws_error = None
        self.retained_file = retained_file
        self.log = log
        self.retained = {}           # topic -> (payload bytes, qos)
        self.clients = {}            # client id -> _Client
        self.loop = None
        self.server = None
        self.thread = None
        self.ready = threading.Event()
        self.error = None
        self._save_pending = False
        self._load()

    # ---------------------------------------------------------------- retained, kept on disk
    def _load(self):
        if not self.retained_file:
            return
        try:
            with open(self.retained_file, encoding="utf-8") as f:
                for t, (p, q) in json.load(f).items():
                    self.retained[t] = (base64.b64decode(p), q)
        except Exception:
            pass

    def _save_soon(self):
        if not self.retained_file or self._save_pending:
            return
        self._save_pending = True
        self.loop.call_later(2, self._save)

    def _save(self):
        self._save_pending = False
        try:
            tmp = self.retained_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({t: [base64.b64encode(p).decode(), q] for t, (p, q) in self.retained.items()}, f)
            os.replace(tmp, self.retained_file)
        except Exception as e:
            self.log(f"⚠️ Broker: the retained messages were not saved: {e}")

    # ---------------------------------------------------------------- life cycle
    def start(self, timeout=5):
        self.thread = threading.Thread(target=self._run, daemon=True, name="armonico-broker")
        self.thread.start()
        self.ready.wait(timeout)
        if self.error:
            raise self.error
        return self

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.server = self.loop.run_until_complete(
                asyncio.start_server(self._handle, self.host, self.port, reuse_address=True))
        except Exception as e:
            self.error = e
            self.ready.set()
            return
        self.log(f"✅ Built-in broker listening on port {self.port} of this phone")
        if self.ws_port:
            try:
                self.ws_server = self.loop.run_until_complete(
                    asyncio.start_server(self._handle_ws, self.ws_host, self.ws_port, reuse_address=True))
                self.log(f"✅ Built-in broker: websockets on port {self.ws_port}, for the status screen")
            except Exception as e:
                self.ws_error = e
                self.log(f"⚠️ Built-in broker: websockets did not start on port {self.ws_port}: {e}")
        self.ready.set()
        self.loop.run_forever()
        if self.retained_file:
            self._save()

    def stop(self):
        if not self.loop or not self.loop.is_running():
            return

        def _close():
            self.server.close()
            if self.ws_server is not None:
                self.ws_server.close()
            for c in list(self.clients.values()):
                c.alive = False
                try:
                    c.writer.close()
                except Exception:
                    pass
            for t in asyncio.all_tasks(self.loop):
                t.cancel()
            self.loop.call_later(0.2, self.loop.stop)
        self.loop.call_soon_threadsafe(_close)
        self.thread.join(3)

    # ---------------------------------------------------------------- routing
    def rules(self, user):
        if self.acl is None:
            return None
        return self.acl.get(user, [])

    def publish(self, topic, payload, qos, retain, sender=None):
        if retain:
            if payload:
                self.retained[topic] = (payload, qos)
            else:
                self.retained.pop(topic, None)
            self._save_soon()
        for c in list(self.clients.values()):
            if not acl_allows(self.rules(c.user), topic, "read"):
                continue
            best = -1
            for flt, q in c.subs.items():
                if topic_matches(flt, topic):
                    best = max(best, q)
            if best >= 0:
                # a retained message forwarded live goes out with the retain flag off (MQTT 3.1.1, 3.3.1.3)
                c.deliver(topic, payload, min(qos, best, 1), False)

    # ---------------------------------------------------------------- one connection
    async def _handle_ws(self, reader, writer):
        """The HTTP upgrade of a websocket, then the same MQTT session as on 1883."""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except Exception:
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        hdr = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            hdr[k.strip().lower()] = v.strip()
        key = hdr.get("sec-websocket-key")
        if not lines[0].startswith("GET ") or "websocket" not in hdr.get("upgrade", "").lower() or not key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            writer.close()
            return
        accept = base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
        reply = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n")
        protos = [p.strip() for p in hdr.get("sec-websocket-protocol", "").split(",") if p.strip()]
        if protos:
            reply += f"Sec-WebSocket-Protocol: {'mqtt' if 'mqtt' in protos else protos[0]}\r\n"
        writer.write((reply + "\r\n").encode())
        await writer.drain()
        await self._handle(_WsReader(reader, writer), _WsWriter(writer))

    async def _handle(self, reader, writer):
        c = _Client(self, reader, writer)
        clean = False
        try:
            ptype, flags, body = await asyncio.wait_for(c.read_packet(), 10)
            if ptype != 1 or not self._connect(c, body):
                return
            while c.alive:
                timeout = c.keepalive * 1.5 if c.keepalive else None
                ptype, flags, body = await asyncio.wait_for(c.read_packet(), timeout)
                if ptype == 14:                         # DISCONNECT: a clean close, no will
                    clean = True
                    break
                self._packet(c, ptype, flags, body)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError, OSError):
            pass
        finally:
            c.alive = False
            if c.cid is not None and self.clients.get(c.cid) is c:
                del self.clients[c.cid]
                if not clean and c.will:
                    t, p, q, r = c.will
                    if acl_allows(self.rules(c.user), t, "write"):
                        self.publish(t, p, q, r)
            try:
                writer.close()
            except Exception:
                pass

    def _connect(self, c, body):
        try:
            i = 0
            n = int.from_bytes(body[i:i + 2], "big"); i += 2
            proto = body[i:i + n]; i += n
            level = body[i]; i += 1
            cflags = body[i]; i += 1
            c.keepalive = int.from_bytes(body[i:i + 2], "big"); i += 2
            if proto not in (b"MQTT", b"MQIsdp") or level not in (3, 4):
                c.send(_packet(2, 0, bytes([0, 1])))   # unacceptable protocol version
                return False
            n = int.from_bytes(body[i:i + 2], "big"); i += 2
            cid = body[i:i + n].decode("utf-8", "replace"); i += n
            will = None
            if cflags & 0x04:
                n = int.from_bytes(body[i:i + 2], "big"); i += 2
                wt = body[i:i + n].decode("utf-8", "replace"); i += n
                n = int.from_bytes(body[i:i + 2], "big"); i += 2
                wp = body[i:i + n]; i += n
                will = (wt, wp, min((cflags >> 3) & 3, 1), bool(cflags & 0x20))
            user = pw = None
            if cflags & 0x80:
                n = int.from_bytes(body[i:i + 2], "big"); i += 2
                user = body[i:i + n].decode("utf-8", "replace"); i += n
            if cflags & 0x40:
                n = int.from_bytes(body[i:i + 2], "big"); i += 2
                pw = body[i:i + n].decode("utf-8", "replace"); i += n
        except Exception:
            return False
        if self.users and (user not in self.users or pw is None
                           or not hmac.compare_digest(self.users[user].encode(), pw.encode())):
            c.send(_packet(2, 0, bytes([0, 4])))       # bad user name or password
            self.log(f"🚫 Broker: login refused for {user or 'anonymous'} from {c.peer[0] if c.peer else '?'}")
            return False
        if not cid:
            cid = f"auto-{time.monotonic_ns()}"
        old = self.clients.get(cid)
        if old is not None:                            # the same client again: the old link goes
            old.alive = False
            try:
                old.writer.close()
            except Exception:
                pass
        c.cid = cid
        c.user = user
        c.will = will
        self.clients[cid] = c
        c.send(_packet(2, 0, bytes([0, 0])))
        return True

    def _packet(self, c, ptype, flags, body):
        if ptype == 3:                                 # PUBLISH
            qos = (flags >> 1) & 3
            retain = bool(flags & 1)
            n = int.from_bytes(body[:2], "big")
            topic = body[2:2 + n].decode("utf-8", "replace")
            i = 2 + n
            pid = None
            if qos:
                pid = int.from_bytes(body[i:i + 2], "big")
                i += 2
            payload = body[i:]
            if "+" in topic or "#" in topic or not topic:
                c.alive = False                        # a protocol error: close
                return
            if qos == 2:
                c.send(_packet(5, 0, pid.to_bytes(2, "big")))      # PUBREC
                if pid in c.inflight_qos2:
                    return                                         # a resent copy
                c.inflight_qos2.add(pid)
            elif qos == 1:
                c.send(_packet(4, 0, pid.to_bytes(2, "big")))      # PUBACK
            if not acl_allows(self.rules(c.user), topic, "write"):
                return                       # as Mosquitto: acknowledged, and dropped
            self.publish(topic, payload, min(qos, 1), retain, c)
        elif ptype == 6:                               # PUBREL
            pid = int.from_bytes(body[:2], "big")
            c.inflight_qos2.discard(pid)
            c.send(_packet(7, 0, body[:2]))            # PUBCOMP
        elif ptype == 8:                               # SUBSCRIBE
            pid = body[:2]
            i = 2
            granted = []
            new = []
            while i < len(body):
                n = int.from_bytes(body[i:i + 2], "big")
                flt = body[i + 2:i + 2 + n].decode("utf-8", "replace")
                q = body[i + 2 + n] & 3
                i += 3 + n
                if valid_filter(flt):
                    g = min(q, 1)
                    c.subs[flt] = g
                    granted.append(g)
                    new.append((flt, g))
                else:
                    granted.append(0x80)
            c.send(_packet(9, 0, pid + bytes(granted)))
            for flt, g in new:                         # the retained messages the new filter covers
                for t, (p, q) in list(self.retained.items()):
                    if topic_matches(flt, t) and acl_allows(self.rules(c.user), t, "read"):
                        c.deliver(t, p, min(q, g), True)
        elif ptype == 10:                              # UNSUBSCRIBE
            pid = body[:2]
            i = 2
            while i < len(body):
                n = int.from_bytes(body[i:i + 2], "big")
                c.subs.pop(body[i + 2:i + 2 + n].decode("utf-8", "replace"), None)
                i += 2 + n
            c.send(_packet(11, 0, pid))
        elif ptype == 12:                              # PINGREQ
            c.send(_packet(13, 0, b""))
        elif ptype in (4, 5, 7):                       # acks of what was sent: nothing is resent
            pass
        else:
            c.alive = False
