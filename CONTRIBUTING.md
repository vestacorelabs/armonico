# Contributing

## Before writing code

Open an issue first for anything beyond a fix. Armonico is one person's project with a
narrow shape, and a pull request that does not fit it is a waste of the work that went
into it. An issue costs a paragraph and settles that question early.

A bug report is welcome as it is. A security problem does not go in an issue: see
[SECURITY.md](SECURITY.md).

## Running it from a checkout

The tests do not need a keyboard or a broker, but they do import the lesson engine, so its
dependencies have to be there:

    pip install -r requirements.txt
    python3 -m unittest discover -s tests

Everything else needs the real thing: a MIDI keyboard on USB, an MQTT broker, and Debian,
Ubuntu or Raspberry Pi OS. `sudo ./install.sh` sets it all up; `sudo ./install.sh --purge` takes
it back off.

## What the code expects

**Every line a person reads has a Hebrew translation.** The tests enforce this and will
fail without it. Engine strings go in `lessons/piano_lang.py`; screen strings go in the
translation block inside `lessons/piano_game.py`. A translation reads as Hebrew, not as
English word order in Hebrew words.

**Comments say why, not what.** The code is read by whoever has to change it a year later.

**Nothing new goes in front of the door.** Every read and every action on the lesson
screen passes the same check. A new route is behind it unless there is a stated reason.

**Edited wording is sanitized when it is saved, not when it is shown.** Adding a tag to
what the editing tools may keep is a decision about what is allowed to run on every
screen from then on.

## Pull requests

One subject per pull request. Say what breaks and what it was tested against: a keyboard,
a broker, a Raspberry Pi, a fresh install, or none of them, which is also an answer.

Run the tests before pushing. `bash -n` on any shell script that changed.

## Releases

    ./setup/make-release.sh 1.1

That writes the archive and the `SHA256SUMS` beside it. Both go on the GitHub release.
Without the checksum file, installations refuse to update, by design.
