"""The anime theme's evening and night, learned from the one pose Gemini drew at all three hours (ui/anime.py).

    python3 -I armonico/device/tools/relight_fields.py

Pose 7 has a day, an evening and a night picture of the same room from the same place; the other poses (8 to 11,
and pose 8 drawn frame by frame) have a day picture only. Tinting them with one color left the sunlit window and
the sunbeam in the evening and at night, and they did not look like pose 7 beside them. Here the change from
day to evening and to night is learned from pose 7, piece by piece over the picture: each of 32 x 18 pieces has its
own gain and offset for red, green and blue (night = gain x day + offset), fitted on that piece of the two
pictures and smoothed into its neighbours. The window becomes the night sky there, the bed takes the lamp's
light, the wall goes blue, as in pose 7. The device then relights any picture of the room with these, in numpy,
once per size (ui/anime.py Pics.relit).

Writes ui/data/anime/relight.npz (a few kilobytes). Needs numpy, scipy and Pillow on the machine that runs it.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

DATA = Path(__file__).resolve().parent.parent / "ui" / "data" / "anime"
GX, GY = 32, 18                 # pieces across and down
PULL = 0.05                     # a piece with little contrast keeps close to its plain brightness ratio
SMOOTH = 1.2                    # in pieces: no edge between neighbouring pieces


def load(name):
    return np.asarray(Image.open(DATA / f"{name}.jpg").convert("RGB").resize((1280, 720), Image.LANCZOS),
                      dtype=np.float32) / 255.0


def field(day, other):
    h, w, _ = day.shape
    gain, off = np.zeros((GY, GX, 3), np.float32), np.zeros((GY, GX, 3), np.float32)
    for j in range(GY):
        for i in range(GX):
            d = day[j * h // GY:(j + 1) * h // GY, i * w // GX:(i + 1) * w // GX].reshape(-1, 3)
            o = other[j * h // GY:(j + 1) * h // GY, i * w // GX:(i + 1) * w // GX].reshape(-1, 3)
            for c in range(3):
                x, y = d[:, c], o[:, c]
                mx, my = x.mean(), y.mean()
                a = (((x - mx) * (y - my)).mean() + PULL * my / max(mx, 1e-3)) / (((x - mx) ** 2).mean() + PULL)
                a = float(np.clip(a, 0.0, 3.0))
                gain[j, i, c], off[j, i, c] = a, my - a * mx
    sm = lambda f: np.stack([gaussian_filter(f[..., c], SMOOTH, mode="nearest") for c in range(3)], -1)
    return sm(gain).astype(np.float16), sm(off).astype(np.float16)


def main():
    day = load("room_07_day")
    out = {}
    for when in ("evening", "night"):
        out[f"{when}_gain"], out[f"{when}_off"] = field(day, load(f"room_07_{when}"))
    np.savez_compressed(DATA / "relight.npz", **out)
    print(f"{DATA / 'relight.npz'}: {(DATA / 'relight.npz').stat().st_size} bytes")


if __name__ == "__main__":
    main()
