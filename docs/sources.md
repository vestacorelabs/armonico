# Song archives

The song search (on the lesson screen, `/getmidi` in Telegram and `armonico getmidi` in the terminal) looks only in archives whose files may legally be downloaded and kept. Each downloaded song gets a `<song>.credit.json` next to it with the archive, the license, the author and the source page, and the lesson screen shows it on the song's card. Creative Commons Attribution licenses require exactly that: the author and the source travel with the file.

| Archive | In the search | License of the files | Notes |
|---|---|---|---|
| [Mutopia Project](https://www.mutopiaproject.org/) | Yes | Public domain, or Creative Commons Attribution / Attribution-ShareAlike, stated per piece | "All music on Mutopia may be freely downloaded, printed, copied, distributed, modified, performed and recorded." Its robots.txt allows automated access. Pieces published only as a zip of several movements are skipped |
| [Wikimedia Commons](https://commons.wikimedia.org/) | Yes | Always a free license or public domain, stated per file | Commons accepts only free content. Requests identify the program by name, as Wikimedia asks |
| [piano-midi.de](http://www.piano-midi.de/) | No | Creative Commons Attribution-ShareAlike 3.0 Germany | The files are free to use with credit to Bernd Krueger and piano-midi.de, but the site refuses automated requests. Files can be downloaded in a browser and added with "Add a song" |
| BitMidi | No | Unknown | Its files come from a collection of about 100,000 MIDI files posted on Reddit, with no license and no copyright policy. Used by earlier versions of this project, removed |
| mfiles | No | Personal use only, no redistribution | |
| Kunst der Fuge | No | Personal use only | |
| [IMSLP](https://imslp.org/) | No | Varies per file, and not stated in a fixed field | Checked again in September 2026 against the two rules above, and it fails both. Its robots.txt disallows `/images/`, `/imglnks/`, `/wiki/File:` and `/index.php` for every user agent but one named bot, and those are the paths the files sit on. A download still passes through a copyright confirmation page meant for a person, which a program can only get past by sending an `imslpdisclaimeraccepted` cookie of its own. Files can be downloaded in a browser and added with "Add a song" |

Songs added with "Add a song" are the user's own files, under the user's own responsibility.

## The songs included in the installer

The six pieces in `setup/music/` are excerpts of Mutopia Project editions that are all in the public domain. See [setup/music/SOURCES.md](../setup/music/SOURCES.md).

## The sounds of the lesson screen

The lesson screen can play the notes on the device it is open on, in one of 5 voices chosen in ⚙.

| Voice | Where it comes from |
|---|---|
| Grand piano | Recordings of a Yamaha C5: the [Salamander Grand Piano V3](https://archive.org/details/SalamanderGrandPianoV3) by Alexander Holm, under [Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/). The 30 MP3 files in `lessons/sounds/piano/` are the subset prepared by the [Tone.js](https://github.com/Tonejs/audio) project, used unchanged; the credit is in `SOURCE.md` next to them and on the lesson screen's About page |
| Electric piano, organ, music box, soft synth | Built in the browser with the Web Audio API. No recordings |

The recordings are served by the lesson engine itself, so the grand piano works with no internet.
