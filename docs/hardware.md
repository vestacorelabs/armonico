# Hardware

## Keyboards

Any **class-compliant USB MIDI** keyboard works, meaning one that Linux, Windows and macOS recognize without a driver. Almost every home digital piano and MIDI controller from the last 15 years is class-compliant. A quick check after plugging it in:

```bash
aconnect -i      # the keyboard appears as a client, for example 20: 'Digital Keyboard'
amidi -l         # and as a raw MIDI device, for example hw:1,0,0
```

The setup measures the keyboard from its lowest and highest keys, so models with 25, 37, 49, 61, 76 and 88 keys all work. Playback and the alarm need a keyboard with its own speakers, such as a digital piano or an arranger keyboard. A controller with no sound of its own still works for shortcuts and lessons: the lesson screen can play the demos in the browser, and it shows every note either way.

### Keyboards with only a 5-pin MIDI port

Older keyboards have a round 5-pin DIN MIDI port instead of USB. There are two ways to connect one:

- **A USB MIDI interface**: a small cable with a USB plug on one end and two DIN plugs on the other. Cheap ones often drop or garble messages, and interfaces from established music brands are the reliable choice.
- **A Raspberry Pi as a USB MIDI gadget.** A Pi Zero, Zero 2 W or Pi 4 can present itself to the host computer as a USB MIDI device, with the keyboard connected to the Pi through a MIDI interface. The host then sees the Pi as the keyboard. On the Pi:

  ```bash
  echo "dtoverlay=dwc2" | sudo tee -a /boot/firmware/config.txt    # /boot/config.txt before Bookworm
  echo -e "dwc2\ng_midi" | sudo tee -a /etc/modules
  sudo reboot
  ```

  After the reboot, the Pi's USB data port connects to the host. On a Pi Zero that is the port marked USB, not PWR. On a Pi 4 it is the USB-C port, which then also powers the Pi, so the host's port has to supply enough current. The host lists a new MIDI client, usually named `f_midi`, and that is the name to pick in the setup.

  The Pi also has to pass the notes between the keyboard and `f_midi`, in both directions. With the names that `aconnect -l` shows on the Pi:

  ```bash
  aconnect '<keyboard>' f_midi
  aconnect f_midi '<keyboard>'
  ```

  These connections are lost at every reboot, so they belong in a small systemd service or in `/etc/rc.local` on the Pi.

## Cables and distance

USB 2.0 is specified for passive cables of up to **5 meters**. A longer passive cable, or a chain of extensions, loses the signal, and the keyboard disconnects at random, often in the middle of a song, when the most data flows.

| Distance | Solution |
|---|---|
| Up to 5 m | A normal USB cable of good quality |
| 5 to 15 m | An **active** USB extension cable (a repeater cable), with a small amplifier in the plug. Some need a power adapter at the far end |
| Over 15 m | A chain of active cables, a powered USB hub at the far end, or a small computer next to the keyboard, such as a Raspberry Pi, running Armonico and reaching the rest over the network |

## Power and USB autosuspend

Linux can suspend idle USB devices to save power, and some keyboards do not wake up properly afterwards. When the keyboard disappears after a quiet period, autosuspend can be turned off for it:

```bash
lsusb                              # the keyboard's ID, for example 0499:1054
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0499", ATTR{idProduct}=="1054", ATTR{power/autosuspend}="-1"' \
  | sudo tee /etc/udev/rules.d/50-piano-autosuspend.rules
sudo udevadm control --reload
```

The rule applies from the next time the keyboard is plugged in.

A keyboard that draws its power over USB, or a Raspberry Pi with a weak power supply, can also drop out under load. A powered USB hub between them solves both.

## Virtual machines

Armonico runs well in a virtual machine, for example on Proxmox, with the keyboard passed through as a USB device. Passing it by vendor and product ID rather than by port keeps it working when it is plugged into another port. Minimal images sometimes lack the ALSA sequencer module, and the installer loads it (`snd-seq`) and keeps it loaded at boot.

## The equipment it was tested on

Armonico 1.0 was tested in the Vesta Core Labs home lab on the equipment below. A model or version that is not listed can work as well: this is what was run, not a limit.

### Keyboard

| Item | Detail |
|---|---|
| Keyboard | Yamaha PSR-E363, 61 keys, class-compliant USB MIDI. USB ID `0499:1710`, listed by ALSA as `Digital Keyboard` |
| Connection | The keyboard's own USB cable and a 5 m active USB extension, about 6 to 7 m in total. A passive 5 m extension in the same place caused frequent disconnects |
| Power | Mains. USB autosuspend turned off for vendor `0499` |

### Machines

| Machine | System | Result |
|---|---|---|
| Virtual machine on Proxmox VE 9.2.21, with all updates, on a Dell PowerEdge R620. The keyboard is passed through by its USB ID | Debian 13, x86_64 | The main installation, in daily use |
| Virtual machine on the same Proxmox | Ubuntu Server 26.04, x86_64 | Works |
| Raspberry Pi 4 | Raspberry Pi OS 64-bit, both the desktop version and Lite | Works |
| Xiaomi Redmi Note 7, 4 GB RAM, rooted with Magisk, with a kernel built for it | A Debian 13 container with systemd under Droidspaces, arm64, on Android 10 | Works, with the limits below |

### Linux on Android

The installer refuses Termux, and that does not change: an app on stock Android has no access to the ALSA MIDI devices and no systemd. What worked is a full Debian container with systemd on a rooted phone, which needs all of these:

- Root, and a kernel that provides the ALSA sequencer and USB MIDI. On the test phone this was a kernel built for it.
- A container that runs systemd. Droidspaces was used, since every part of Armonico is a systemd service.
- The keyboard's sound devices (`/dev/snd`) passed into the container. On the test phone they were passed one by one over ADB, not with a blanket hardware access mode, which can take devices away from Android.
- A USB OTG cable. The PSR-E363 worked on a single plain cable, USB-C to USB-A, with no charger on the phone.

The limits that showed up:

- **Memory.** The phone's 4 GB is shared with Android and its apps, and the container was capped at 1800 MB. That is enough for Armonico, but Android competes for the same memory.
- **Android's own processes.** Android keeps running and managing its own processes around the container, and they compete with it for the CPU and memory. Playback and key detection lag at times because of it.
- **Start at boot.** On the test phone the container was started by hand, not at boot, so the alarm works only while it runs.

For daily use, a virtual machine, a mini PC or a Raspberry Pi is the steadier choice. The phone is a working option for those who already have one rooted.

### Home Assistant and Telegram

| Item | Detail |
|---|---|
| Home Assistant | Home Assistant OS in a virtual machine on the same Proxmox, version 2026.9.4 |
| Broker | The Mosquitto broker app 7.1.1 in Home Assistant, which does not enforce topic rules (see [SECURITY.md](../SECURITY.md)) |
| Telegram | The Telegram bot integration of Home Assistant |

### Lesson screen

| Device | System | Browser |
|---|---|---|
| Samsung Galaxy S25+ | Android 16 | Chrome |
| iPad 10.2" | iOS 26.5.2 | Chrome |
| Laptop | Windows 11 | Chrome |

Chrome is the browser the screen was tested in, and the recommended one. The screen only shows the lesson and sends commands, and no USB device goes through the browser, so other current browsers are expected to work too.
