"""
mark.py — the program's mark: BT in orange on a dark tile, drawn once here.

    python -m coordination.mark --write     writes ui/logo.svg, the app icons and the website's copy

"i want the wordmark "BT" as the logo of the program as like we have for
Premier pro, after effects" — a rounded tile, two letters, a thin border in
the letters' colour. The letters are built from straight lines and circles,
not a font, so the mark is the same everywhere and sharp at any size. The
T's bar runs on across the top of the B, so the two letters are one piece.

One geometry, three outputs: an SVG for the screens, PNGs for the installed
app (ui/icon-192.png, ui/icon-512.png) and the Windows installer's icon
(installer/build_windows.py). No drawing library: the PNGs are worked out
pixel by pixel, sampled 4 x 4 per pixel so the edges are smooth.

"program should have the program icon with botix written" — the same tile
with BOTIX across it, in the same orange, for every size where five letters
can be read (48 pixels and up: the desktop, the Start menu, the installer and
the uninstall list). At 16 to 32 pixels — a taskbar or a title bar — five
letters are a smudge, so the tile keeps BT there, as every program's small
icon does. The installer's own pictures (bmp()) are drawn from it too, so no
stock Windows image is left in it.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TILE_TOP, TILE_BOTTOM = (29, 32, 40), (17, 19, 24)   # cool graphite, lighter at the top
ORANGE = (232, 131, 79)      # #E8834F — a quiet orange, not a bright one
TILE_HEX, TILE_HEX_2, ORANGE_HEX = "#1D2028", "#111318", "#E8834F"

# The tile is 240 units square; the letters sit on a 100-unit-high grid.
SIDE, RADIUS, RIM = 240.0, 46.0, 4.0
T = 14.0                                  # stroke
HU, HL = 28.0, 30.0                       # the B's two counters
CXU, CXL = 42.0, 46.0                     # where the bowls start to curve
RU, RL = T + HU / 2, T + HL / 2           # outer radius of each bowl
SCALE = 0.85
OFF_X, OFF_Y = (SIDE - 152 * SCALE) / 2, (SIDE - 100 * SCALE) / 2

LETTERS = (f"M0 0H{CXU:g}A{RU:g} {RU:g} 0 0 1 {CXU:g} {2 * RU:g}H0Z"
           f"M{T:g} {T:g}H{CXU:g}A{HU / 2:g} {HU / 2:g} 0 0 1 {CXU:g} {T + HU:g}H{T:g}Z",
           f"M0 {T + HU:g}H{CXL:g}A{RL:g} {RL:g} 0 0 1 {CXL:g} 100H0Z"
           f"M{T:g} {2 * T + HU:g}H{CXL:g}A{HL / 2:g} {HL / 2:g} 0 0 1 {CXL:g} {100 - T:g}H{T:g}Z",
           f"M0 0H152V{T:g}H0Z", f"M111 {T:g}H125V100H111Z")


def svg(size=None, title="BT") -> str:
    """The tile as SVG. size: its width and height in pixels, or None to fill its box."""
    wh = f' width="{size}" height="{size}"' if size else ""
    paths = "".join(f'<path fill-rule="evenodd" d="{d}"/>' for d in LETTERS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"{wh} role="img" aria-label="{title}">'
            f'<defs><linearGradient id="bt-tile" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TILE_HEX}"/>'
            f'<stop offset="1" stop-color="{TILE_HEX_2}"/></linearGradient></defs>'
            f'<rect width="240" height="240" rx="{RADIUS:g}" fill="url(#bt-tile)"/>'
            f'<rect x="{RIM / 2:g}" y="{RIM / 2:g}" width="{SIDE - RIM:g}" height="{SIDE - RIM:g}" rx="{RADIUS - RIM / 2:g}" '
            f'fill="none" stroke="{ORANGE_HEX}" stroke-width="{RIM:g}"/>'
            f'<g fill="{ORANGE_HEX}" transform="translate({OFF_X:g} {OFF_Y:g}) scale({SCALE:g})">{paths}</g></svg>')


# ---------------------------------------------------------------------------
# the same shapes, as "is this point inside?" — for the PNGs
# ---------------------------------------------------------------------------

def _in_round_rect(x, y, x0, y0, side, r):
    if not (x0 <= x <= x0 + side and y0 <= y <= y0 + side):
        return False
    cx = min(max(x, x0 + r), x0 + side - r)
    cy = min(max(y, y0 + r), y0 + side - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_d(x, y, top, bottom, cx):
    """A D shape: a rectangle from x=0 to cx, then a half circle to the right."""
    if not (top <= y <= bottom) or x < 0:
        return False
    if x <= cx:
        return True
    r, cy = (bottom - top) / 2, (top + bottom) / 2
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_hole(x, y, top, bottom, cx):
    return x >= T and _in_d(x, y, top, bottom, cx)


def _in_letters(x, y):
    """x, y on the letters' 100-high grid."""
    if 0 <= x <= 152 and 0 <= y <= T:              # one bar across the top of both letters
        return True
    if 111 <= x <= 125 and T <= y <= 100:
        return True
    upper = _in_d(x, y, 0, 2 * RU, CXU) and not _in_hole(x, y, T, T + HU, CXU)
    lower = _in_d(x, y, T + HU, 100, CXL) and not _in_hole(x, y, 2 * T + HU, 100 - T, CXL)
    return upper or lower


# BOTIX: cap height 60, stroke 10, built like the B above — straight lines and
# circles, no font. x is measured from each letter's own left edge.
WT = 10.0
WORD_GAP = 9.0
WORD_SCALE = 0.82


def _in_bowl(x, y, top, bottom, cx, inset=0.0):
    r = (bottom - top) / 2 - inset
    mid = (top + bottom) / 2
    if r <= 0:
        return False
    if inset <= x <= cx and mid - r <= y <= mid + r:
        return True
    return x > cx and (x - cx) ** 2 + (y - mid) ** 2 <= r * r


def _w_b(x, y):
    up = _in_bowl(x, y, 0, 35, 20) and not _in_bowl(x, y, 0, 35, 20, WT)
    low = _in_bowl(x, y, 25, 60, 22) and not _in_bowl(x, y, 25, 60, 22, WT)
    return up or low


def _stadium(x, y, x0, y0, w, h, inset=0.0):
    """A rounded column w wide and h tall (ends of radius w/2), shrunk by inset."""
    r = w / 2 - inset
    cx = x0 + w / 2
    top, bottom = y0 + w / 2, y0 + h - w / 2
    if r <= 0:
        return False
    if top <= y <= bottom:
        return abs(x - cx) <= r
    yy = top if y < top else bottom
    return (x - cx) ** 2 + (y - yy) ** 2 <= r * r


def _w_o2(x, y):
    return _stadium(x, y, 0, 0, 48, 60) and not _stadium(x, y, 0, 0, 48, 60, WT)


def _w_t(x, y):
    return (0 <= x <= 44 and 0 <= y <= WT) or (17 <= x <= 17 + WT and 0 <= y <= 60)


def _w_i(x, y):
    return 0 <= x <= WT and 0 <= y <= 60


def _seg(x, y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    k = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return ((x - ax - k * dx) ** 2 + (y - ay - k * dy) ** 2) ** 0.5


def _w_x(x, y):
    if not (0 <= y <= 60 and 0 <= x <= 46):
        return False
    w = WT * 0.62
    return _seg(x, y, 4, 0, 42, 60) <= w or _seg(x, y, 42, 0, 4, 60) <= w


WORD = ((_w_b, 40.0), (_w_o2, 48.0), (_w_t, 44.0), (_w_i, WT), (_w_x, 46.0))
WORD_W = sum(w for _f, w in WORD) + WORD_GAP * (len(WORD) - 1)
WORD_X, WORD_Y = (SIDE - WORD_W * WORD_SCALE) / 2, (SIDE - 60 * WORD_SCALE) / 2


def _in_word(x, y):
    """Is (x, y) — in word units, the word's top left at 0, 0 — inside BOTIX?"""
    if y < 0 or y > 60:
        return False
    at = 0.0
    for f, w in WORD:
        if at <= x <= at + w:
            return f(x - at, y)
        at += w + WORD_GAP
    return False


def _colour_at(x, y, word=False):
    """None outside the tile, else an RGB tuple. x, y in tile units."""
    if not _in_round_rect(x, y, 0, 0, SIDE, RADIUS):
        return None
    if not _in_round_rect(x, y, RIM, RIM, SIDE - 2 * RIM, RADIUS - RIM):
        return ORANGE
    if word:
        if _in_word((x - WORD_X) / WORD_SCALE, (y - WORD_Y) / WORD_SCALE):
            return ORANGE
    elif _in_letters((x - OFF_X) / SCALE, (y - OFF_Y) / SCALE):
        return ORANGE
    k = y / SIDE
    return tuple(round(a + (b - a) * k) for a, b in zip(TILE_TOP, TILE_BOTTOM))


WORD_FROM = 48                 # the smallest size at which BOTIX is drawn instead of BT


def pixels(size: int, word=None, samples: int = 4) -> list:
    """The tile as rows of (r, g, b, a), size x size. word: BOTIX (True), BT (False), or by size (None)."""
    word = size >= WORD_FROM if word is None else word
    step = SIDE / size
    offs = [(i + 0.5) / samples for i in range(samples)]
    n = samples * samples
    out = []
    for py in range(size):
        row = []
        for px in range(size):
            r = g = b = a = 0
            for oy in offs:
                y = (py + oy) * step
                for ox in offs:
                    c = _colour_at((px + ox) * step, y, word)
                    if c:
                        r += c[0]; g += c[1]; b += c[2]; a += 1
            row.append((r // a, g // a, b // a, 255 * a // n) if a else (0, 0, 0, 0))
        out.append(row)
    return out


def png(size: int, samples: int = 4, word=None) -> bytes:
    """The tile as a size x size PNG with a transparent outside."""
    rows = [b"\0" + b"".join(bytes(p) for p in row) for row in pixels(size, word, samples)]

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    """A Windows .ico holding the tile at several sizes: BT up to 32 pixels, BOTIX from 48."""
    pngs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset, entries = 6 + 16 * len(sizes), b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return head + entries + b"".join(pngs)


def _over(px, bg):
    r, g, b, a = px
    return tuple(round((c * a + d * (255 - a)) / 255) for c, d in zip((r, g, b), bg))


def bmp(width: int, height: int, tile: int, at_y: int) -> bytes:
    """A 24-bit Windows bitmap for the installer's own pictures: the graphite of
    the tile from top to bottom, the BOTIX tile on it, centred across."""
    t = pixels(tile, word=tile >= WORD_FROM)
    x0 = (width - tile) // 2
    rows = []
    for y in range(height):
        k = y / max(1, height - 1)
        bg = tuple(round(a + (b - a) * k) for a, b in zip(TILE_TOP, TILE_BOTTOM))
        row = bytearray()
        for x in range(width):
            c = bg
            if at_y <= y < at_y + tile and x0 <= x < x0 + tile:
                c = _over(t[y - at_y][x - x0], bg)
            row += bytes((c[2], c[1], c[0]))            # BMP keeps blue, green, red
        row += b"\0" * (-len(row) % 4)
        rows.append(bytes(row))
    data = b"".join(reversed(rows))                     # bottom row first
    head = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(data), 2835, 2835, 0, 0)
    return b"BM" + struct.pack("<IHHI", 14 + len(head) + len(data), 0, 0, 14 + len(head)) + head + data


def installer_pictures(folder: Path) -> list:
    """The Setup wizard's pictures, at 100% and 200%: the tall one on its first and
    last pages, the small one in the corner of the others."""
    folder.mkdir(parents=True, exist_ok=True)
    made = {"wizard-large.bmp": bmp(164, 314, 112, 70), "wizard-large-2x.bmp": bmp(328, 628, 224, 140),
            "wizard-small.bmp": bmp(55, 55, 51, 2), "wizard-small-2x.bmp": bmp(110, 110, 102, 4)}
    for name, data in made.items():
        (folder / name).write_bytes(data)
    return [folder / n for n in made]


def word_svg(size=None, title="Botix") -> str:
    """The BOTIX tile as SVG, drawn from the same shapes (for pages that want the word)."""
    wh = f' width="{size}" height="{size}"' if size else ""
    s = WORD_SCALE
    at, parts = WORD_X, []
    for f, w in WORD:
        parts.append((f, at, w))
        at += (w + WORD_GAP) * s
    paths = []
    for f, x0, _w in parts:
        tr = f'translate({x0:g} {WORD_Y:g}) scale({s:g})'
        if f is _w_b:
            d = ("M0 0H20A17.5 17.5 0 0 1 20 35H0Z M10 10H20A7.5 7.5 0 0 1 20 25H10Z"
                 " M0 25H22A17.5 17.5 0 0 1 22 60H0Z M10 35H22A7.5 7.5 0 0 1 22 50H10Z")
            paths.append(f'<path fill-rule="evenodd" transform="{tr}" d="{d}"/>')
        elif f is _w_o2:
            paths.append(f'<path fill-rule="evenodd" transform="{tr}" d="M0 24A24 24 0 0 1 48 24V36A24 24 0 0 1 0 36Z'
                         f' M10 24A14 14 0 0 1 38 24V36A14 14 0 0 1 10 36Z"/>')
        elif f is _w_t:
            paths.append(f'<path transform="{tr}" d="M0 0H44V{WT:g}H0Z M17 0H{17 + WT:g}V60H17Z"/>')
        elif f is _w_i:
            paths.append(f'<path transform="{tr}" d="M0 0H{WT:g}V60H0Z"/>')
        else:
            sw = WT * 1.24
            paths.append(f'<path transform="{tr}" stroke="{ORANGE_HEX}" stroke-width="{sw:g}" fill="none" '
                         f'd="M4 0L42 60M42 0L4 60" clip-path="url(#bx-clip)"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"{wh} role="img" aria-label="{title}">'
            f'<defs><linearGradient id="bx-tile" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TILE_HEX}"/>'
            f'<stop offset="1" stop-color="{TILE_HEX_2}"/></linearGradient>'
            f'<clipPath id="bx-clip" clipPathUnits="userSpaceOnUse"><rect x="0" y="0" width="46" height="60"/></clipPath></defs>'
            f'<rect width="240" height="240" rx="{RADIUS:g}" fill="url(#bx-tile)"/>'
            f'<rect x="{RIM / 2:g}" y="{RIM / 2:g}" width="{SIDE - RIM:g}" height="{SIDE - RIM:g}" rx="{RADIUS - RIM / 2:g}" '
            f'fill="none" stroke="{ORANGE_HEX}" stroke-width="{RIM:g}"/>'
            f'<g fill="{ORANGE_HEX}">{"".join(paths)}</g></svg>')


def write(ui=ROOT / "ui") -> list:
    out = []
    (ui / "logo.svg").write_text(svg() + "\n", encoding="utf-8")
    out.append(ui / "logo.svg")
    for s in (192, 512):
        (ui / f"icon-{s}.png").write_bytes(png(s, word=True))
        out.append(ui / f"icon-{s}.png")
    site = ROOT / "website" / "assets"                 # the TechDuo website shows the same mark
    if site.is_dir():
        (site / "botix-logo.svg").write_text(svg() + "\n", encoding="utf-8")
        (site / "botix-192.png").write_bytes((ui / "icon-192.png").read_bytes())
        out += [site / "botix-logo.svg", site / "botix-192.png"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The BT mark.")
    ap.add_argument("--write", action="store_true", help="write ui/logo.svg and the app icons")
    a = ap.parse_args(argv)
    if a.write:
        for p in write():
            print("wrote", p.relative_to(ROOT))
    else:
        print(svg())
    return 0


if __name__ == "__main__":
    sys.exit(main())
