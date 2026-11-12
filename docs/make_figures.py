"""Draws every figure and chart of docs/voronoi_method.html from the add-in's
own maths (core/geometry.py and core/stress.py). Standard library only.

    py docs\\make_figures.py

Writes SVG files to docs/figures and prints the numbers the text quotes.
"""

import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'VoronoiPocket'))
from core import geometry as g  # noqa: E402
from core import stress as s  # noqa: E402

OUT = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)

INK, INK2, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
BLUE, ORANGE = '#2a78d6', '#eb6834'
PLATE, POCKET, EDGE = '#cde2fb', '#184f95', '#104281'
RAMP = ['#eef4fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
FONT = 'font-family="Segoe UI, Helvetica, Arial, sans-serif"'


def circle(cx, cy, r, steps=64):
    return [(cx + r * math.cos(2 * math.pi * k / steps), cy + r * math.sin(2 * math.pi * k / steps))
            for k in range(steps)]


def blob(scale=2.0, steps=400):
    pts = []
    for k in range(steps):
        a = 2 * math.pi * k / steps
        r = 3.0 + 1.4 * math.cos(2 * a)
        pts.append((scale * (7.0 + 1.5 * r * math.cos(a)), scale * (4.6 + r * math.sin(a))))
    return pts


def ramp(v):
    """Light to dark blue for 0..1 (one hue, so it also reads in grey)."""
    v = max(0.0, min(1.0, v)) * (len(RAMP) - 1)
    i = min(int(v), len(RAMP) - 2)
    t = v - i
    a, b = RAMP[i], RAMP[i + 1]
    return '#' + ''.join('%02x' % int(round(int(a[k:k + 2], 16) * (1 - t) + int(b[k:k + 2], 16) * t))
                         for k in (1, 3, 5))


class Fig(object):
    """An SVG drawing of geometry given in centimetres (y up)."""

    def __init__(self, box, width=720, pad=10):
        self.x0, self.y0, x1, y1 = box
        self.scale = (width - 2 * pad) / (x1 - self.x0)
        self.pad = pad
        self.w = width
        self.h = int((y1 - self.y0) * self.scale + 2 * pad)
        self.parts = []

    def pt(self, p):
        return ((p[0] - self.x0) * self.scale + self.pad,
                self.h - ((p[1] - self.y0) * self.scale + self.pad))

    def path(self, loops, fill='none', stroke='none', width=1.0, dash=None, opacity=None):
        d = ''
        for loop in loops:
            d += 'M' + ' L'.join('%.1f %.1f' % self.pt(p) for p in loop) + ' Z '
        extra = (' stroke-dasharray="%s"' % dash if dash else '') + \
                (' fill-opacity="%s"' % opacity if opacity is not None else '')
        self.parts.append('<path d="%s" fill="%s" fill-rule="evenodd" stroke="%s" stroke-width="%s" '
                          'stroke-linejoin="round"%s/>' % (d, fill, stroke, width, extra))

    def line(self, a, b, stroke=INK, width=1.0, dash=None):
        (x1, y1), (x2, y2) = self.pt(a), self.pt(b)
        self.parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s"%s/>'
                          % (x1, y1, x2, y2, stroke, width, ' stroke-dasharray="%s"' % dash if dash else ''))

    def dot(self, p, r=3.0, fill=INK, ring=True):
        x, y = self.pt(p)
        self.parts.append('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s"%s/>'
                          % (x, y, r, fill, ' stroke="%s" stroke-width="1.5"' % SURFACE if ring else ''))

    def arrow(self, a, vec, length, stroke=ORANGE):
        size = math.hypot(*vec)
        d = (vec[0] / size, vec[1] / size)
        b = (a[0] + d[0] * length, a[1] + d[1] * length)
        self.line(a, b, stroke, 3)
        for sgn in (1, -1):
            self.line(b, (b[0] - 0.3 * length * (d[0] + sgn * 0.5 * d[1]),
                          b[1] - 0.3 * length * (d[1] - sgn * 0.5 * d[0])), stroke, 3)

    def text(self, p, label, anchor='start', size=13, fill=INK2, dy=0):
        x, y = self.pt(p)
        self.parts.append('<text x="%.1f" y="%.1f" %s font-size="%d" fill="%s" text-anchor="%s">%s</text>'
                          % (x, y + dy, FONT, size, fill, anchor, label))

    def save(self, name):
        with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
                    '<rect width="100%%" height="100%%" fill="%s"/>%s</svg>'
                    % (self.w, self.h, self.w, self.h, SURFACE, ''.join(self.parts)))


def face_fig(outer, holes, width=720, extra=0.0):
    x0, y0, x1, y1 = g.bounding_box(outer)
    fig = Fig((x0 - extra, y0 - extra, x1 + extra, y1 + extra), width)
    fig.path([outer] + list(holes), PLATE, EDGE, 1.2)
    return fig


def draw_pattern(name, outer, holes, pattern, width=720):
    fig = face_fig(outer, holes, width)
    fig.path([g.path_points(p, 10) for p in pattern.paths], POCKET)
    fig.save(name)


def chart(name, series, xlabel, ylabel, xlim, ylim, xticks, yticks, width=640, height=380,
          refs=(), fmt='%g', notes=()):
    """Line chart. series: [(label, colour, points, 'line'|'dots'|'both')]."""
    left, right, top, bottom = 62, 150, 18, 48
    pw, ph = width - left - right, height - top - bottom
    fx = lambda x: left + (x - xlim[0]) / (xlim[1] - xlim[0]) * pw
    fy = lambda y: top + ph - (y - ylim[0]) / (ylim[1] - ylim[0]) * ph
    out = ['<rect width="100%%" height="100%%" fill="%s"/>' % SURFACE]
    for y in yticks:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (left, fy(y), left + pw, fy(y), GRID))
        out.append('<text x="%d" y="%.1f" %s font-size="12" fill="%s" text-anchor="end">%s</text>'
                   % (left - 8, fy(y) + 4, FONT, INK2, fmt % y))
    for x in xticks:
        out.append('<text x="%.1f" y="%d" %s font-size="12" fill="%s" text-anchor="middle">%s</text>'
                   % (fx(x), top + ph + 18, FONT, INK2, fmt % x))
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (left, top + ph, left + pw, top + ph, INK2))
    out.append('<text x="%.1f" y="%d" %s font-size="13" fill="%s" text-anchor="middle">%s</text>'
               % (left + pw / 2, height - 8, FONT, INK2, xlabel))
    out.append('<text transform="translate(16 %.1f) rotate(-90)" %s font-size="13" fill="%s" '
               'text-anchor="middle">%s</text>' % (top + ph / 2, FONT, INK2, ylabel))
    for value, label in refs:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-dasharray="5 4"/>'
                   % (left, fy(value), left + pw, fy(value), INK2))
        out.append('<text x="%d" y="%.1f" %s font-size="12" fill="%s">%s</text>'
                   % (left + pw + 8, fy(value) + 4, FONT, INK2, label))
    for index, (label, colour, points, kind) in enumerate(series):
        pts = [(fx(x), fy(y)) for x, y in points]
        if kind in ('line', 'both'):
            out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round"/>'
                       % (' '.join('%.1f,%.1f' % p for p in pts), colour))
        if kind in ('dots', 'both'):
            for x, y in pts:
                out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"/>'
                           % (x, y, colour, SURFACE))
        if len(series) > 1:  # legend, identity never by colour alone: the name sits beside the mark
            ly = top + 14 + 22 * index
            lx = left + pw + 14
            if refs:
                ly += 0
            out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2"/>'
                       % (lx, ly + 60, lx + 18, ly + 60, colour))
            if kind != 'line':
                out.append('<circle cx="%d" cy="%d" r="4" fill="%s" stroke="%s" stroke-width="2"/>'
                           % (lx + 9, ly + 60, colour, SURFACE))
            out.append('<text x="%d" y="%d" %s font-size="12" fill="%s">%s</text>'
                       % (lx + 24, ly + 64, FONT, INK, label))
    for (x, y, label) in notes:
        out.append('<text x="%.1f" y="%.1f" %s font-size="12" fill="%s">%s</text>' % (fx(x) + 8, fy(y) - 8, FONT, INK, label))
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">%s</svg>'
                % (width, height, width, height, ''.join(out)))


def stress_fig(name, outer, holes, field, top, held=(), loaded=(), arrows=(), width=720):
    fig = face_fig(outer, holes, width, extra=1.2 if arrows else 0.0)
    mesh = field.mesh
    for i, j, k in mesh.triangles:
        level = (field.node_von_mises[i] + field.node_von_mises[j] + field.node_von_mises[k]) / 3.0
        col = ramp(level / top)
        fig.path([[mesh.nodes[i], mesh.nodes[j], mesh.nodes[k]]], col, col, 0.6)
    fig.path([outer] + list(holes), 'none', EDGE, 1.2)
    for line in held:
        for a, b in zip(line, line[1:]):
            fig.line(a, b, INK, 5)
    for line in loaded:
        for a, b in zip(line, line[1:]):
            fig.line(a, b, ORANGE, 5)
    for start, vec, length in arrows:
        fig.arrow(start, vec, length)
    fig.save(name)


# ----------------------------------------------------------------- shapes
FACE = blob()
HOLES = [circle(20.6, 9.2, 2.6, 96), circle(7.2, 9.2, 0.45, 32)]
MARGIN, WALL, RADIUS = 0.4, 0.3, 0.15
L_SHAPE = [(0.0, 0.0), (20.0, 0.0), (20.0, 8.0), (8.0, 8.0), (8.0, 16.0), (0.0, 16.0)]
E, NU = 7.0e6, 0.33
print('figures ->', OUT)

# 01 the face
fig = face_fig(FACE, HOLES)
fig.save('01_face.svg')

# 02 the face shrunk by the margin, and by margin + radius
rings = [g.ensure_ccw(FACE)] + [g.ensure_cw(h) for h in HOLES]
usable_loops = g.offset_region(rings, MARGIN)
core_loops = g.offset_region(rings, MARGIN + RADIUS)
fig = face_fig(FACE, HOLES)
fig.path(usable_loops, '#ffffff', BLUE, 1.6, opacity=0.55)
fig.path(core_loops, 'none', ORANGE, 1.4, dash='6 4')
fig.save('02_shrunk.svg')
print('usable area %.1f of %.1f cm2' % (sum(g.signed_area(l) for l in usable_loops), sum(g.signed_area(r) for r in rings)))

# 03 / 04 seeds before and after evening out
usable = g.Region(usable_loops)
x0, y0, x1, y1 = g.bounding_box(FACE)
pad = 0.01 * max(x1 - x0, y1 - y0)
BOX = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
seeds = g.random_points(usable, BOX, 60, random.Random(3))
relaxed = g.lloyd_relax(seeds, usable, 2, BOX)
for name, pts in (('03_seeds_random.svg', seeds), ('04_seeds_relaxed.svg', relaxed)):
    fig = face_fig(FACE, HOLES)
    cells = g.voronoi_cells(pts, BOX)
    for cell in cells:
        for part in g.clip_to_region(cell, usable) or []:
            if g.signed_area(part) > 0:
                fig.path([part], 'none', INK2, 0.8)
    for p in pts:
        fig.dot(p, 3.2, BLUE)
    fig.save(name)


def spread(pts):
    areas = []
    for cell in g.voronoi_cells(pts, BOX):
        parts = g.clip_to_region(cell, usable) or []
        areas.append(sum(g.signed_area(p) for p in parts))
    mean = sum(areas) / len(areas)
    return math.sqrt(sum((a - mean) ** 2 for a in areas) / len(areas)) / mean


series = []
for passes in range(0, 9):
    series.append((passes, 100 * spread(g.lloyd_relax(seeds, usable, passes, BOX))))
print('cell area spread (%% of mean) by smoothing passes:', ['%.0f' % v for _, v in series])
chart('c_lloyd.svg', [('', BLUE, series, 'both')], 'Smoothing passes', 'Spread of cell areas (% of mean)',
      (0, 8), (0, 60), range(0, 9), (0, 20, 40, 60))

# 05 one border cell, step by step
cells = g.voronoi_cells(relaxed, BOX)
core_region = g.Region(core_loops)
best = None
for cell in cells:
    shrunk = g.inset_convex(cell, WALL / 2 + RADIUS)
    if not shrunk:
        continue
    cores = g.clip_to_region(shrunk, core_region) or []
    if len(cores) == 1 and g.signed_area(cores[0]) > 0 and not g.is_convex(cores[0]):
        lost = g.signed_area(shrunk) - g.signed_area(cores[0])
        if best is None or lost > best[0]:
            best = (lost, cell, shrunk, cores[0])
_, cell, shrunk, core = best
cx0, cy0, cx1, cy1 = g.bounding_box(cell)
view = (cx0 - 0.6, cy0 - 0.6, cx1 + 0.6, cy1 + 0.6)
fig = Fig(view, 620)
fig.path([FACE] + HOLES, PLATE, EDGE, 1.5)
fig.path(usable_loops, 'none', BLUE, 1.5)
fig.path(core_loops, 'none', ORANGE, 1.2, dash='6 4')
fig.path([cell], 'none', INK2, 1.2, dash='3 3')
fig.path([g.inset_convex(cell, WALL / 2)], 'none', INK2, 1.0)
fig.path([core], '#9ec5f4', 'none')
fig.path([g.path_points(g.grow_loop(core, RADIUS), 12)], 'none', POCKET, 2.6)
fig.save('05_one_cell.svg')

# 06-08 finished patterns
kw = dict(seed=3, wall=WALL, margin=MARGIN, radius=RADIUS, min_width=0.2, relax=2)
p = g.build_pattern(FACE, HOLES, 60, **kw)
draw_pattern('06_pattern_curved.svg', FACE, HOLES, p)
print('curved: %d pockets, share %.1f%%' % (len(p.paths), 100 * p.share))
p = g.build_pattern(L_SHAPE, [], 60, **kw)
draw_pattern('07_pattern_l.svg', L_SHAPE, [], p, 520)
plate = [(0.0, 0.0), (24.0, 0.0), (24.0, 12.0), (0.0, 12.0)]
plate_holes = [circle(5.0, 6.0, 1.4), circle(19.0, 6.0, 1.4), circle(12.0, 6.0, 0.4, 32)]
draw_pattern('08_pattern_holes.svg', plate, plate_holes, g.build_pattern(plate, plate_holes, 70, **kw))

# 09 simple mode at three shares, and asked against reached
for share in (0.3, 0.5, 0.7):
    p = g.build_pattern_for_share(FACE, HOLES, MARGIN, share, seed=3)
    draw_pattern('09_simple_%d.svg' % int(share * 100), FACE, HOLES, p, 360)
    print('simple %.0f%%: got %.1f%%, %d cells, wall %.2f mm, radius %.2f mm'
          % (share * 100, p.share * 100, p.settings['count'], p.settings['wall'] * 10, p.settings['radius'] * 10))
asked = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
lines = []
for margin, label, colour in ((0.4, 'Border 4 mm', BLUE), (0.2, 'Border 2 mm', ORANGE)):
    pts = []
    for share in asked:
        p = g.build_pattern_for_share(FACE, HOLES, margin, share, seed=3)
        pts.append((share * 100, p.share * 100))
    lines.append((label, colour, pts, 'both'))
    print(label, ['%.1f' % y for _, y in pts])
chart('c_simple.svg', lines + [('Asked = reached', INK2, [(20, 20), (90, 90)], 'line')],
      'Pocketing asked for (%)', 'Pocketing reached (%)', (15, 95), (15, 95), range(20, 91, 10), range(20, 91, 10))

# pattern maths time against cell count
pts = []
for count in (30, 60, 120, 250, 500, 1000):
    t = time.perf_counter()
    g.build_pattern(FACE, HOLES, count, **kw)
    pts.append((count, time.perf_counter() - t))
print('pattern time:', ['%d: %.2f s' % p for p in pts])
chart('c_pattern_time.svg', [('', BLUE, pts, 'both')], 'Cells', 'Time (s)', (0, 1000), (0, 2.0),
      (0, 250, 500, 750, 1000), (0, 0.5, 1.0, 1.5, 2.0))

# ------------------------------------------------------------------ stress
HPLATE = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
HOLE = circle(10.0, 5.0, 1.0, 96)
LEFT = [(0.0, 0.0), (0.0, 10.0)]
RIGHT = [(20.0, 0.0), (20.0, 10.0)]
NOMINAL = 1000.0 / (10.0 * 0.5)


def pulled(size):
    return s.solve_plate(HPLATE, [HOLE], [LEFT], [(RIGHT, (1000.0, 0.0))], E, NU, thickness=0.5, size=size)


# 10 the mesh
coarse = pulled(0.5)
fig = Fig((6.0, 2.0, 14.0, 8.0), 620)
for i, j, k in coarse.mesh.triangles:
    fig.path([[coarse.mesh.nodes[i], coarse.mesh.nodes[j], coarse.mesh.nodes[k]]], '#ffffff', INK2, 0.7)
fig.path([HOLE], 'none', BLUE, 2)
for n, node in enumerate(coarse.mesh.nodes):
    if coarse.mesh.on_edge[n] and 6 < node[0] < 14 and 2 < node[1] < 8:
        fig.dot(node, 3, ORANGE)
fig.save('10_mesh.svg')

# 11 stress around the hole; profile against theory; peak against mesh size
fine = pulled(0.3)
stress_fig('11_stress_hole.svg', HPLATE, [HOLE], fine, 2.4 * NOMINAL, held=[LEFT], loaded=[RIGHT],
           arrows=[((20.0, 5.0), (1, 0), 1.0)])
theory = [(r, 1 + 1 / (2 * r * r) + 3 / (2 * r ** 4)) for r in [1 + 0.05 * k for k in range(61)]]
lines = [('Theory (Kirsch)', INK2, theory, 'line')]
for result, label, colour in ((coarse, 'Mesh 0.50 a', ORANGE), (fine, 'Mesh 0.30 a', BLUE)):
    h = result.mesh.size
    pts = sorted((abs(y - 5.0), result.node_stress[n][0] / NOMINAL)
                 for n, (x, y) in enumerate(result.mesh.nodes)
                 if abs(x - 10.0) < 0.35 * h and 0.99 <= abs(y - 5.0) <= 4.0)
    lines.append((label, colour, pts, 'dots'))
chart('c_kirsch.svg', lines, 'Distance from the hole centre (hole radii)', 'Stress / far stress',
      (1, 4), (0.8, 3.2), (1, 2, 3, 4), (1.0, 1.5, 2.0, 2.5, 3.0))
peaks, times = [], []
for size in (0.6, 0.5, 0.4, 0.35, 0.3, 0.25):
    r = pulled(size)
    near = [n for n, (x, y) in enumerate(r.mesh.nodes) if abs(x - 10) < 0.2 and abs(abs(y - 5) - 1) < 0.2]
    peaks.append((1.0 / size, max(r.node_stress[n][0] for n in near) / NOMINAL))
    times.append((len(r.mesh.nodes), r.seconds, r.steps))
print('rim peak by triangles per radius:', ['%.1f: %.2f' % p for p in peaks])
chart('c_peak.svg', [('', BLUE, peaks, 'both')], 'Triangles per hole radius', 'Stress beside the hole / far stress',
      (1.5, 4.2), (1.0, 3.4), (2, 3, 4), (1.0, 1.5, 2.0, 2.5, 3.0), refs=[(3.14, 'theory 3.14')])

# solve time and steps against mesh points
for size in (0.22,):
    r = s.solve_plate(HPLATE, [], [LEFT], [(RIGHT, (0.0, -1000.0))], E, NU, thickness=0.5, size=size)
    times.append((len(r.mesh.nodes), r.seconds, r.steps))
times.sort()
print('solve: points, seconds, steps:', ['%d: %.2f s, %d' % t for t in times])
chart('c_solve_time.svg', [('', BLUE, [(n, sec) for n, sec, _ in times], 'both')], 'Mesh points', 'Solve time (s)',
      (0, 6000), (0, 14), (0, 2000, 4000, 6000), (0, 4, 8, 12))
chart('c_solve_steps.svg', [('', BLUE, [(n, st) for n, _, st in times], 'both')], 'Mesh points', 'Solver steps',
      (0, 6000), (0, 900), (0, 2000, 4000, 6000), (0, 300, 600, 900))

# 12 bent plate: map and profile across the height
bent = s.solve_plate(HPLATE, [], [LEFT], [(RIGHT, (0.0, -1000.0))], E, NU, thickness=0.5, size=0.35)
stress_fig('12_stress_bent.svg', HPLATE, [], bent, 2500.0, held=[LEFT], loaded=[RIGHT],
           arrows=[((20.0, 5.0), (0, -1), 1.0)])
inertia = 0.5 * 10.0 ** 3 / 12.0
pts = sorted((y, bent.node_stress[n][0]) for n, (x, y) in enumerate(bent.mesh.nodes) if abs(x - 10.0) < 0.12)
xs = sorted(set(round(x, 3) for n, (x, y) in enumerate(bent.mesh.nodes) if abs(x - 10.0) < 0.12))
xm = sum(xs) / len(xs)
chart('c_bending.svg', [('Beam theory', INK2, [(0, -1000 * (20 - xm) * 5 / inertia), (10, 1000 * (20 - xm) * 5 / inertia)], 'line'),
                        ('Computed', BLUE, pts, 'dots')],
      'Height across the plate (cm)', 'Stress along the plate (N/cm2)', (0, 10), (-1400, 1400),
      (0, 2, 4, 6, 8, 10), (-1200, -600, 0, 600, 1200))
errs = [abs(v - 1000 * (20 - xm) * (y - 5) / inertia) for y, v in pts]
print('bending profile: worst difference %.0f N/cm2 of %.0f at the edge' % (max(errs), 1000 * (20 - xm) * 5 / inertia))

# 13 a force at a point
pointed = s.solve_plate(HPLATE, [], [LEFT], [], E, NU, thickness=0.5, size=0.35,
                        point_loads=[((14.0, 5.0), (800.0, 0.0))])
stress_fig('13_stress_point.svg', HPLATE, [], pointed, 500.0, held=[LEFT], arrows=[((14.0, 5.0), (1, 0), 1.6)])
print('point force: peak %.0f, far field %.0f (nominal 160)' % (pointed.peak, pointed.von_mises_at((4.0, 5.0))))

# 14 wall rule and size rule
chart('c_wall_rule.svg', [('', BLUE, [(k / 100.0, 1 - math.sqrt(1 - k / 100.0)) for k in range(0, 100)], 'line')],
      'Load: stress / allowed stress', 'Wall thickness / cell size', (0, 1), (0, 1), (0, 0.25, 0.5, 0.75, 1.0),
      (0, 0.25, 0.5, 0.75, 1.0))

# 15 stress-driven patterns: even cells, then cells that follow the load
BPLATE = [(0.0, 0.0), (24.0, 0.0), (24.0, 10.0), (0.0, 10.0)]
field = s.solve_plate(BPLATE, [], [[(0.0, 0.0), (0.0, 10.0)]], [([(24.0, 0.0), (24.0, 10.0)], (0.0, -1000.0))],
                      E, NU, thickness=0.5)
stress_fig('15_stress_bracket.svg', BPLATE, [], field, 2500.0, held=[[(0.0, 0.0), (0.0, 10.0)]],
           loaded=[[(24.0, 0.0), (24.0, 10.0)]], arrows=[((24.0, 5.0), (0, -1), 1.0)])
skw = dict(count=110, seed=3, margin=0.4, wall_min=0.12, radius=0.12, min_width=0.25)
even = s.build_stress_pattern(BPLATE, [], field, 2500.0, size_ratio=1.0, **skw)
varied = s.build_stress_pattern(BPLATE, [], field, 2500.0, size_ratio=2.5, **skw)
draw_pattern('16_stress_even.svg', BPLATE, [], even)
draw_pattern('17_stress_varied.svg', BPLATE, [], varied)
for label, pat in (('even', even), ('varied', varied)):
    st = pat.settings
    sizes = sorted(v for v in st['cell_sizes'] if v > 0)
    print('stress pattern %s: %d pockets, share %.0f%%, walls %.1f mm up, cells %.0f to %.0f mm'
          % (label, len(pat.paths), 100 * pat.share, st['wall'] * 10, sizes[0] * 10, sizes[-1] * 10))
st = varied.settings
pts = [(load, wall / size) for load, wall, size in zip(st['cell_loads'], st['walls'], st['cell_sizes'])
       if size > 0 and load < 1.0]
chart('c_wall_cells.svg', [('Rule', INK2, [(k / 100.0, 1 - math.sqrt(1 - k / 100.0)) for k in range(0, 100)], 'line'),
                           ('Cells of the pattern', BLUE, pts, 'dots')],
      'Load in the cell: stress / allowed stress', 'Wall thickness / cell size', (0, 1), (0, 1),
      (0, 0.25, 0.5, 0.75, 1.0), (0, 0.25, 0.5, 0.75, 1.0))
levels = sorted(field.node_von_mises)
low, high = levels[int(0.05 * (len(levels) - 1))], levels[int(0.95 * (len(levels) - 1))]
pts = [(min(1.0, max(0.0, (field.von_mises_at(c) - low) / (high - low))), size * 10)
       for c, size in zip(st['cell_centres'], st['cell_sizes']) if c and size > 0]
chart('c_size_cells.svg', [('', BLUE, pts, 'dots')], 'How busy the spot is (0 idle, 1 busiest)', 'Cell width (mm)',
      (0, 1), (0, 24), (0, 0.25, 0.5, 0.75, 1.0), (0, 6, 12, 18, 24))

# 18 link with two holes and a point force, stress and pattern
link = [(0.0, 0.0), (26.0, 0.0), (26.0, 9.0), (0.0, 9.0)]
h1, h2 = circle(4.5, 4.5, 1.6, 80), circle(21.5, 4.5, 1.6, 80)
field = s.solve_plate(link, [h1, h2], [h1 + [h1[0]]], [(h2 + [h2[0]], (4000.0, 0.0))], E, NU, thickness=0.5,
                      point_loads=[((13.0, 9.0), (0.0, -1500.0))])
stress_fig('18_stress_link.svg', link, [h1, h2], field, 2500.0, held=[h1 + [h1[0]]], loaded=[h2 + [h2[0]]],
           arrows=[((21.5, 4.5), (1, 0), 2.4), ((13.0, 10.1), (0, -1), 1.0)])
pat = s.build_stress_pattern(link, [h1, h2], field, 2500.0, count=110, seed=5, margin=0.4, wall_min=0.12,
                             radius=0.12, min_width=0.25, size_ratio=2.5)
draw_pattern('19_pattern_link.svg', link, [h1, h2], pat)
print('link: peak %.0f N/cm2, %d pockets, share %.0f%%' % (field.peak, len(pat.paths), 100 * pat.share))
print('done:', len(os.listdir(OUT)), 'files')

# ------------------------------------------------- one self-contained file
# The document shows figures by name (data-fig). Put the drawings inside it,
# so the single HTML file can be opened, sent or printed on its own.
import base64
import re

DOC = os.path.join(HERE, 'voronoi_method.html')
if os.path.exists(DOC):
    html = open(DOC, encoding='utf-8').read()
    html = re.sub(r'src="figures/([^"]+\.svg)"', r'data-fig="\1" src=""', html)

    def embed(match):
        data = open(os.path.join(OUT, match.group(1)), 'rb').read()
        return 'data-fig="%s" src="data:image/svg+xml;base64,%s"' % (
            match.group(1), base64.b64encode(data).decode('ascii'))

    html, count = re.subn(r'data-fig="([^"]+)" src="[^"]*"', embed, html)
    open(DOC, 'w', encoding='utf-8', newline='\n').write(html)
    print('embedded %d figures in %s (%.0f kB)' % (count, DOC, os.path.getsize(DOC) / 1024.0))
