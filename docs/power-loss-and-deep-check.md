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

It measures for 30 seconds. What it measures: per service (the Armonico units, mosquitto, PipeWire, WirePlumber, Bluetooth, NetworkManager) whether it runs, its restarts since boot, its memory now, its disk writes per hour, and its CPU twice: the average since boot (from systemd's accounting) and over the 30 seconds. It also covers:

- **System resources:** CPU busy and idle in the 30 seconds, the load average against the number of cores, the CPU clock now and at most, the core voltage and the GPU memory (`vcgencmd` on a Raspberry Pi), the time spent waiting for the disk, the 3 busiest programs in the 30 seconds and the 3 largest, zombie processes, memory and swap, the temperature, and throttling and under-voltage now and since boot
- **System:** the OS and kernel, and whether the clock is synced over NTP
- **Boot:** each stage, the Armonico units, and the slowest others
- **Audio:** dropouts since boot, the default output, and PipeWire's buffer (samples per cycle and rate)
- **Network and radios:** each network device in words (cable or Wi-Fi, connected or not), the Wi-Fi and Bluetooth radios, the Wi-Fi signal
- **Storage:** the system partition and the data folder's partition, disk writes per hour since boot and in the 30 seconds, the SD card's own wear report where the card gives one, and the size of the data folder
- **Logs:** errors of the last 24 hours per unit, with the last line
- **History:** what the sampler kept (below)

Next to each number that is not plain, a short line says what it means and when it is a problem. What a machine does not have is skipped.

On the device, the result is a scrollable list, with ▼ ▲ or by dragging: a summary row (each number with a line of context, the temperature with its peak in the history), the services table with a second line under each column title saying what it measures, then the groups. The top line says how long it measured and how long the device has been up. The check button on the result copies the report to a USB stick.

### History sampler

`armonico-sampler.timer` runs `device/deepcheck.py sample` every 5 minutes at the lowest priority. Readings are kept in `/run` (RAM) and rolled into one line per hour in `checks/history.json`, 7 days kept, so the report can show averages, peaks and busy hours. The timer is installed by `install.sh` for general and device installs.
