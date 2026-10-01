# Security

## Reporting a problem

Report a security problem privately, not as a public issue.

Use GitHub's private reporting on this repository: **Security → Report a vulnerability**.
That opens a channel only the maintainer can see.

A first answer usually comes within a week. A confirmed problem is fixed, released and
credited to whoever reported it, unless they ask to stay unnamed.

Nothing here is a bug bounty. There is no payment, and there is no legal threat either:
a report made in good faith, without reaching into anyone else's installation, is welcome.

## What this project is

Armonico runs on one machine on a home network, next to a MIDI keyboard. It is not
built to face the internet, and nothing here should be read as a claim that it is.
A port forward to it from a router is outside what this design defends against.

## Known limits

These are choices of the design, not oversights.

- **The screen is plain HTTP.** The pages, the code and the pass cookie cross the home
  network unencrypted. Someone who can listen on that network can see them. For anything
  wider, put a reverse proxy with TLS in front (see `LESSON_HOSTS`).
- **The status page gets the broker login.** A signed-in screen is handed the broker
  address and the `piano` login (`/api/status-login`) so it can talk to the broker. That
  login is limited to the `piano/` topics, but any device that has the screen code can
  read it. The login is kept in memory only, never in the address and never on the device.
- **Turning the screen code off** opens everything to every device on the network (see
  below).
- **The admin secret travels in MQTT commands** that need it, so someone who can read the
  `piano/` topics while such a command is sent can reuse it. The recorder does not write it.

## What it defends

**The lesson screen.** Every read and every action needs a pass. A device enters the
six-digit code once, keeps a signed pass for 14 days, and that pass is renewed on every
visit. The code is shown by `armonico code`, by `/pianocode` in Telegram, and at the end
of the install. "Sign every screen out" in the settings replaces the key the passes are
signed with, which ends every pass that was ever handed out.

Asking for the code can be turned off. Turning it off means every device on the network
can start lessons, play the keyboard, read the practice history, download a full backup
and ask for a system update. The setting says so where it is turned off.

**The commands that arrive over MQTT.** Three of them hand out the screen code, open the
editing tools, or change whose progress is being recorded. Those three answer only to a
request carrying the secret written in `/etc/armonico/config.env`. This is deliberate and
not belt-and-braces: the broker cannot be relied on to say who may publish what. Home
Assistant's Mosquitto add-on, in every version of series 7, does not enforce topic rules
at all, because the plugin it authenticates with answers the permission question before
the broker's own rules are ever consulted.

**The broker, when this project installed it.** Two logins with separate jobs. `piano`
reads and writes only under `piano/`. `piano-rec`, which the recorder uses, reads
everything and writes nothing. The broker stays on the machine unless the install was told
another machine needs it. On a broker this project did not install, none of this applies,
and the rules there belong to whoever owns it.

**The setup screen.** The installer runs it as the `armonico` user and not as root, even
though the installer itself needs root. Nothing the screen does asks for more: the keyboard
comes from the `audio` group, the broker test is network only, and its answer goes into a
folder of its own that belongs to that user. The installer reads that answer and writes
`config.env` itself, so the settings file has one author. The screen is reachable only by
address or host name, asks for a six-digit code, locks after five wrong answers, and closes
after fifteen minutes or once it has saved.

**Updates.** A release is checked against the `SHA256SUMS` published with it before
anything in it runs, and a release with no published checksum is not installed.

**Edited wording.** The screen's editing tools take HTML, because laying the page out is
what they are for. What is saved keeps structure and styling and loses anything that can
execute: script and style blocks, event handlers, and addresses that are really code.

## Known limits

- The screen speaks plain HTTP. A machine on a home network has no name a certificate can
  be issued for, and a self-signed certificate teaches people to click through warnings.
  The code and the pass travel in the clear on the local network.
- Topic rules cannot be enforced on a broker this project did not install, and in the
  Home Assistant add-on they cannot be enforced at all today.
- The recording of MQTT traffic, when it is turned on, writes everything that crosses the
  broker to files on this machine. It is off unless it is turned on.
