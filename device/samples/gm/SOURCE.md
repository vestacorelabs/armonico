# Other voices and drums of the device

Fluid (R3) SoundFont by Frank Wen, Copyright (c) 2000-2002, 2008 Frank Wen.
Released under the MIT License (the full text is in NOTICE). The file used is
FluidR3_GM.sf2 as packaged in Debian and Ubuntu (fluid-soundfont-gm 3.1).

One voice for each General MIDI family apart from the piano, which is the
Salamander Grand (../SOURCE.md), and the General MIDI drum kit:

| Folder | GM program | Voice |
|---|---|---|
| 01-vibraphone | 12 | Vibraphone |
| 02-organ | 17 | Drawbar Organ |
| 03-guitar | 25 | Acoustic Guitar (nylon) |
| 04-bass | 34 | Electric Bass (finger) |
| 05-violin | 41 | Violin |
| 06-strings | 49 | String Ensemble 1 |
| 07-trumpet | 57 | Trumpet |
| 08-sax | 66 | Alto Sax |
| 09-flute | 74 | Flute |
| 10-lead | 81 | Lead 1 (square) |
| 11-pad | 90 | Pad 2 (warm) |
| 12-atmosphere | 100 | FX 4 (atmosphere) |
| 13-kalimba | 109 | Kalimba |
| 14-steeldrums | 115 | Steel Drums |
| 15-birds | 124 | Bird Tweet |
| drums | channel 10 | Standard kit, notes 35 to 81 |

How they were made: each note played alone through FluidSynth 2.3.4 at 48 kHz,
velocity 100, with reverb and chorus off. Melodic voices every fourth semitone
from A0 (21), held 1.8 seconds (struck and plucked voices) or 3.6 seconds
(held voices), with 0.6 seconds of release. Notes the soundfont has no sample
for (too high for the bass, the violin, the strings and the saxophone) are left
out. Silence before the note cut, a 0.4 second fade at the end, mixed to mono,
each voice leveled so its loudest note peaks at -12 dBFS (the drums at -10),
stored as 16 bit FLAC.

The device turns these into WAV once ("armonico_pi.py prepare") and makes the
notes in between by resampling, the first time each one is played.
