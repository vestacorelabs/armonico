"""logs.py - what the checks look for in the logs, in one place.

The quick check (fullcheck.py), the deep check (deepcheck.py) and the root unit that reads the logs the device's own
user cannot (root_actions.py logs-read) all read the same things: the kernel's read and write errors, the warnings
of the Bluetooth sound, and the journal's errors as the root unit hands them back. Each of them had its own copy
of these, and the copies could drift apart."""
import re

# the kernel's read and write errors: the SD card, a USB drive, the file system
IO_ERROR = re.compile(r"I/O error|EXT4-fs error|Buffer I/O|blk_update_request|mmc\d+:.*(?:error|timeout)", re.I)
# journalctl and dmesg say this, and answer 0, to a user that may not read the log
NO_ACCESS = re.compile(r"insufficient permissions|not seeing messages|Operation not permitted|Permission denied|"
                       r"No journal files were", re.I)
# a line where the Bluetooth link or its sound fell short
BT_PROBLEM = re.compile(r"(a2dp|bluez|bluetooth|bluetoothd).*(underrun|xrun|timeout|timed out|disconnect|fail|error)", re.I)
# where those lines come from, as journalctl arguments, read over the last 30 minutes
BT_SOURCES = (("-u", "bluetooth.service"), ("_COMM=pipewire",), ("_COMM=wireplumber",))
# the kernel's errors since boot, the first that works
KERNEL_ERR_CMDS = (("dmesg", "--level=err,crit,alert,emerg"),
                   ("journalctl", "-k", "-b", "-p", "err", "-o", "cat", "-q", "--no-pager"))


# On a Pi 3 (and a Pi Zero 2, 3B+ and others) the Wi-Fi chip sits on the second MMC bus, mmc1, over SDIO: its
# start-up and its timeouts ("mmc1: error -110 whilst initialising SDIO card", "brcmfmac_sdio: probe of
# mmc1:0001:1 failed") are the Wi-Fi's, not the SD card's. A line of an MMC bus with no disk on it is left out.
SDIO = re.compile(r"brcmf|SDIO", re.I)
MMC_HOST = re.compile(r"\bmmc(\d+)[:\s]")


def disk_hosts(sysfs="/sys/class/mmc_host"):
    """The MMC buses that have a disk (an SD card or eMMC) on them, as numbers: {0} on a Pi 3. None when the
    system does not say (then no MMC line is left out)."""
    import os
    try:
        hosts = os.listdir(sysfs)
    except OSError:
        return None
    out = set()
    for h in hosts:
        try:
            for card in os.listdir(os.path.join(sysfs, h)):
                if card.startswith(h + ":") and os.path.isdir(os.path.join(sysfs, h, card, "block")):
                    out.add(int(h[3:]))
        except (OSError, ValueError):
            continue
    return out


def io_lines(text, keep=None, width=None, hosts=None):
    """The read and write errors among the kernel's lines; the last keep of them, each cut to width.
    hosts: the MMC buses with a disk (disk_hosts, read here when not given)."""
    hosts = disk_hosts() if hosts is None else hosts
    lines = []
    for x in (text or "").splitlines():
        if not IO_ERROR.search(x):
            continue
        m = MMC_HOST.search(x)
        if SDIO.search(x) or (m and hosts is not None and int(m.group(1)) not in hosts):
            continue                                     # the Wi-Fi's own bus, not a disk
        lines.append(x.strip()[:width] if width else x.strip())
    return lines[-keep:] if keep else lines


def bt_problems(text):
    """How many lines of a log say the Bluetooth link or its sound fell short."""
    return sum(1 for x in (text or "").splitlines() if BT_PROBLEM.search(x))


def journal_answer(j):
    """{unit: [count, last line]} from the root unit's answer, or None when it is not one."""
    if not isinstance(j, dict):
        return None
    out = {}
    for u, v in j.items():
        try:
            out[str(u)] = [int(v[0]), str(v[1])]
        except (ValueError, TypeError, IndexError):
            continue
    return out
