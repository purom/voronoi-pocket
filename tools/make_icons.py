"""Draws the toolbar icons for the Voronoi Pocket command.

Standard library only. Run it again if you change the look:

    python tools/make_icons.py
"""

import math
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'VoronoiPocket', 'commands', 'voronoiPocket', 'resources')

# File name -> size in pixels. Fusion picks the one that fits the screen.
SIZES = {
    '16x16.png': 16,
    '16x16@2x.png': 32,
    '32x32.png': 32,
    '32x32@2x.png': 64,
    '64x64.png': 64,
    '64x64@2x.png': 128,
}

PLATE = (74, 144, 217)    # the solid part: walls and border
POCKET = (27, 48, 77)     # the cut-out cells
SEEDS = [(0.20, 0.22), (0.62, 0.16), (0.86, 0.46), (0.48, 0.50),
         (0.16, 0.66), (0.44, 0.86), (0.80, 0.82)]
WALL = 0.085              # rib width, as a fraction of the icon
BORDER = 0.10             # solid band along the edge
CORNER = 0.16             # rounding of the plate itself
SAMPLES = 4               # anti-aliasing: samples per pixel per axis


def _inside_plate(x, y, inset):
    """Inside the rounded square, ``inset`` in from its edge."""
    lo, hi = inset, 1.0 - inset
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    r = max(CORNER - inset, 0.0)
    cx = min(max(x, lo + r), hi - r)
    cy = min(max(y, lo + r), hi - r)
    return math.hypot(x - cx, y - cy) <= r


def _sample(x, y):
    """Colour and alpha at one point of the unit square."""
    if not _inside_plate(x, y, 0.02):
        return (0, 0, 0, 0)
    if not _inside_plate(x, y, 0.02 + BORDER):
        return PLATE + (255,)
    ranked = sorted((math.hypot(x - sx, y - sy), sx, sy) for sx, sy in SEEDS)
    (d1, x1, y1), (d2, x2, y2) = ranked[0], ranked[1]
    # Distance from this point to the line halfway between the two seeds.
    to_wall = (d2 * d2 - d1 * d1) / (2.0 * math.hypot(x2 - x1, y2 - y1))
    return (PLATE if to_wall < WALL / 2.0 else POCKET) + (255,)


def render(size):
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    cr, cg, cb, ca = _sample((px + (sx + 0.5) / SAMPLES) / size,
                                             (py + (sy + 0.5) / SAMPLES) / size)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            if a > 0:
                row += bytes((int(round(r / a)), int(round(g / a)),
                              int(round(b / a)), int(round(a / SAMPLES ** 2))))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return rows


def write_png(path, size, rows):
    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    raw = b''.join(b'\x00' + row for row in rows)
    header = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(path, 'wb') as handle:
        handle.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
                     + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, size in sorted(SIZES.items()):
        write_png(os.path.join(OUT_DIR, name), size, render(size))
        print('wrote', name)


if __name__ == '__main__':
    main()
