"""2D geometry for the Voronoi Pocket add-in.

Pure Python on purpose: no ``adsk`` imports, so this module can be tested
outside Fusion. Points are ``(x, y)`` tuples and polygons are lists of points
without a repeated closing point. Lengths are in whatever unit the caller uses
(the add-in passes centimetres, Fusion's internal unit).

A region (a face, or a face shrunk by some distance) is a list of loops: the
outline runs counter-clockwise and holes run clockwise, so the material is
always on the left of every loop.

How one pocket is made
----------------------
A pocket is its Voronoi cell shrunk by half the wall, cut back to the face
shrunk by the border margin, with every corner rounded by ``r``. The rounding
is done the exact way: shrink both by ``r`` more (the "core"), then grow the
core back by ``r``, which turns its corners into arcs of radius ``r``:

    core   = (cell shrunk by wall/2 + r)  AND  (face shrunk by margin + r)
    pocket = core grown by r

The face is shrunk as a whole, curved edges and holes included, so wherever a
pocket reaches the border, the solid band left there is exactly ``margin``.
"""

import math
import random

# Vertices closer together than this are merged (1e-4 cm = 1 micron).
MERGE_TOL = 1e-4
# Round corners made while shrinking a face are built from short straight
# pieces that stay within this distance of the true arc (5e-4 cm = 5 micron).
ARC_TOL = 5e-4
# Points closer than this count as the same point when pieces of an outline
# are joined up.
SNAP_TOL = 1e-7
# Pocket corners that turn less than this get no arc: it would be tiny. This
# is for the many small corners a pocket has where it follows a curved edge.
SMALL_TURN = math.radians(12.0)


# --------------------------------------------------------------------------
# Basic polygon helpers
# --------------------------------------------------------------------------

def signed_area(poly):
    """Area of a polygon; positive when the points run counter-clockwise."""
    total = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def ensure_ccw(poly):
    """Return the polygon with counter-clockwise winding."""
    return list(poly) if signed_area(poly) >= 0 else list(reversed(poly))


def ensure_cw(poly):
    """Return the polygon with clockwise winding."""
    return list(poly) if signed_area(poly) <= 0 else list(reversed(poly))


def centroid(poly):
    """Area centroid of a polygon (vertex average if the area is ~0)."""
    area6 = 0.0
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        area6 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area6) < 1e-18:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (3.0 * area6), cy / (3.0 * area6))


def bounding_box(poly):
    """(min_x, min_y, max_x, max_y) of a polygon."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def point_in_polygon(pt, poly):
    """True if the point is inside the polygon (ray casting)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def clean_polygon(poly, tol=MERGE_TOL):
    """Drop repeated and collinear vertices. Returns [] if nothing is left."""
    pts = []
    for p in poly:
        if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > tol:
            pts.append((float(p[0]), float(p[1])))
    while len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0],
                                      pts[0][1] - pts[-1][1]) <= tol:
        pts.pop()

    changed = True
    while changed and len(pts) >= 3:
        changed = False
        n = len(pts)
        for i in range(n):
            ax, ay = pts[i - 1]
            bx, by = pts[i]
            cx, cy = pts[(i + 1) % n]
            base = math.hypot(cx - ax, cy - ay)
            if base <= tol:
                del pts[i]
                changed = True
                break
            # Distance of b from the line a-c.
            dist = abs((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / base
            if dist <= tol * 0.1:
                del pts[i]
                changed = True
                break

    if len(pts) < 3 or abs(signed_area(pts)) <= tol * tol:
        return []
    return pts


def is_convex(poly):
    """True if a counter-clockwise polygon has no inward (reflex) corners."""
    n = len(poly)
    if n < 3:
        return False
    for i in range(n):
        ax, ay = poly[i - 1]
        bx, by = poly[i]
        cx, cy = poly[(i + 1) % n]
        e1x, e1y = bx - ax, by - ay
        e2x, e2y = cx - bx, cy - by
        scale = math.hypot(e1x, e1y) * math.hypot(e2x, e2y)
        if scale > 0 and (e1x * e2y - e1y * e2x) / scale < -1e-9:
            return False
    return True


def _inside_convex(pt, poly):
    """True if the point is strictly inside a counter-clockwise convex polygon."""
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax) <= 0:
            return False
    return True


# --------------------------------------------------------------------------
# Clipping and shrinking convex polygons
# --------------------------------------------------------------------------

def clip_halfplane(poly, a, b, c):
    """Keep the part of a convex polygon where ``a*x + b*y <= c``."""
    out = []
    if not poly:
        return out
    prev = poly[-1]
    prev_d = a * prev[0] + b * prev[1] - c
    for cur in poly:
        cur_d = a * cur[0] + b * cur[1] - c
        if (cur_d <= 0) != (prev_d <= 0):
            t = prev_d / (prev_d - cur_d)
            out.append((prev[0] + t * (cur[0] - prev[0]),
                        prev[1] + t * (cur[1] - prev[1])))
        if cur_d <= 0:
            out.append(cur)
        prev, prev_d = cur, cur_d
    return out


def _clip_to_edges(subject, clip, inset):
    """Clip ``subject`` by every edge of CCW convex ``clip`` moved in by ``inset``."""
    out = list(subject)
    n = len(clip)
    for i in range(n):
        x1, y1 = clip[i]
        x2, y2 = clip[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        nx, ny = dy / length, -dx / length  # outward normal of a CCW edge
        out = clip_halfplane(out, nx, ny, nx * x1 + ny * y1 - inset)
        if len(out) < 3:
            return []
    return clean_polygon(out)


def clip_convex(subject, clip):
    """Intersection of two convex polygons (``clip`` must be CCW)."""
    return _clip_to_edges(subject, clip, 0.0)


def inset_convex(poly, distance):
    """Shrink a CCW convex polygon inward. Returns [] if it disappears."""
    if distance <= 0:
        return clean_polygon(poly)
    return _clip_to_edges(poly, poly, distance)


# --------------------------------------------------------------------------
# Distances and intersections
# --------------------------------------------------------------------------

def _point_segment_distance(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a, b, c, d):
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def segment_distance(a, b, c, d):
    """Shortest distance between segment a-b and segment c-d."""
    if _segments_cross(a, b, c, d):
        return 0.0
    return min(_point_segment_distance(a, c, d),
               _point_segment_distance(b, c, d),
               _point_segment_distance(c, a, b),
               _point_segment_distance(d, a, b))


def polygon_outline_distance(poly, loop):
    """Shortest distance between the outlines of two polygons."""
    best = float('inf')
    n, m = len(poly), len(loop)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(m):
            dist = segment_distance(a, b, loop[j], loop[(j + 1) % m])
            if dist < best:
                best = dist
                if best == 0.0:
                    return 0.0
    return best


def point_ring_distance(pt, ring):
    """Shortest distance from a point to the outline of a polygon."""
    n = len(ring)
    return min(_point_segment_distance(pt, ring[i], ring[(i + 1) % n])
               for i in range(n))


def _segment_intersection(a, b, c, d):
    """Where a-b and c-d cross strictly inside both: (t, u, point) or None.

    ``t`` and ``u`` are how far along a-b and c-d the crossing is (0..1).
    """
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) <= 1e-14 * (abs(rx) + abs(ry)) * (abs(sx) + abs(sy)):
        return None  # parallel
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if 1e-12 < t < 1.0 - 1e-12 and 1e-12 < u < 1.0 - 1e-12:
        return t, u, (a[0] + t * rx, a[1] + t * ry)
    return None


# --------------------------------------------------------------------------
# Shrinking a whole region (outline and holes) by a distance
# --------------------------------------------------------------------------

def _arc_steps(radius, sweep, tol):
    """How many straight pieces an arc needs to stay within ``tol`` of it."""
    if radius <= tol:
        return 1
    step = 2.0 * math.acos(max(-1.0, 1.0 - tol / radius))
    step = min(step, math.radians(15.0))
    return max(1, int(math.ceil(abs(sweep) / step)))


def _offset_ring_raw(ring, d, tol):
    """``ring`` moved ``d`` to its left.

    At a left turn the two moved edges are extended until they meet. At a
    right turn they are joined by an arc of radius ``d`` round the corner.
    The result can cross itself where the ring is narrower than ``2 * d``;
    ``offset_region`` cleans that up.
    """
    n = len(ring)
    dirs = []
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        dirs.append((dx / length, dy / length))

    out = []
    for i in range(n):
        vx, vy = ring[i]
        d1x, d1y = dirs[i - 1]
        d2x, d2y = dirs[i]
        n1x, n1y = -d1y, d1x  # left normals of the two edges at this corner
        n2x, n2y = -d2y, d2x
        cross = d1x * d2y - d1y * d2x
        dot = d1x * d2x + d1y * d2y
        if cross > 1e-12 or (abs(cross) <= 1e-12 and dot > 0):
            denom = 1.0 + n1x * n2x + n1y * n2y
            if denom < 1e-3:  # needle-sharp corner: leave it to the clean-up
                out.append((vx + d * n1x, vy + d * n1y))
                out.append((vx + d * n2x, vy + d * n2y))
            else:
                k = d / denom
                out.append((vx + k * (n1x + n2x), vy + k * (n1y + n2y)))
        else:
            start = math.atan2(n1y, n1x)
            sweep = math.atan2(cross, dot)  # negative: clockwise
            if sweep >= 0:
                sweep = -math.pi
            steps = _arc_steps(d, sweep, tol)
            # The straight pieces touch the arc from outside (their corners
            # sit slightly beyond it), so they never cut closer than ``d``.
            reach = d / math.cos(sweep / (2.0 * steps))
            for k in range(steps):
                angle = start + sweep * (k + 0.5) / steps
                out.append((vx + reach * math.cos(angle), vy + reach * math.sin(angle)))
    return out


def _first_crossing(ring):
    """Two edges of ``ring`` that cross: (i, j, point) with i < j, or None."""
    n = len(ring)
    if n < 4:
        return None
    minx, miny, maxx, maxy = bounding_box(ring)
    size = max(maxx - minx, maxy - miny)
    if size <= 0:
        return None
    cell = size / max(1, min(256, int(math.sqrt(n))))

    grid = {}
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        cx0 = int((min(a[0], b[0]) - minx) / cell)
        cx1 = int((max(a[0], b[0]) - minx) / cell)
        cy0 = int((min(a[1], b[1]) - miny) / cell)
        cy1 = int((max(a[1], b[1]) - miny) / cell)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                grid.setdefault((cx, cy), []).append(i)

    checked = set()
    for members in grid.values():
        count = len(members)
        for p in range(count):
            for q in range(p + 1, count):
                i, j = members[p], members[q]
                if i > j:
                    i, j = j, i
                if j - i <= 1 or (i == 0 and j == n - 1):
                    continue  # neighbours share a corner, they cannot cross
                key = i * n + j
                if key in checked:
                    continue
                checked.add(key)
                hit = _segment_intersection(ring[i], ring[i + 1],
                                            ring[j], ring[(j + 1) % n])
                if hit:
                    return i, j, hit[2]
    return None


def _split_crossings(ring):
    """Cut a self-crossing ring into loops that no longer cross themselves."""
    done = []
    todo = [ring]
    budget = 5000
    while todo and budget > 0:
        budget -= 1
        current = todo.pop()
        hit = _first_crossing(current)
        if hit is None:
            done.append(current)
            continue
        i, j, point = hit
        todo.append([point] + current[i + 1:j + 1])
        todo.append(current[:i + 1] + [point] + current[j + 1:])
    return done + todo


def _far_enough(loop, ring, d, tol):
    """True if the inside of ``loop`` is at least ``d`` away from ``ring``.

    Checked just inside the three longest edges of the loop. Left-over pieces
    that fold back over the band near the edge fail this.
    """
    n = len(loop)

    def length(i):
        a, b = loop[i], loop[(i + 1) % n]
        return math.hypot(b[0] - a[0], b[1] - a[1])

    for i in sorted(range(n), key=length, reverse=True)[:3]:
        a, b = loop[i], loop[(i + 1) % n]
        size = length(i)
        if size <= 0:
            continue
        step = min(0.25 * size, 0.01 * d)
        probe = ((a[0] + b[0]) / 2.0 - step * (b[1] - a[1]) / size,
                 (a[1] + b[1]) / 2.0 + step * (b[0] - a[0]) / size)
        if point_ring_distance(probe, ring) < d - 2.0 * tol:
            return False
    return True


def offset_region(rings, d, tol=ARC_TOL):
    """Shrink a region by ``d``.

    ``rings`` is the outline (counter-clockwise) followed by the holes
    (clockwise). In the result the outline has moved inward and the holes have
    grown, both by ``d``, with round corners where the material bends away.
    Every point inside the result is at least ``d`` from the region's edge.

    The result is again a list of loops. It can have fewer or more loops than
    the input: a narrow neck pinches the outline in two, a part thinner than
    ``2 * d`` vanishes. Grown holes may overlap each other or stick out past
    the outline; ``Region`` handles that.
    """
    if d <= 0:
        return [list(ring) for ring in rings]
    loops = []
    for ring in rings:
        ccw = signed_area(ring) > 0
        for loop in _split_crossings(_offset_ring_raw(ring, d, tol)):
            loop = clean_polygon(loop)
            if not loop or (signed_area(loop) > 0) != ccw:
                continue  # a fold, not a real piece
            if _far_enough(loop, ring, d, tol):
                loops.append(loop)
    return loops


class Region(object):
    """A set of loops with an index for quick inside tests and edge look-ups.

    A point is inside when it is inside an outline (counter-clockwise loop)
    and not inside any hole (clockwise loop), also where holes overlap each
    other or the outline.
    """

    def __init__(self, loops):
        self.loops = [list(loop) for loop in loops if len(loop) >= 3]
        self.ccw = [signed_area(loop) > 0 for loop in self.loops]
        self.edges = []  # (ax, ay, bx, by, loop index)
        for index, loop in enumerate(self.loops):
            n = len(loop)
            for i in range(n):
                a, b = loop[i], loop[(i + 1) % n]
                self.edges.append((a[0], a[1], b[0], b[1], index))

        self._grid = {}  # (column, row) -> edge indices
        self._rows = {}  # row -> edge indices
        self._ox = self._oy = 0.0
        self._cell = 1.0
        if not self.edges:
            return
        xs = [e[0] for e in self.edges] + [e[2] for e in self.edges]
        ys = [e[1] for e in self.edges] + [e[3] for e in self.edges]
        self._ox, self._oy = min(xs), min(ys)
        size = max(max(xs) - self._ox, max(ys) - self._oy, 1e-9)
        self._cell = size / max(1, min(200, int(math.sqrt(len(self.edges)))))
        for index, (ax, ay, bx, by, _) in enumerate(self.edges):
            cx0, cx1 = self._column(min(ax, bx)), self._column(max(ax, bx))
            for cy in range(self._row(min(ay, by)), self._row(max(ay, by)) + 1):
                self._rows.setdefault(cy, []).append(index)
                for cx in range(cx0, cx1 + 1):
                    self._grid.setdefault((cx, cy), []).append(index)

    def _column(self, x):
        return int(math.floor((x - self._ox) / self._cell))

    def _row(self, y):
        return int(math.floor((y - self._oy) / self._cell))

    def winding(self, pt, skip_loop=None):
        """How many outlines minus how many holes the point is inside.

        ``skip_loop`` leaves one loop out of the count.
        """
        px, py = pt
        total = 0
        for index in self._rows.get(self._row(py), ()):
            ax, ay, bx, by, loop = self.edges[index]
            if loop == skip_loop:
                continue
            if ay <= py:
                if by > py and (bx - ax) * (py - ay) - (px - ax) * (by - ay) > 0:
                    total += 1
            elif by <= py and (bx - ax) * (py - ay) - (px - ax) * (by - ay) < 0:
                total -= 1
        return total

    def contains(self, pt):
        return self.winding(pt) > 0

    def edges_near(self, minx, miny, maxx, maxy):
        """Indices of the edges whose bounding box touches the given box."""
        found = set()
        for cy in range(self._row(miny), self._row(maxy) + 1):
            for cx in range(self._column(minx), self._column(maxx) + 1):
                found.update(self._grid.get((cx, cy), ()))
        result = []
        for index in found:
            ax, ay, bx, by, _ = self.edges[index]
            if (max(ax, bx) < minx or min(ax, bx) > maxx
                    or max(ay, by) < miny or min(ay, by) > maxy):
                continue
            result.append(index)
        result.sort()
        return result


# --------------------------------------------------------------------------
# Cutting a convex polygon back to a region
# --------------------------------------------------------------------------

_SNAP_TOL2 = SNAP_TOL * SNAP_TOL


def _clip_segment(a, b, poly):
    """The part of segment a-b inside CCW convex ``poly``.

    Returns (start, end, start_edge, end_edge) or None. The two edge numbers
    say which edge of ``poly`` the start and the end lie on, or -1 for a point
    that is properly inside.
    """
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    t0, t1 = 0.0, 1.0
    e0 = e1 = near_a = near_b = -1
    n = len(poly)
    for j in range(n):
        qx, qy = poly[j]
        rx, ry = poly[j + 1 - n]
        nx, ny = ry - qy, qx - rx  # outward normal of this edge of poly
        num = nx * (qx - ax) + ny * (qy - ay)  # > 0: a is inside this edge
        den = nx * dx + ny * dy
        if den == 0.0:
            if num < 0:
                return None
        else:
            t = num / den
            if den > 0:
                if t < t1:
                    t1, e1 = t, j
            elif t > t0:
                t0, e0 = t, j
            if t0 >= t1:
                return None
        # An end that is inside but touches this edge also counts as on it.
        limit = _SNAP_TOL2 * (nx * nx + ny * ny)
        if num >= 0 and num * num <= limit:
            near_a = j
        num_b = num - den
        if num_b >= 0 and num_b * num_b <= limit:
            near_b = j
    if t0 <= 1e-12:
        t0 = 0.0
    if t1 >= 1.0 - 1e-12:
        t1 = 1.0
    if t1 - t0 <= 1e-12:
        return None
    start = a if t0 == 0.0 else (ax + t0 * dx, ay + t0 * dy)
    end = b if t1 == 1.0 else (ax + t1 * dx, ay + t1 * dy)
    return start, end, (e0 if e0 >= 0 else near_a), (e1 if e1 >= 0 else near_b)


def _position_on_segment(pt, a, b, tol=SNAP_TOL):
    """How far along a-b the point sits (0..1), or None if it is not on it."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return None
    s = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / length2
    slack = tol / math.sqrt(length2)
    if s < -slack or s > 1.0 + slack:
        return None
    if math.hypot(pt[0] - (a[0] + s * dx), pt[1] - (a[1] + s * dy)) > tol:
        return None
    return max(0.0, min(1.0, s))


class _Snapper(object):
    """Gives points that are (almost) equal one shared value."""

    def __init__(self, tol=SNAP_TOL):
        self._tol = tol
        self._size = 4.0 * tol
        self._cells = {}
        self._known = {}  # exact value -> shared value, the common case

    def __call__(self, pt):
        shared = self._known.get(pt)
        if shared is not None:
            return shared
        shared = pt
        kx = int(math.floor(pt[0] / self._size))
        ky = int(math.floor(pt[1] / self._size))
        for cx in (kx - 1, kx, kx + 1):
            for cy in (ky - 1, ky, ky + 1):
                for other in self._cells.get((cx, cy), ()):
                    if (abs(other[0] - pt[0]) <= self._tol
                            and abs(other[1] - pt[1]) <= self._tol):
                        shared = other
        if shared is pt:
            self._cells.setdefault((kx, ky), []).append(pt)
        self._known[pt] = shared
        return shared


def _stitch(pieces):
    """Join directed pieces (start, end) into closed loops, or None."""
    outgoing = {}
    for index, (start, _) in enumerate(pieces):
        outgoing.setdefault(start, []).append(index)
    used = [False] * len(pieces)
    loops = []
    for index in range(len(pieces)):
        if used[index]:
            continue
        used[index] = True
        start, current = pieces[index]
        loop = [start]
        budget = len(pieces)
        while current != start:
            loop.append(current)
            following = None
            for candidate in outgoing.get(current, ()):
                if not used[candidate]:
                    following = candidate
                    break
            if following is None or budget <= 0:
                return None
            budget -= 1
            used[following] = True
            current = pieces[following][1]
        loop = clean_polygon(loop)
        if loop:
            loops.append(loop)
    return loops


def clip_to_region(poly, region):
    """The parts of CCW convex ``poly`` that lie inside ``region``.

    Returns a list of loops: counter-clockwise outlines, plus a clockwise loop
    for every hole of the region that lies wholly inside ``poly``. Returns
    None if the pieces could not be joined up (not expected).

    How: take the region's edges that pass through ``poly`` and the edges of
    ``poly`` itself, cut them all where they meet, keep every piece that has
    "inside both" on its left and "not inside both" on its right, and walk
    the kept pieces head to tail.
    """
    if not poly:
        return []
    minx, miny, maxx, maxy = bounding_box(poly)
    near = region.edges_near(minx, miny, maxx, maxy)
    if not near:
        return [list(poly)] if region.contains(centroid(poly)) else []

    n = len(poly)
    on_edge = [[] for _ in range(n)]  # where region edges touch poly's outline

    def touches(pt, j):
        a, b = poly[j], poly[j + 1 - n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        s = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / (dx * dx + dy * dy)
        on_edge[j].append((max(0.0, min(1.0, s)), pt))

    # Region edges, cut down to the part inside poly.
    inner = []  # [start, end, loop index, crossings with other loops]
    loops_inside = set()
    for index in near:
        ax, ay, bx, by, loop = region.edges[index]
        clipped = _clip_segment((ax, ay), (bx, by), poly)
        if clipped:
            start, end, start_edge, end_edge = clipped
            inner.append([start, end, loop, None])
            loops_inside.add(loop)
            if start_edge >= 0:
                touches(start, start_edge)
            if end_edge >= 0:
                touches(end, end_edge)

    # Loops of the region can cross each other (a grown hole over the outline).
    several_loops = len(loops_inside) > 1
    if several_loops:
        boxes = [(min(p[0][0], p[1][0]), min(p[0][1], p[1][1]),
                  max(p[0][0], p[1][0]), max(p[0][1], p[1][1])) for p in inner]
        for i in range(len(inner)):
            first = inner[i]
            x0, y0, x1, y1 = boxes[i]
            for j in range(i + 1, len(inner)):
                second = inner[j]
                if first[2] == second[2]:
                    continue
                box = boxes[j]
                if box[2] < x0 or box[0] > x1 or box[3] < y0 or box[1] > y1:
                    continue
                hit = _segment_intersection(first[0], first[1], second[0], second[1])
                if hit:
                    for piece, t in ((first, hit[0]), (second, hit[1])):
                        if piece[3] is None:
                            piece[3] = []
                        piece[3].append((t, hit[2]))

    kept = []  # the pieces that bound "inside poly and inside region"

    # Edges of the region. One is a real border only where the other loops do
    # not already decide the matter: no other outline around it, or exactly
    # one for a hole. With a single loop inside poly, no other loop's edge
    # runs through poly, so that answer is the same for all its pieces.
    # With several loops the answer can only change where two loops cross, so
    # along an unbroken run of one loop's edges it is carried over as well.
    keep = None
    run_loop = run_end = None
    for start, end, loop, crossings in inner:
        points = [start, end]
        if crossings:
            points = [start] + [pt for _, pt in sorted(crossings)] + [end]
        for k in range(len(points) - 1):
            p, q = points[k], points[k + 1]
            carried = keep is not None and (
                not several_loops
                or (k == 0 and loop == run_loop and p == run_end))
            if not carried:
                others = region.winding(((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0),
                                        skip_loop=loop)
                keep = others == (0 if region.ccw[loop] else 1)
            if keep:
                kept.append((p, q))
        # A piece that was crossed ends the run: the answer may differ after it.
        run_loop, run_end = (loop, end) if not crossings else (None, None)

    # Edges of poly itself: kept where they run through the region.
    for j in range(n):
        points = [poly[j], poly[j + 1 - n]]
        if on_edge[j]:
            points = [poly[j]] + [pt for _, pt in sorted(on_edge[j])] + [poly[j + 1 - n]]
        for k in range(len(points) - 1):
            p, q = points[k], points[k + 1]
            if region.winding(((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)) > 0:
                kept.append((p, q))

    snap = _Snapper()
    joined = []
    for p, q in kept:
        p, q = snap(p), snap(q)
        if p != q:
            joined.append((p, q))
    return _stitch(joined)


# --------------------------------------------------------------------------
# Seed points and Voronoi cells
# --------------------------------------------------------------------------

def random_points(region, bbox, count, rng, density=None, density_max=1.0):
    """``count`` random points inside the region (fewer if it is hard to hit).

    ``density``, if given, is a function of a point that says how crowded the
    points should be there, from 0 up to ``density_max``. A spot with twice
    the density gets twice as many points per area.
    """
    minx, miny, maxx, maxy = bbox
    points = []
    tries = 0
    max_tries = max(1000, count * 200) * (1 if density is None else int(density_max) + 1)
    while len(points) < count and tries < max_tries:
        tries += 1
        pt = (rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if not region.contains(pt):
            continue
        if density is not None and rng.random() * density_max > density(pt):
            continue  # thinned out here
        points.append(pt)
    return points


def voronoi_cells(points, bbox):
    """Voronoi cell of every point, clipped to ``bbox``.

    Each cell starts as the bounding box and is cut by the bisector line
    between its point and each neighbour, nearest first. Once a neighbour is
    more than twice as far away as the cell's farthest corner, no later
    neighbour can cut the cell, so the loop stops early.

    Returns one CCW convex polygon per point ([] for a duplicated point).
    """
    minx, miny, maxx, maxy = bbox
    box = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    n = len(points)
    cells = []
    for i in range(n):
        px, py = points[i]
        order = sorted(
            ((points[j][0] - px) ** 2 + (points[j][1] - py) ** 2, j)
            for j in range(n) if j != i)
        cell = box
        for dist2, j in order:
            if dist2 < 1e-18:
                if j < i:  # exact duplicate: only the first copy gets a cell
                    cell = []
                    break
                continue
            reach2 = max((vx - px) ** 2 + (vy - py) ** 2 for vx, vy in cell)
            if dist2 > 4.0 * reach2:
                break
            qx, qy = points[j]
            cell = clip_halfplane(
                cell, qx - px, qy - py,
                (qx * qx + qy * qy - px * px - py * py) / 2.0)
            if len(cell) < 3:
                cell = []
                break
        cells.append(clean_polygon(cell))
    return cells


def _loops_centroid(loops):
    """Centroid of a shape given as outlines and holes, or None if empty."""
    total = cx = cy = 0.0
    for loop in loops:
        area = signed_area(loop)
        x, y = centroid(loop)
        total += area
        cx += area * x
        cy += area * y
    if total <= 1e-12:
        return None
    return (cx / total, cy / total)


def _weighted_centroid(loops, weight):
    """Centre of a shape when some parts of it count for more than others.

    Each loop is cut into triangles from its own centre; a triangle counts
    for its area times ``weight`` at its middle. None if nothing is left.
    """
    total = cx = cy = 0.0
    for loop in loops:
        mx, my = centroid(loop)
        n = len(loop)
        for k in range(n):
            ax, ay = loop[k]
            bx, by = loop[(k + 1) % n]
            # Signed: clockwise loops (holes) come off.
            area = ((ax - mx) * (by - my) - (bx - mx) * (ay - my)) / 2.0
            gx, gy = (mx + ax + bx) / 3.0, (my + ay + by) / 3.0
            share = area * weight((gx, gy))
            total += share
            cx += share * gx
            cy += share * gy
    if total <= 1e-12:
        return None
    return (cx / total, cy / total)


def lloyd_relax(points, region, iterations, bbox, weight=None):
    """Even out the points: move each to the centre of the part of its cell
    that lies inside the region, ``iterations`` times.

    With ``weight`` (a function of a point) each point moves to the weighted
    centre of its cell instead. Points then drift towards where the weight is
    high and end up closer together there.
    """
    points = list(points)
    for _ in range(iterations):
        cells = voronoi_cells(points, bbox)
        moved = []
        for point, cell in zip(points, cells):
            target = None
            if cell:
                parts = clip_to_region(cell, region)
                if parts and weight is None:
                    target = _loops_centroid(parts)
                elif parts:
                    target = _weighted_centroid(parts, weight)
            if target is not None and region.contains(target):
                moved.append(target)
            else:
                moved.append(point)
        points = moved
    return points


# --------------------------------------------------------------------------
# Growing an outline back, with round corners
# --------------------------------------------------------------------------

def grow_loop(loop, radius, small_turn=SMALL_TURN):
    """Outline of ``loop`` moved outward by ``radius``.

    The loop has its inside on the left (a counter-clockwise outline). Corners
    that stick out become arcs of exactly ``radius`` centred on the corner.
    Corners that point inward stay sharp, and corners that turn less than
    ``small_turn`` get a single point instead of a tiny arc; that point lies
    on the arc, so the result is never bigger than the exact shape.

    Returns a closed list of segments, in order:

        ('line', start, end)
        ('arc', start, mid, end)     # counter-clockwise

    With ``radius`` <= 0 the result is just the loop's edges.
    """
    n = len(loop)
    if radius <= 0:
        return [('line', loop[i], loop[(i + 1) % n]) for i in range(n)]

    dirs = []
    for i in range(n):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        dirs.append((dx / length, dy / length))

    corners = []  # (arc or None, point where the corner starts, where it ends)
    for i in range(n):
        vx, vy = loop[i]
        d1x, d1y = dirs[i - 1]
        d2x, d2y = dirs[i]
        n1x, n1y = d1y, -d1x  # outward normals of the two edges
        n2x, n2y = d2y, -d2x
        turn = math.atan2(d1x * d2y - d1y * d2x, d1x * d2x + d1y * d2y)
        if turn >= small_turn:
            start = (vx + radius * n1x, vy + radius * n1y)
            end = (vx + radius * n2x, vy + radius * n2y)
            angle = math.atan2(n1y, n1x) + turn / 2.0
            mid = (vx + radius * math.cos(angle), vy + radius * math.sin(angle))
            corners.append((('arc', start, mid, end), start, end))
            continue
        sx, sy = n1x + n2x, n1y + n2y
        if turn > 0:
            scale = radius / max(math.hypot(sx, sy), 1e-12)  # on the arc
        else:
            scale = radius / max(1.0 + n1x * n2x + n1y * n2y, 1e-6)  # mitre
        point = (vx + scale * sx, vy + scale * sy)
        corners.append((None, point, point))

    path = []
    for i in range(n):
        arc, _, leave = corners[i]
        if arc:
            path.append(arc)
        path.append(('line', leave, corners[(i + 1) % n][1]))
    return path


def rounded_path(core, radius):
    """``grow_loop`` under its older name (kept for a convex ``core``)."""
    return grow_loop(core, radius)


def path_points(path, arc_steps=8):
    """Flatten a path from ``grow_loop`` into a polygon (arcs sampled)."""
    pts = []
    for seg in path:
        if seg[0] == 'line':
            pts.append(seg[1])
            continue
        _, start, mid, end = seg
        centre = _circle_centre(start, mid, end)
        if centre is None:
            pts.extend([start, mid])
            continue
        cx, cy = centre
        r = math.hypot(start[0] - cx, start[1] - cy)
        a0 = math.atan2(start[1] - cy, start[0] - cx)
        a1 = math.atan2(end[1] - cy, end[0] - cx)
        sweep = (a1 - a0) % (2.0 * math.pi)
        for k in range(arc_steps):
            ang = a0 + sweep * k / arc_steps
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _circle_centre(a, b, c):
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-18:
        return None
    a2 = a[0] ** 2 + a[1] ** 2
    b2 = b[0] ** 2 + b[1] ** 2
    c2 = c[0] ** 2 + c[1] ** 2
    return ((a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
            (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d)


# --------------------------------------------------------------------------
# Stitching face edges into loops
# --------------------------------------------------------------------------

def chain_polylines(chains):
    """Join edge polylines (any order, any direction) into one closed loop.

    ``chains`` is a list of point lists, one per edge of a face loop. The
    result has no repeated closing point. Works for points of any dimension.
    """
    def dist(p, q):
        return math.sqrt(sum((u - v) ** 2 for u, v in zip(p, q)))

    remaining = [list(c) for c in chains if len(c) >= 2]
    if not remaining:
        return []
    loop = remaining.pop(0)
    while remaining:
        tail = loop[-1]
        best = None
        for index, chain in enumerate(remaining):
            for flipped, end in ((False, chain[0]), (True, chain[-1])):
                gap = dist(tail, end)
                if best is None or gap < best[0]:
                    best = (gap, index, flipped)
        _, index, flipped = best
        chain = remaining.pop(index)
        if flipped:
            chain.reverse()
        loop.extend(chain[1:])
    if len(loop) > 1 and dist(loop[0], loop[-1]) < MERGE_TOL:
        loop.pop()
    return loop


# --------------------------------------------------------------------------
# The whole pattern
# --------------------------------------------------------------------------

class Pattern(object):
    """Result of :func:`build_pattern` and :func:`build_pattern_for_share`."""

    def __init__(self):
        self.paths = []         # one closed segment list per pocket
        self.seed_count = 0     # points actually placed
        self.dropped = 0        # cells that gave no pocket (too small)
        self.face_area = 0.0    # area of the face (holes not counted)
        self.pocket_area = 0.0  # total area of all pockets
        # The values the pattern was made with: count, wall, radius, min_width.
        self.settings = {}
        self.cell_pockets = []  # per cell of the layout: pockets it gave
        self.path_cells = []    # per path: the cell it came from
        self.pocketed = None    # stress with the pockets cut, if it was checked

    @property
    def share(self):
        """Part of the face that is pocket, 0..1."""
        return self.pocket_area / self.face_area if self.face_area > 0 else 0.0


def path_area(path):
    """Exact area inside a path from ``grow_loop``."""
    area = signed_area([seg[1] for seg in path])
    for seg in path:
        if seg[0] != 'arc':
            continue
        _, start, mid, end = seg
        centre = _circle_centre(start, mid, end)
        if centre is None:
            continue
        r = math.hypot(start[0] - centre[0], start[1] - centre[1])
        sweep = (math.atan2(end[1] - centre[1], end[0] - centre[0])
                 - math.atan2(start[1] - centre[1], start[0] - centre[0])) % (2.0 * math.pi)
        area += 0.5 * r * r * (sweep - math.sin(sweep))  # arc beyond its chord
    return area


def _split_around_holes(cell, grown_holes, shrink, depth=0):
    """Split a cell in two through any hole that would sit inside its pocket.

    A pocket that surrounds a hole would be a ring. Cutting the cell through
    the hole gives two ordinary pockets with a wall between them instead.
    """
    inner = inset_convex(cell, shrink) if depth < 8 else []
    if inner:
        minx, miny, maxx, maxy = bounding_box(inner)
        for hole in grown_holes:
            hx0, hy0, hx1, hy1 = bounding_box(hole)
            if hx0 <= minx or hy0 <= miny or hx1 >= maxx or hy1 >= maxy:
                continue
            if not all(_inside_convex(pt, inner) for pt in hole):
                continue
            cx, cy = centroid(hole)
            x0, y0, x1, y1 = bounding_box(cell)
            # Cut across the cell's longer side.
            a, b = (1.0, 0.0) if x1 - x0 >= y1 - y0 else (0.0, 1.0)
            c = a * cx + b * cy
            parts = []
            for half in (clean_polygon(clip_halfplane(cell, a, b, c)),
                         clean_polygon(clip_halfplane(cell, -a, -b, -c))):
                if half:
                    parts.extend(_split_around_holes(half, grown_holes, shrink, depth + 1))
            return parts
    return [cell]


def _pockets_for(cell, core_area, wide_area, wall, radius, half_min):
    """The pocket outlines for one (part of a) Voronoi cell."""
    shrunk = inset_convex(cell, wall / 2.0 + radius)
    if not shrunk:
        return []
    cores = clip_to_region(shrunk, core_area)
    if not cores or any(signed_area(core) < 0 for core in cores):
        return []  # nothing left, or a ring-shaped pocket that could not be split

    if wide_area is not None:
        # Keep a piece only if a circle of the minimum pocket width fits in it.
        wide_cell = inset_convex(cell, wall / 2.0 + half_min)
        wide = clip_to_region(wide_cell, wide_area) if wide_cell else []
        wide = [loop for loop in (wide or []) if signed_area(loop) > 0]
        cores = [core for core in cores
                 if any(point_in_polygon(loop[0], core) for loop in wide)]
    return [grow_loop(core, radius) for core in cores]


def build_pattern(outer, holes, count, seed=1, wall=0.2, margin=0.3,
                  radius=0.1, min_width=0.2, relax=2):
    """Generate the pocket outlines for one face.

    outer      outline of the face
    holes      outlines of the holes in the face
    count      number of Voronoi cells to aim for
    seed       random seed; the same seed gives the same pattern
    wall       width of the rib left between two pockets
    margin     solid band kept along the face edge and around holes
    radius     corner radius of every pocket (0 = sharp)
    min_width  pockets narrower than this are left out
    relax      Lloyd smoothing passes (0 = raw random cells)

    Pockets next to an edge or a hole follow it at exactly ``margin``, curved
    edges and inward corners included. See the top of this file for how.
    """
    layout = _Layout(outer, holes, margin)
    layout.place(count, seed, relax)
    return _fill(layout, wall, radius, min_width)


class _Layout(object):
    """A face, its usable area and the Voronoi cells laid out on it.

    Everything here depends only on the face, the margin and the seed points.
    Wall thickness and corner radius come in later (``_fill``), so they can be
    tried with several values without redoing this part.
    """

    def __init__(self, outer, holes, margin):
        self.margin = max(margin, 0.0)
        self.rings = []
        self.cells = []
        self.points = []  # the cells' centres; cell k is all that is nearest to point k
        self.seed_count = 0
        self._areas = {}
        outer = clean_polygon(outer)
        if outer:
            self.rings.append(ensure_ccw(outer))
            for hole in holes:
                hole = clean_polygon(hole)
                if hole:
                    self.rings.append(ensure_cw(hole))
        # Holes run clockwise, so their areas are negative and come off.
        self.face_area = sum(signed_area(ring) for ring in self.rings)

    def area(self, extra=0.0):
        """The face shrunk by the margin plus ``extra``, as a Region."""
        key = round(extra, 9)
        if key not in self._areas:
            loops = offset_region(self.rings, self.margin + extra) if self.rings else []
            self._areas[key] = Region(loops)
        return self._areas[key]

    def usable_area(self):
        """Area inside the border band (rough where grown holes overlap)."""
        return sum(signed_area(loop) for loop in self.area().loops)

    def place(self, count, seed, relax, density=None, density_max=1.0):
        """Scatter ``count`` points, even them out, and build their cells.

        ``density`` (a function of a point, between 0 and ``density_max``)
        makes the cells uneven on purpose: where it is 4 times higher there
        are 4 times as many cells per area, so cells half as wide.
        """
        usable = self.area()
        if not usable.loops:
            return  # the margin swallows the whole face
        minx, miny, maxx, maxy = bounding_box(self.rings[0])
        pad = 0.01 * max(maxx - minx, maxy - miny)
        bbox = (minx - pad, miny - pad, maxx + pad, maxy + pad)
        points = random_points(usable, bbox, count, random.Random(seed),
                               density, density_max)
        self.seed_count = len(points)
        if points:
            weight = None
            if density is not None:
                # Evening out pulls the spacing back towards even. Weighting
                # by the density squared is what keeps the points spread as
                # the density itself asks.
                def weight(pt):
                    value = density(pt)
                    return value * value
            points = lloyd_relax(points, usable, relax, bbox, weight)
            self.points = points
            self.cells = voronoi_cells(points, bbox)


def _fill(layout, wall, radius, min_width, walls=None):
    """Turn the cells of a layout into pockets.

    ``walls``, if given, holds a wall thickness for every cell and is used
    instead of ``wall``. Each cell gives up half of its own thickness on all
    sides, so the rib between two cells is the average of their two values.
    """
    result = Pattern()
    result.face_area = layout.face_area
    result.seed_count = layout.seed_count
    wall = max(wall, 0.0)
    radius = max(radius, 0.0)
    half_min = max(min_width, 0.0) / 2.0
    result.settings = {'count': layout.seed_count, 'wall': wall,
                       'radius': radius, 'min_width': 2.0 * half_min}
    if walls is not None:
        walls = [max(value, 0.0) for value in walls]
        result.settings['wall'] = min(walls) if walls else 0.0
        result.settings['wall_max'] = max(walls) if walls else 0.0

    core_area = layout.area(radius)
    if not layout.cells or not core_area.loops:
        return result
    wide_area = layout.area(half_min) if half_min > radius else None

    grown_holes = [loop for loop, ccw in zip(core_area.loops, core_area.ccw) if not ccw]
    result.cell_pockets = [0] * len(layout.cells)
    for index, cell in enumerate(layout.cells):
        if not cell:
            result.dropped += 1
            continue
        if walls is not None:
            wall = walls[index]
        for part in _split_around_holes(cell, grown_holes, wall / 2.0 + radius):
            pockets = _pockets_for(part, core_area, wide_area, wall, radius, half_min)
            if pockets:
                result.paths.extend(pockets)
                result.path_cells.extend([index] * len(pockets))
                result.cell_pockets[index] += len(pockets)
            else:
                result.dropped += 1
    result.pocket_area = sum(path_area(path) for path in result.paths)
    return result


# --------------------------------------------------------------------------
# Simple mode: only a border margin and how much to remove
# --------------------------------------------------------------------------

SIMPLE_MIN_CELLS = 5
SIMPLE_MAX_CELLS = 120
SIMPLE_RADIUS = 0.08     # corner radius, as a part of the cell size
SIMPLE_MIN_WIDTH = 0.25  # smallest pocket kept, as a part of the cell size
SIMPLE_MIN_WALL = 0.02   # thinnest wall tried, as a part of the cell size
SIMPLE_CLOSE = 0.002     # "reached" means within this part of the face area


def _walls_for_area(layout, usable, wanted):
    """Pockets on a layout with the wall thickness that gives ``wanted`` area.

    Thicker walls always mean less pocket. Each guess comes from a simple
    model: a cell loses a strip of wall on every side, so the pocket area
    falls off like (1 - wall / span) squared. After every try ``span`` is
    refitted to what was actually measured, which gets within 0.05% of the
    face in three or four tries. A guess that leaves the known range is
    replaced by the range's middle, so the search cannot run away.

    If even the thinnest wall leaves too little pocket, that thinnest-wall
    pattern is returned.
    """
    size = math.sqrt(usable / layout.seed_count)
    radius = SIMPLE_RADIUS * size
    min_width = SIMPLE_MIN_WIDTH * size
    thinnest = SIMPLE_MIN_WALL * size
    best = _fill(layout, thinnest, radius, min_width)
    most = best.pocket_area
    if most <= wanted:
        return best

    low, high = thinnest, size  # too much pocket at low, too little at high
    span = size
    for _ in range(14):
        guess = thinnest + span * (1.0 - math.sqrt(wanted / most))
        edge = 0.02 * (high - low)
        if not low + edge < guess < high - edge:
            guess = (low + high) / 2.0
        trial = _fill(layout, guess, radius, min_width)
        if abs(trial.pocket_area - wanted) < abs(best.pocket_area - wanted):
            best = trial
        if abs(best.pocket_area - wanted) <= 0.0005 * layout.face_area:
            break
        if trial.pocket_area > wanted:
            low = guess
        else:
            high = guess
        part = trial.pocket_area / most
        if 0.0 < part < 1.0:
            span = (guess - thinnest) / (1.0 - math.sqrt(part))
    return best


def build_pattern_for_share(outer, holes, margin, share, seed=1, relax=3):
    """Generate pockets that take up ``share`` (0..1) of the face's area.

    Only the border margin is given. The rest is chosen here:

    * Wall thickness: whatever makes the pockets cover ``share`` of the face
      (see ``_walls_for_area``).
    * Cell count: the one for which that wall thickness comes out about as
      wide as the border margin, so ribs and border look alike. It stays
      between SIMPLE_MIN_CELLS and SIMPLE_MAX_CELLS, so for very high or very
      low shares the walls end up thinner or thicker than the border.
    * Corner radius and smallest pocket: fixed parts of the cell size.

    If the border band leaves less than ``share`` of the face, the result is
    the most that fits and its ``share`` is below the target. The chosen
    values are in ``settings``, with the target under ``'target'``.
    """
    layout = _Layout(outer, holes, margin)
    usable = layout.usable_area() if layout.rings else 0.0
    if layout.face_area <= 0 or usable <= 0:
        return _fill(layout, 0.0, 0.0, 0.0)  # an empty pattern
    share = min(max(share, 0.01), 0.99)
    wanted = share * layout.face_area
    close = SIMPLE_CLOSE * layout.face_area

    # First guess. With square-ish cells of size s and walls w, the pockets
    # fill about (1 - w/s)^2 of the usable area. Setting w = margin gives s,
    # and s gives the number of cells.
    count = 60
    if layout.margin > 0:
        part = min(wanted / usable, 0.97)
        size = layout.margin / (1.0 - math.sqrt(part))
        count = int(round(usable / (size * size)))

    best = None
    tried = set()
    for _ in range(6):
        count = max(SIMPLE_MIN_CELLS, min(SIMPLE_MAX_CELLS, count))
        if count in tried:
            break
        tried.add(count)
        layout.place(count, seed, relax)
        if not layout.cells:
            break
        trial = _walls_for_area(layout, usable, wanted)
        reached = abs(trial.pocket_area - wanted) <= close
        wall = trial.settings['wall']

        if best is None:
            best = trial
        else:
            best_reached = abs(best.pocket_area - wanted) <= close
            if reached and best_reached:
                # Both hit the target: take the walls closest to the border.
                if abs(wall - layout.margin) < abs(best.settings['wall'] - layout.margin):
                    best = trial
            elif reached or (not best_reached
                             and trial.pocket_area > best.pocket_area + close):
                # Out of reach either way: fewer cells must gain something
                # real to be worth the coarser pattern.
                best = trial

        if not reached:
            count = int(count * 0.6)  # too little pocket: fewer, bigger cells
        elif layout.margin <= 0 or abs(wall - layout.margin) <= 0.1 * layout.margin:
            break
        else:
            # Wall thickness goes with cell size, and cell size with
            # 1 / sqrt(count).
            count = int(round(count * (wall / layout.margin) ** 2))

    if best is None:
        return _fill(layout, 0.0, 0.0, 0.0)
    if abs(best.pocket_area - wanted) > close and best.settings['count'] < 12:
        # Out of reach. A handful of huge cells gains almost nothing over a
        # dozen, and a dozen looks better.
        layout.place(12, seed, relax)
        if layout.cells:
            trial = _walls_for_area(layout, usable, wanted)
            if trial.pocket_area >= best.pocket_area - 0.005 * layout.face_area:
                best = trial
    best.settings['target'] = share
    return best
