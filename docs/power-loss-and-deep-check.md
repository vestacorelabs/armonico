# After a power loss, and the deep check

## Recovery after a power loss (Armonico Pi)

The device keeps a "running" marker, written at every boot and removed by a clean shutdown or reboot (`armonico-boot-mark.service`). If the next boot finds the marker, the power went out (or the system crashed), and the device shows a recovery screen before the normal one. A service restarted by hand is not taken for a power loss.

The screen shows what was open last (screen, lesson, profile, time), then works through these steps by itself, each as a line that is waiting, running or finished:

1. File system: what the boot's file system check did (`fsck.repair=yes` is added to the kernel command line by the installer when it is missing).
2. Settings files: each one is read; a broken one is restored from its last good copy. This is done at boot by `armonico-boot-mark.service`, before any service reads the files, and the screen shows what was restored.
3. Learning data and profiles: the same.
4. An interrupted screen recording: the parts are put together into a file if they can be, otherwise removed.
5. Songs: every MIDI file is checked; a damaged one is moved to `songs/_damaged/`.
6. Services: each one is started and checked.
7. Sound and keys: a short self test.

Skipping is offered after steps 1 and 2. At the end the device goes on to the normal screen, or stays and says what could not be repaired. An alarm that was ringing when the power went does not ring again by itself.

Files (under the data folder, `device/`): `running.json`, `unclean.json`, `last.json` (written when the screen, lesson or profile changes, at most every 5 seconds, and otherwise once every 5 minutes; fsync'd at most every 2 minutes), and `last-good/` (copies of the settings, refreshed every 10 minutes while they are valid).

## Deep check

A measurement of the whole system, on the device (Settings, Device test, Deep check) and on any Linux machine:

```
armonico check --deep
```

It prints the report and saves it as text and JSON in `checks/` in the data folder (the 10 newest are kept). `armonico check` is unchanged. The exit code is 1 when a problem was found.

What it measures: per service (the Armonico units, mosquitto, PipeWire, WirePlumber, Bluetooth, NetworkManager) the state, restarts, memory and CPU. CPU is shown as the average over the whole uptime (from systemd's accounting) and over a 30 second live window. It also covers CPU load and idle, memory, temperature, throttling and under-voltage (`vcgencmd` on a Raspberry Pi), disk use and write rate, boot time per stage and the slowest units, audio dropouts, network and radios, and journal errors of the last 24 hours per unit. What a machine does not have is skipped.

On the device, the result is a scrollable list (a summary row, a services table, then the groups). The check button on the result copies the report to a USB stick.

### History sampler

`armonico-sampler.timer` runs `device/deepcheck.py sample` every 5 minutes at the lowest priority. Readings are kept in `/run` (RAM) and rolled into one line per hour in `checks/history.json`, 7 days kept, so the report can show averages, peaks and busy hours. The timer is installed by `install.sh` for general and device installs.
