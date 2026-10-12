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


def _colour_at(x, y):
    """None outside the tile, else an RGB tuple. x, y in tile units."""
    if not _in_round_rect(x, y, 0, 0, SIDE, RADIUS):
        return None
    if not _in_round_rect(x, y, RIM, RIM, SIDE - 2 * RIM, RADIUS - RIM):
        return ORANGE
    if _in_letters((x - OFF_X) / SCALE, (y - OFF_Y) / SCALE):
        return ORANGE
    k = y / SIDE
    return tuple(round(a + (b - a) * k) for a, b in zip(TILE_TOP, TILE_BOTTOM))


def png(size: int, samples: int = 4) -> bytes:
    """The tile as a size x size PNG with a transparent outside."""
    step = SIDE / size
    offs = [(i + 0.5) / samples for i in range(samples)]
    n = samples * samples
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            r = g = b = a = 0
            for oy in offs:
                y = (py + oy) * step
                for ox in offs:
                    c = _colour_at((px + ox) * step, y)
                    if c:
                        r += c[0]; g += c[1]; b += c[2]; a += 1
            row += bytes((r // a, g // a, b // a, 255 * a // n)) if a else b"\0\0\0\0"
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 32, 48, 256)) -> bytes:
    """A Windows .ico holding the tile at several sizes."""
    pngs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset, entries = 6 + 16 * len(sizes), b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return head + entries + b"".join(pngs)


def write(ui=ROOT / "ui") -> list:
    out = []
    (ui / "logo.svg").write_text(svg() + "\n", encoding="utf-8")
    out.append(ui / "logo.svg")
    for s in (192, 512):
        (ui / f"icon-{s}.png").write_bytes(png(s))
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
