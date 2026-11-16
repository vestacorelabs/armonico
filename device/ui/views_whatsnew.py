"""The What's new screen: shown once on the rest screen after the installed version changed, from the
Highlights block of that version in CHANGELOG.md (the installed folder's own copy, so the lines are
the project's own, in English in both languages; the group labels and the button follow the screen's
language). A device on a fresh install or after a full reset does not see it (the tour covers those),
and neither does one whose CHANGELOG has no Highlights for its version.

Kept in the device settings: whatsnew_seen, the last version that was shown or skipped."""
import re
import time
from pathlib import Path

from .canvas import W, H
from .widgets import BAR, alpha

LABELS = {"new": "wn.new", "improved": "wn.imp", "fixed": "wn.fix"}
SIDE = 210


def changelog_path():
    return Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"


def parse_highlights(text, version):
    """[(label key, [lines])] from the "### Highlights" block of the "## <version>" section, or None
    when the version has no section or the section no block. In the block a line **New**, **Improved**
    or **Fixed** opens a group and each "- " line under it is one item."""
    if not text or not version:
        return None
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"##\s+" + re.escape(version) + r"(\s|$)", ln)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"##\s", lines[i])), len(lines))
    hi = next((i for i in range(start + 1, end) if re.match(r"###\s+Highlights\s*$", lines[i])), None)
    if hi is None:
        return None
    groups, cur = [], None
    for ln in lines[hi + 1:end]:
        if re.match(r"###\s", ln):
            break
        if not ln.strip():
            cur = None                              # a blank line closes a group: bullets after it are not Highlights
            continue
        m = re.match(r"\*\*(\w+)\*\*\s*:?\s*$", ln.strip())
        if m and m.group(1).lower() in LABELS:
            cur = (m.group(1).lower(), [])
            groups.append(cur)
        elif cur is not None and ln.lstrip().startswith("- "):
            item = ln.lstrip()[2:].strip()
            if item:
                cur[1].append(item)
    groups = [(k, v) for k, v in groups if v]
    return groups or None


class WhatsNewViews:
    def init_whatsnew_state(self):
        self.wn = None                              # {"v": version, "groups": [...]} while it is due
        self.wn_due = False
        self.wn_scroll = 0

    def wn_load(self):
        """At the start: due when the version differs from the last one shown, the setup is done and
        the version has Highlights; recorded as seen, with nothing shown, in every other case."""
        v = self.version()
        if not v or self.st.get("whatsnew_seen") == v:
            return
        groups = None
        if self.st.get("setup_done"):
            try:
                groups = parse_highlights(changelog_path().read_text(encoding="utf-8"), v)
            except (OSError, ValueError):
                groups = None
        if groups:
            self.wn, self.wn_due = {"v": v, "groups": groups}, True
        else:
            self.save(whatsnew_seen=v)

    def wn_ready(self):
        """Due, and the device is idle on the rest screen with no tour, question or lesson in the way."""
        return bool(self.wn_due and self.wn and self.view == "rest" and not self.confirm
                    and not self.tour_due() and not self.device_busy()
                    and time.time() >= self.screen.summary_until and time.time() >= self.off_until)

    def open_whatsnew(self):
        self.wn_scroll = 0
        self.go("whatsnew")

    def wn_done(self):
        v = (self.wn or {}).get("v") or self.version()
        self.wn_due = False
        self.save(whatsnew_seen=v)
        self.go("rest")

    def wn_rows(self):
        rows = []
        for key, items in (self.wn or {}).get("groups", []):
            rows.append(("g", self.tr(LABELS[key])))
            rows += [("i", it) for it in items]
        return rows

    def wn_move(self, d):
        self.wn_scroll = max(0, self.wn_scroll + d * 50)

    def view_whatsnew(self, t):
        P, cv, tr = self.P, self.cv, self.tr
        self.hills(0.35)
        with cv.physical():                         # the lines are English, in both languages: the screen is left to right, never mirrored
            wn = self.wn or {"v": self.version(), "groups": []}
            cv.rect(0, 0, SIDE, H - BAR, P["panel"])
            cv.text(wn["v"], 24, 92, 54, P["gold"])
            cv.text(tr("wn.date", d=self.local().strftime("%d.%m.%Y")), 24, 122, 14, P["dim"])
            cv.text(tr("wn.title"), 24, 168, 22, P["ink"], True)
            bx, by, bw, bh = 18, H - BAR - 66, SIDE - 36, 46
            self.hit(bx, by, bw, bh, self.wn_done)
            cv.rect(bx, by, bw, bh, P["gold"], r=12)
            cv.text(tr("wn.ok"), bx + bw / 2, by + 30, 18, (26, 20, 5), True, "center")
            x, w, top, bottom = SIDE + 28, W - SIDE - 56, 22, H - BAR - 8
            rows = []                               # (kind, line, height)
            pitch = max(25, 16 * cv.zoom * 1.15)    # the lines never touch, at any interface size
            for kind, text in self.wn_rows():
                if kind == "g":
                    rows.append((kind, text, 32 * max(1.0, cv.zoom * 0.9)))
                else:
                    ls = cv.wrap(text, w - 14, 16)  # a long line is wrapped, not cut
                    rows += [(kind, ln, pitch + (6 if j == len(ls) - 1 else 0)) for j, ln in enumerate(ls)]
            total = sum(r[2] for r in rows)
            self.wn_scroll = min(self.wn_scroll, max(0, total - (bottom - top)))
            y = top - self.wn_scroll
            for kind, text, h in rows:
                if y >= top - 4 and y + h <= bottom + 4:
                    if kind == "g":
                        cv.text(text, x, y + 20, 15, P["gold"], True)
                        cv.rect(x, y + h - 7, w, 1, alpha(P["dim"], 0.4))
                    else:
                        cv.text(text, x, y + 18 * max(1.0, cv.zoom * 0.9), 16, P["ink"])
                y += h
            if total > bottom - top:
                cv.rect(W - 8, top, 4, bottom - top, alpha(P["dim"], 0.25), r=2)
                th = max(18, (bottom - top) ** 2 / total)
                cv.rect(W - 8, top + (bottom - top - th) * self.wn_scroll / max(1, total - (bottom - top)), 4, th, P["gold"], r=2)
                self.scroll_zone(x, top, w, bottom - top, 50, lambda d: self.wn_move(d))
        self.bar([("☰", "", self.wn_done), ("▼", tr("down"), lambda: self.wn_move(1)),
                  ("▲", tr("up"), lambda: self.wn_move(-1)), ("✓", tr("wn.ok"), self.wn_done)])
