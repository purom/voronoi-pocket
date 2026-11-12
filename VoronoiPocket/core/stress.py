"""Flat (2D) stress calculation for the stress-driven pattern.

Pure Python, like geometry.py: no ``adsk`` imports and no extra packages, so
it can be tested and timed outside Fusion.

What it does
------------
The face is treated as a thin flat plate that is only loaded in its own plane
("plane stress"). The plate is cut into small triangles, each triangle is
given the stiffness of the material, the held points are pinned, the forces
are put on, and the resulting set of equations is solved for how far every
corner point moves. From the movements comes the stress in every triangle.

    result = solve_plate(outer, holes, supports, loads, youngs, poisson)
    result.von_mises_at((x, y))

Units: any set that goes together. With lengths in cm and forces in N, the
stiffness has to be given in N/cm^2 and stresses come out in N/cm^2
(1 MPa = 100 N/cm^2).

Limits
------
* ``solve_plate`` gives the stress of the solid plate, before any pockets
  are cut. ``solve_pocketed`` gives that of the plate with its pockets, by a
  quicker and rougher method; its limits are written there.
* Forces out of the plate's plane and bending are not part of it.
* Accuracy. Checked against the textbook case of a pulled plate with a hole:
  a border margin away from the hole, where pockets go, the result is within
  about 5% of theory. Right on the hole's rim, where stress peaks at 3 times
  the far value, it reads 2.4 times with the default mesh. The simple triangle
  used here spreads a sharp peak over its neighbours. So ``peak`` is good for
  steering wall thickness, and not for deciding whether a part is strong
  enough.
* Speed. Plain Python solves about 2,500 points in 2 to 3 seconds, and the
  time grows faster than the number of points. A finer mesh is where a
  compiled helper would pay off.
"""

import math
import operator
import time

from . import geometry


class StressError(Exception):
    """A problem with the inputs that the user can fix."""


# --------------------------------------------------------------------------
# Mesh: the face cut into triangles
# --------------------------------------------------------------------------

class Mesh(object):
    def __init__(self):
        self.nodes = []      # (x, y)
        self.triangles = []  # (i, j, k) node numbers, counter-clockwise
        self.on_edge = []    # per node: True if it sits on the face's edge
        self.size = 0.0      # the triangle size that was asked for
        self._buckets = None

    def area(self):
        return sum(_double_area(self.nodes[i], self.nodes[j], self.nodes[k])
                   for i, j, k in self.triangles) / 2.0

    def buckets(self):
        """(grid, square size): the triangles sorted into a grid of squares,
        for finding the ones near a spot without looking at all of them."""
        if self._buckets is None:
            cell = 2.0 * self.size
            grid = {}
            for index, (i, j, k) in enumerate(self.triangles):
                xs = (self.nodes[i][0], self.nodes[j][0], self.nodes[k][0])
                ys = (self.nodes[i][1], self.nodes[j][1], self.nodes[k][1])
                for cx in range(int(math.floor(min(xs) / cell)),
                                int(math.floor(max(xs) / cell)) + 1):
                    for cy in range(int(math.floor(min(ys) / cell)),
                                    int(math.floor(max(ys) / cell)) + 1):
                        grid.setdefault((cx, cy), []).append(index)
            self._buckets = (grid, cell)
        return self._buckets


def _double_area(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])


def _nearest_on_edges(region, pt, reach):
    """The point of the region's outline nearest to ``pt``, if within ``reach``."""
    best = None
    best_dist = reach
    px, py = pt
    for index in region.edges_near(px - reach, py - reach, px + reach, py + reach):
        ax, ay, bx, by, _ = region.edges[index]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = 0.0
        if length2 > 0.0:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
        qx, qy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        if dist < best_dist:
            best_dist = dist
            best = (qx, qy)
    return best


def make_mesh(outer, holes, size):
    """Cut a face into triangles of about ``size``.

    A regular pattern of equal-sided triangles is laid over the face. Points
    of the pattern that are close to the face's edge are moved onto the edge,
    so the mesh follows the outline instead of being stepped. Triangles that
    end up outside the face, or squashed flat by the moving, are left out.
    """
    outer = geometry.clean_polygon(outer)
    if not outer or size <= 0:
        raise StressError('The face has no usable outline.')
    rings = [geometry.ensure_ccw(outer)]
    for hole in holes:
        hole = geometry.clean_polygon(hole)
        if hole:
            rings.append(geometry.ensure_cw(hole))
    region = geometry.Region(rings)

    minx, miny, maxx, maxy = geometry.bounding_box(rings[0])
    row_step = size * math.sqrt(3.0) / 2.0
    # A little over half the spacing: of two neighbouring pattern points on
    # opposite sides of the edge, at least one is then always moved onto it,
    # so the mesh's rim is made of moved points only and follows the edge.
    reach = 0.51 * size
    columns = int((maxx - minx) / size) + 4
    rows = int((maxy - miny) / row_step) + 4

    points = {}  # (column, row) -> (point, on edge?)
    for row in range(rows):
        y = miny - row_step + row * row_step
        shift = 0.5 * size if row % 2 else 0.0
        for column in range(columns):
            pt = (minx - size + column * size + shift, y)
            moved = _nearest_on_edges(region, pt, reach)
            if moved is not None:
                points[(column, row)] = (moved, True)
            elif region.contains(pt):
                points[(column, row)] = (pt, False)

    # Points that were moved to almost the same spot become one point.
    # Otherwise the thin triangle between them would have to be dropped,
    # which leaves a slit in the mesh that acts like a crack.
    close = 0.3 * size
    buckets = {}
    same_as = {}
    for key in sorted(points):
        pt, on_edge = points[key]
        if not on_edge:
            continue
        bx, by = int(math.floor(pt[0] / close)), int(math.floor(pt[1] / close))
        twin = None
        for cx in (bx - 1, bx, bx + 1):
            for cy in (by - 1, by, by + 1):
                for other in buckets.get((cx, cy), ()):
                    spot = points[other][0]
                    if math.hypot(spot[0] - pt[0], spot[1] - pt[1]) < close:
                        twin = other
        if twin is None:
            buckets.setdefault((bx, by), []).append(key)
        else:
            same_as[key] = twin

    # Sharp corners of the face: put the nearest edge point exactly on each,
    # so the mesh keeps the corner instead of cutting it off.
    taken = set()
    for ring in rings:
        n = len(ring)
        for index in range(n):
            ax, ay = ring[index - 1]
            bx, by = ring[index]
            cx, cy = ring[(index + 1) % n]
            turn = math.atan2((bx - ax) * (cy - by) - (by - ay) * (cx - bx),
                              (bx - ax) * (cx - bx) + (by - ay) * (cy - by))
            if abs(turn) < math.radians(25.0):
                continue
            best = None
            best_dist = 0.75 * size
            for key, (pt, on_edge) in points.items():
                if on_edge and key not in taken and key not in same_as:
                    dist = math.hypot(pt[0] - bx, pt[1] - by)
                    if dist < best_dist:
                        best_dist = dist
                        best = key
            if best is not None:
                taken.add(best)
                points[best] = ((bx, by), True)

    # Two triangles per lattice cell; which corners they use depends on
    # whether the row is shifted.
    wanted = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            a, b = (column, row), (column + 1, row)
            c, d = (column, row + 1), (column + 1, row + 1)
            if row % 2:
                wanted.append((a, d, c))
                wanted.append((a, b, d))
            else:
                wanted.append((a, b, c))
                wanted.append((b, d, c))

    full = size * size * math.sqrt(3.0) / 2.0  # twice the area of a whole triangle
    kept = []
    for corners in wanted:
        if not all(corner in points for corner in corners):
            continue
        corners = tuple(same_as.get(corner, corner) for corner in corners)
        if len(set(corners)) < 3:
            continue  # two of its corners became one point
        pa, pb, pc = (points[corner][0] for corner in corners)
        double = _double_area(pa, pb, pc)
        if double < 0.02 * full:
            continue  # flat (three points on one edge) or flipped
        centre = ((pa[0] + pb[0] + pc[0]) / 3.0, (pa[1] + pb[1] + pc[1]) / 3.0)
        if region.contains(centre):
            kept.append(corners)

    kept = _largest_piece(kept)
    if not kept:
        raise StressError('The face is too small for this mesh size.')

    mesh = Mesh()
    mesh.size = size
    numbers = {}
    for corners in kept:
        triangle = []
        for corner in corners:
            if corner not in numbers:
                numbers[corner] = len(mesh.nodes)
                mesh.nodes.append(points[corner][0])
                mesh.on_edge.append(points[corner][1])
            triangle.append(numbers[corner])
        mesh.triangles.append(tuple(triangle))
    return mesh


def _largest_piece(triangles):
    """The biggest group of triangles joined edge to edge.

    A loose bit that hangs on by one corner, or not at all, could move freely
    and would make the equations unsolvable.
    """
    by_edge = {}
    for index, (a, b, c) in enumerate(triangles):
        for edge in ((a, b), (b, c), (c, a)):
            by_edge.setdefault(frozenset(edge), []).append(index)
    seen = [False] * len(triangles)
    best = []
    for start in range(len(triangles)):
        if seen[start]:
            continue
        seen[start] = True
        group = [start]
        todo = [start]
        while todo:
            a, b, c = triangles[todo.pop()]
            for edge in ((a, b), (b, c), (c, a)):
                for other in by_edge[frozenset(edge)]:
                    if not seen[other]:
                        seen[other] = True
                        group.append(other)
                        todo.append(other)
        if len(group) > len(best):
            best = group
    return [triangles[index] for index in sorted(best)]


# --------------------------------------------------------------------------
# Picking the mesh points on a held or loaded edge
# --------------------------------------------------------------------------

def _along_polyline(pt, polyline, tol):
    """How far along ``polyline`` the point sits, or None if it is not on it."""
    travelled = 0.0
    for k in range(len(polyline) - 1):
        ax, ay = polyline[k]
        bx, by = polyline[k + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length > 0.0:
            t = max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / (length * length)))
            if math.hypot(pt[0] - (ax + t * dx), pt[1] - (ay + t * dy)) <= tol:
                return travelled + t * length
        travelled += length
    return None


def _nodes_on(mesh, polyline, tol):
    """[(distance along the polyline, node number)] for edge nodes on it, in order."""
    found = []
    for number, pt in enumerate(mesh.nodes):
        if mesh.on_edge[number]:
            where = _along_polyline(pt, polyline, tol)
            if where is not None:
                found.append((where, number))
    found.sort()
    return found


def _shares(found, closed):
    """Split 1.0 over nodes on an edge by the length of edge each one covers."""
    count = len(found)
    if count == 1:
        return [1.0]
    spans = []
    for k in range(count):
        before = found[k][0] - found[k - 1][0] if k > 0 else 0.0
        after = found[k + 1][0] - found[k][0] if k < count - 1 else 0.0
        spans.append((before + after) / 2.0)
    if closed:  # a full ring: the last node is next to the first
        gap = closed - found[-1][0] + found[0][0]
        spans[0] += gap / 2.0
        spans[-1] += gap / 2.0
    total = sum(spans)
    return [span / total for span in spans]


# --------------------------------------------------------------------------
# Stiffness, solving, stress
# --------------------------------------------------------------------------

def _dot(a, b):
    return sum(map(operator.mul, a, b))


def _conjugate_gradient(rows, diagonal, rhs, tol, limit, start=None):
    """Solve ``rows * x = rhs`` step by step (conjugate gradients).

    ``rows`` is the matrix, one list of (column, value) per row. Each step
    divides by the diagonal first, which evens out stiff and soft spots and
    cuts the number of steps. ``start`` is a first guess for x; a good one
    (the answer of a nearly equal problem) saves steps. Returns (x, steps
    taken).
    """
    n = len(rhs)
    x = [0.0] * n
    size = math.sqrt(_dot(rhs, rhs))
    if size == 0.0:
        return x, 0
    r = list(rhs)
    if start is not None:
        x = list(start)
        r = [ri - sum([value * x[column] for column, value in row])
             for ri, row in zip(rhs, rows)]
        if math.sqrt(_dot(r, r)) <= tol * size:
            return x, 0
    z = [ri / di for ri, di in zip(r, diagonal)]
    p = list(z)
    rz = _dot(r, z)
    for step in range(1, limit + 1):
        ap = [sum([value * p[column] for column, value in row]) for row in rows]
        alpha = rz / _dot(p, ap)
        x = [xi + alpha * pi for xi, pi in zip(x, p)]
        r = [ri - alpha * api for ri, api in zip(r, ap)]
        if math.sqrt(_dot(r, r)) <= tol * size:
            return x, step
        z = [ri / di for ri, di in zip(r, diagonal)]
        rz_new = _dot(r, z)
        beta = rz_new / rz
        p = [zi + beta * pi for zi, pi in zip(z, p)]
        rz = rz_new
    return x, limit


class StressResult(object):
    """What ``solve_plate`` found."""

    def __init__(self):
        self.mesh = None
        self.moves = []            # per node: (dx, dy) it moved
        self.triangle_stress = []  # per triangle: (sx, sy, sxy)
        self.node_stress = []      # per node: (sx, sy, sxy), neighbours averaged
        self.node_von_mises = []   # per node: one "how loaded" number
        self.reaction = (0.0, 0.0)  # total force the supports push back with
        self.steps = 0             # solver steps taken
        self.seconds = 0.0
        self.setup = {}            # how it was held and loaded, for solving again
        # Only for a pocketed plate (``shares`` given to solve_plate):
        self.shares = None         # per triangle: part of the material left
        self.triangle_von_mises = []  # per triangle, 0 where no material is left

    @property
    def peak(self):
        return max(self.node_von_mises) if self.node_von_mises else 0.0

    def _find(self, pt):
        """(triangle, three weights) for the triangle the point is in, or None."""
        mesh = self.mesh
        grid, cell = mesh.buckets()
        key = (int(math.floor(pt[0] / cell)), int(math.floor(pt[1] / cell)))
        nearest = None
        for index in grid.get(key, ()):
            i, j, k = mesh.triangles[index]
            a, b, c = mesh.nodes[i], mesh.nodes[j], mesh.nodes[k]
            whole = _double_area(a, b, c)
            wa = _double_area(pt, b, c) / whole
            wb = _double_area(a, pt, c) / whole
            wc = 1.0 - wa - wb
            low = min(wa, wb, wc)
            if low >= -1e-9:
                return index, (wa, wb, wc)
            if nearest is None or low > nearest[0]:
                nearest = (low, index, (wa, wb, wc))
        if nearest is None:
            return None
        # Just outside the mesh (it stops a little short of the true edge):
        # use the closest triangle, with the weights pulled back onto it.
        weights = [max(0.0, w) for w in nearest[2]]
        total = sum(weights)
        return nearest[1], tuple(w / total for w in weights)

    def von_mises_at(self, pt):
        """The "how loaded" number at a point of the face (0 off the mesh)."""
        found = self._find(pt)
        if found is None:
            return 0.0
        index, (wa, wb, wc) = found
        i, j, k = self.mesh.triangles[index]
        values = self.node_von_mises
        return wa * values[i] + wb * values[j] + wc * values[k]


def von_mises(sx, sy, sxy):
    """One number for how hard the material is loaded, from the three parts
    of a flat stress state. Yielding starts when it reaches the yield strength."""
    return math.sqrt(max(0.0, sx * sx - sx * sy + sy * sy + 3.0 * sxy * sxy))


def _point_shares(mesh, pt, reach):
    """How a force at ``pt`` is shared out over the mesh points around it.

    Returns [(node number, share)], shares adding up to 1. A force on one
    single mesh point would give a needle-sharp stress peak that depends on
    the mesh and nothing else. Spreading it over the points within ``reach``
    (more to the near ones) is closer to how a real force arrives, through a
    bolt head or a pad. Empty if the point is not on the mesh.
    """
    near = []
    closest = None
    for number, (x, y) in enumerate(mesh.nodes):
        dist = math.hypot(x - pt[0], y - pt[1])
        if dist < reach:
            near.append((number, 1.0 - dist / reach))
        if closest is None or dist < closest[0]:
            closest = (dist, number)
    if not near:
        # None within reach: fine if the point is just off the mesh's rim.
        if closest is None or closest[0] > 2.0 * reach:
            return []
        return [(closest[1], 1.0)]
    total = sum(weight for _, weight in near)
    return [(number, weight / total) for number, weight in near]


# A triangle with nothing left in it (inside a pocket cut right through)
# keeps this part of its stiffness, so the equations stay solvable.
SOFT_FLOOR = 1e-3
# Below this share a triangle or point counts as "no material here".
NO_MATERIAL = 0.02


def solve_plate(outer, holes, supports, loads, youngs, poisson,
                thickness=1.0, size=None, mesh=None, tol=1e-7, point_loads=(),
                shares=None, start=None):
    """Work out the stress in a flat plate.

    outer, holes   the face, as in geometry.build_pattern
    supports       where the plate is held: a list of polylines along its
                   edge, or of (polyline, (hold_x, hold_y)) to hold only one
                   direction
    loads          forces on it: a list of (polyline along its edge, (fx, fy));
                   the force is the total, spread evenly along the polyline
    point_loads    forces at single points: a list of ((x, y), (fx, fy)).
                   Each is spread over the mesh points within one and a half
                   triangle sizes of its point.
    youngs         stiffness of the material (Young's modulus)
    poisson        how much it narrows when stretched (Poisson's ratio)
    thickness      thickness of the plate
    size           triangle size; by default about 1/65 of the face's diagonal,
                   which gives roughly 2,000 points on a plate twice as long
                   as wide

    shares         for a plate with pockets (see ``material_shares``): for
                   every triangle of ``mesh`` the part of its material that
                   is left, 0 to 1. A triangle is made softer by that part.
                   The stresses returned are then those in the material that
                   is left, not averages over the triangle.
    start          the ``moves`` of an earlier result on the same mesh, as a
                   first guess. Saves solver steps when little has changed.

    A polyline that closes on itself (first point = last point) stands for a
    whole ring, such as a hole.
    """
    started = time.perf_counter()
    if not 0.0 <= poisson < 0.5 or youngs <= 0 or thickness <= 0:
        raise StressError('The material data is not usable for a stress calculation.')
    if mesh is None:
        if size is None:
            minx, miny, maxx, maxy = geometry.bounding_box(outer)
            size = math.hypot(maxx - minx, maxy - miny) / 65.0
        mesh = make_mesh(outer, holes, size)
    nodes = mesh.nodes
    count = len(nodes)
    tol_on = 1e-6 * max(mesh.size, 1e-9)
    if shares is not None:
        if len(shares) != len(mesh.triangles):
            raise StressError('The material shares do not belong to this mesh.')
        shares = [min(1.0, max(0.0, share)) for share in shares]

    # Held points.
    held = set()
    for support in supports:
        polyline, (hold_x, hold_y) = support, (True, True)
        if len(support) == 2 and isinstance(support[1][0], bool):
            polyline, (hold_x, hold_y) = support
        found = _nodes_on(mesh, polyline, tol_on)
        for _, number in found:
            if hold_x:
                held.add(2 * number)
            if hold_y:
                held.add(2 * number + 1)
    if len(held) < 3:
        raise StressError('The part is not held firmly enough. Hold at least one edge.')

    # Forces.
    force = [0.0] * (2 * count)
    for polyline, (fx, fy) in loads:
        found = _nodes_on(mesh, polyline, tol_on)
        if not found:
            raise StressError('A loaded edge is too short for this mesh size.')
        closed = 0.0
        if len(polyline) > 2 and math.hypot(polyline[0][0] - polyline[-1][0],
                                            polyline[0][1] - polyline[-1][1]) <= tol_on:
            closed = sum(math.hypot(polyline[k + 1][0] - polyline[k][0],
                                    polyline[k + 1][1] - polyline[k][1])
                         for k in range(len(polyline) - 1))
        for (_, number), share in zip(found, _shares(found, closed)):
            force[2 * number] += fx * share
            force[2 * number + 1] += fy * share
    for pt, (fx, fy) in point_loads:
        spread = _point_shares(mesh, pt, 1.5 * mesh.size)
        if not spread:
            raise StressError('A load point is not on the face.')
        for number, share in spread:
            force[2 * number] += fx * share
            force[2 * number + 1] += fy * share
    if not any(force):
        raise StressError('There is no force on the part.')

    # The inside of a pocket cut right through carries nothing, and leaving
    # it in makes the solving slow. So the points deep inside are taken out,
    # with the triangles that use them. One row of empty triangles stays
    # around the material, very soft (``SOFT_FLOOR``), so that a bit of
    # material that hangs on by a corner cannot swing loose.
    deep = [False] * count
    if shares is not None:
        empty = [not on_edge and not force[2 * number] and not force[2 * number + 1]
                 for number, on_edge in enumerate(mesh.on_edge)]
        for tri, share in zip(mesh.triangles, shares):
            if share >= NO_MATERIAL:
                for number in tri:
                    empty[number] = False
        deep = list(empty)
        for tri in mesh.triangles:
            if not (empty[tri[0]] and empty[tri[1]] and empty[tri[2]]):
                for number in tri:
                    deep[number] = False

    # Stiffness of every triangle, added into one big table.
    scale = youngs / (1.0 - poisson * poisson)
    d11, d12, d33 = scale, scale * poisson, scale * (1.0 - poisson) / 2.0
    table = [dict() for _ in range(2 * count)]
    shapes = []  # per triangle: (b, c, twice its area), for the stress later
    for index, tri in enumerate(mesh.triangles):
        (xi, yi), (xj, yj), (xk, yk) = nodes[tri[0]], nodes[tri[1]], nodes[tri[2]]
        b = (yj - yk, yk - yi, yi - yj)
        c = (xk - xj, xi - xk, xj - xi)
        double = xi * b[0] + xj * b[1] + xk * b[2]
        shapes.append((b, c, double))
        if deep[tri[0]] or deep[tri[1]] or deep[tri[2]]:
            continue
        factor = thickness / (2.0 * double)
        if shares is not None:
            factor *= max(SOFT_FLOOR, shares[index])
        for p in range(3):
            row_x, row_y = table[2 * tri[p]], table[2 * tri[p] + 1]
            for q in range(3):
                cx, cy = 2 * tri[q], 2 * tri[q] + 1
                row_x[cx] = row_x.get(cx, 0.0) + factor * (d11 * b[p] * b[q] + d33 * c[p] * c[q])
                row_x[cy] = row_x.get(cy, 0.0) + factor * (d12 * b[p] * c[q] + d33 * c[p] * b[q])
                row_y[cx] = row_y.get(cx, 0.0) + factor * (d12 * c[p] * b[q] + d33 * b[p] * c[q])
                row_y[cy] = row_y.get(cy, 0.0) + factor * (d11 * c[p] * c[q] + d33 * b[p] * b[q])

    # Leave the held directions out and solve for the rest.
    free = [d for d in range(2 * count) if d not in held and not deep[d // 2]]
    place = {d: n for n, d in enumerate(free)}
    rows = [[(place[col], value) for col, value in table[d].items() if col in place]
            for d in free]
    diagonal = [table[d][d] for d in free]
    guess = None
    if start is not None and len(start) == count:
        guess = [start[d // 2][d % 2] for d in free]
    solved, steps = _conjugate_gradient(rows, diagonal, [force[d] for d in free],
                                        tol, 20 * len(free) + 100, guess)
    moved = [0.0] * (2 * count)
    for d, value in zip(free, solved):
        moved[d] = value

    result = StressResult()
    result.mesh = mesh
    result.setup = dict(supports=supports, loads=loads, youngs=youngs, poisson=poisson,
                        thickness=thickness, point_loads=point_loads)
    result.steps = steps
    result.moves = [(moved[2 * n], moved[2 * n + 1]) for n in range(count)]

    # What the supports push back with: stiffness times movement, at held points.
    rx = ry = 0.0
    for d in held:
        push = sum(value * moved[col] for col, value in table[d].items()) - force[d]
        if d % 2:
            ry += push
        else:
            rx += push
    result.reaction = (rx, ry)

    # Stress per triangle (it is the same all over one triangle). In a
    # softened triangle this is the stress in the material that is left: the
    # triangle stretches as a whole, and what is left of it stretches along.
    result.shares = shares
    sums = [[0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(count)]
    around = [[] for _ in range(count)]  # per point: the triangles that use it
    centres = []
    for index, (tri, (b, c, double)) in enumerate(zip(mesh.triangles, shapes)):
        ex = ey = gxy = 0.0
        for p in range(3):
            ux, uy = moved[2 * tri[p]], moved[2 * tri[p] + 1]
            ex += b[p] * ux
            ey += c[p] * uy
            gxy += c[p] * ux + b[p] * uy
        ex, ey, gxy = ex / double, ey / double, gxy / double
        stress = (d11 * ex + d12 * ey, d12 * ex + d11 * ey, d33 * gxy)
        result.triangle_stress.append(stress)
        centres.append(((nodes[tri[0]][0] + nodes[tri[1]][0] + nodes[tri[2]][0]) / 3.0,
                        (nodes[tri[0]][1] + nodes[tri[1]][1] + nodes[tri[2]][1]) / 3.0))
        # Weight: the material in the triangle, so that a point between a
        # wall and a pocket gets the wall's stress and not a mix.
        weight = double
        if shares is not None:
            weight = double * shares[index]
            if shares[index] < NO_MATERIAL:
                weight = 0.0
            result.triangle_von_mises.append(von_mises(*stress) if weight else 0.0)
        for number in tri:
            around[number].append(index)
            total = sums[number]
            total[0] += stress[0] * weight
            total[1] += stress[1] * weight
            total[2] += stress[2] * weight
            total[3] += weight
            total[4] += double

    # Stress at the points. Inside the plate a point is surrounded by
    # triangles, and their average (weighted by area) is a good value. A
    # point on the edge only has triangles on one side, so an average reads
    # too low exactly where stress peaks (beside a hole). There a sloping
    # plane is fitted through the nearby triangles and read off at the point.
    for number in range(count):
        total = sums[number]
        if total[3] <= 0.0 or total[3] < NO_MATERIAL * total[4]:
            result.node_stress.append((0.0, 0.0, 0.0))  # inside a through pocket
            result.node_von_mises.append(0.0)
            continue
        stress = (total[0] / total[3], total[1] / total[3], total[2] / total[3])
        if mesh.on_edge[number]:
            patch = set()
            for index in around[number]:
                for corner in mesh.triangles[index]:
                    patch.update(around[corner])
            if shares is not None:  # only whole material, or the fit is a mix
                patch = [index for index in patch if shares[index] >= 0.999]
            fitted = _plane_value(nodes[number],
                                  [(centres[index], result.triangle_stress[index])
                                   for index in patch])
            if fitted is not None:
                stress = fitted
        result.node_stress.append(stress)
        result.node_von_mises.append(von_mises(*stress))

    result.seconds = time.perf_counter() - started
    return result


# --------------------------------------------------------------------------
# The plate with its pockets
# --------------------------------------------------------------------------

def pocket_cover(mesh, paths):
    """For every triangle of ``mesh``: the part of its area that the pockets
    ``paths`` (as in ``Pattern.paths``) cover, between 0 and 1, exactly."""
    nodes = mesh.nodes
    grid, square = mesh.buckets()
    covered = [0.0] * len(mesh.triangles)
    for path in paths:
        outline = geometry.path_points(path, arc_steps=4)
        if len(outline) < 3:
            continue
        if geometry.signed_area(outline) < 0:
            outline.reverse()
        minx, miny, maxx, maxy = geometry.bounding_box(outline)
        seen = set()
        for cx in range(int(math.floor(minx / square)), int(math.floor(maxx / square)) + 1):
            for cy in range(int(math.floor(miny / square)), int(math.floor(maxy / square)) + 1):
                for index in grid.get((cx, cy), ()):
                    if index in seen:
                        continue
                    seen.add(index)
                    corners = [nodes[number] for number in mesh.triangles[index]]
                    if (max(pt[0] for pt in corners) < minx or min(pt[0] for pt in corners) > maxx
                            or max(pt[1] for pt in corners) < miny
                            or min(pt[1] for pt in corners) > maxy):
                        continue
                    # The part of the pocket inside the triangle: cut the
                    # outline down by the triangle's three sides in turn.
                    part = outline
                    for k in range(3):
                        (px, py), (qx, qy) = corners[k], corners[(k + 1) % 3]
                        a, b = qy - py, px - qx
                        part = geometry.clip_halfplane(part, a, b, a * px + b * py)
                        if len(part) < 3:
                            break
                    else:
                        covered[index] += max(0.0, geometry.signed_area(part))
    cover = []
    for index, (i, j, k) in enumerate(mesh.triangles):
        area = _double_area(nodes[i], nodes[j], nodes[k]) / 2.0
        cover.append(min(1.0, covered[index] / area))
    return cover


def material_shares(mesh, paths, depth_ratio=1.0):
    """For every triangle of ``mesh``: the part of its material that is left
    once the pockets are cut, between 0 and 1.

    paths        pocket outlines, as in ``Pattern.paths``
    depth_ratio  pocket depth divided by the plate thickness. 1 or more is a
                 cut right through; at 0.6 a floor of 40% stays under every
                 pocket and still carries load.

    A triangle fully inside a pocket keeps ``1 - depth_ratio``, one fully in a
    wall keeps 1, and one that a pocket's outline runs through gets a value
    in between, by the exact area the pocket covers of it.
    """
    ratio = min(1.0, max(0.0, depth_ratio))
    return [1.0 - ratio * part for part in pocket_cover(mesh, paths)]


def solve_pocketed(field, paths, depth_ratio=1.0, start=None, tol=1e-5):
    """The stress in the plate of ``field`` once the pockets ``paths`` are cut.

    Same mesh, same supports and forces as ``field`` (the solid plate); every
    triangle is made softer by the part of its material the pockets take
    away. Returns a StressResult whose stresses are those in the material
    that is left.

    How good it is depends on how wide the walls are next to the triangles
    of the mesh. Measured against a mesh of the real pocketed shape, fine
    enough for the walls, on a pulled plate cut right through (the hardest
    case, since the walls then carry everything and bend):

    * Walls 1.6 triangles wide or more: the highest value read in a cell's
      walls is about the fine mesh's "nearly highest" value there (the level
      that 95% of the wall material stays under), mostly within 30%.
    * Thinner walls read low, evenly so: at 1.1 triangles 0.8 of that value,
      at 0.8 triangles 0.65, at 0.6 triangles 0.5. ``thin_wall_factor``
      undoes this.
    * The sharp peaks in the rounded pocket corners are not seen at all: the
      single highest value of the fine mesh is about 1.5 times the "nearly
      highest" one.
    * The plate as a whole comes out too stiff, by 10% to 100% as the walls
      get thinner.

    Good enough to find walls that are too thin and to compare one pattern
    with another. Not a strength proof.
    """
    setup = field.setup
    shares = material_shares(field.mesh, paths, depth_ratio)
    return solve_plate(None, [], setup['supports'], setup['loads'], setup['youngs'],
                       setup['poisson'], thickness=setup['thickness'], mesh=field.mesh,
                       tol=tol, point_loads=setup['point_loads'], shares=shares,
                       start=field.moves if start is None else start)


def thin_wall_factor(wall, triangle, depth_ratio=1.0):
    """What to multiply a stress read in a wall by, to make up for a wall
    that is thin next to the mesh's triangles (see ``solve_pocketed``).

    Fitted to the measurements there: the reading is 0.25 + 0.47 * (wall /
    triangle size) of the true value, up to 1. It was measured down to walls
    0.57 triangles wide and is held at that value (a factor of 2) below.

    The measurements are for cuts right through. A floor under the pockets
    carries load of its own and keeps the walls from bending, so less is
    missed. How much less is not measured; the correction is taken in
    proportion to the depth, which is a cautious guess.
    """
    if triangle <= 0.0 or wall <= 0.0:
        return 1.0
    reading = min(1.0, 0.25 + 0.47 * max(0.57, wall / triangle))
    return 1.0 + (1.0 / reading - 1.0) * min(1.0, max(0.0, depth_ratio))


def cell_points(mesh, layout):
    """For every cell of ``layout``: the mesh points whose stress counts for it.

    These are the points inside the border band that are nearest to the
    cell's centre. A point in a wall, about as near to a second centre, counts
    for both cells, since the wall belongs to both. Points in the border band
    itself count for no cell: its width is the margin, which the wall
    thickness of a cell does not change.
    """
    usable = layout.area()
    seeds = layout.points
    members = [[] for _ in seeds]
    if not seeds or not usable.loops:
        return members
    for number, (x, y) in enumerate(mesh.nodes):
        if not usable.contains((x, y)):
            continue
        distances = [math.hypot(x - sx, y - sy) for sx, sy in seeds]
        reach = min(distances) + mesh.size
        for cell, distance in enumerate(distances):
            if distance <= reach:
                members[cell].append(number)
    return members


# The loop aims a little under the allowed stress, so that a cell that has
# settled is on the safe side of the limit and not hovering around it.
LOOP_AIM = 0.95
# How strongly a cell's material is changed for the stress found in it. The
# stress in thin walls falls faster than the material grows (they bend), so
# the full change would overshoot. A cell under the aim is thinned with more
# care still, and only when it is below LOOP_IDLE of the aim: thinning one
# cell loads its neighbours.
LOOP_THICKENING = 0.7
LOOP_IDLE = 0.85
LOOP_THINNING = 0.4
# The loop has settled when no wall changes by more than this part of its
# cell's size.
LOOP_SETTLED = 0.01


def _wall_for_keep(keep, cell_size, thinnest):
    """Wall thickness that leaves the part ``keep`` of a cell's material."""
    if keep >= 1.0:
        return max(thinnest, 2.0 * cell_size)
    return max(thinnest, cell_size * (1.0 - math.sqrt(1.0 - max(0.0, keep))))


def _keep_for_wall(wall, cell_size):
    if cell_size <= 0.0 or wall >= cell_size:
        return 1.0
    return 1.0 - (1.0 - wall / cell_size) ** 2


def refine_pattern(layout, field, pattern, allow, rounds, depth_ratio=1.0,
                   wall_min=0.1, radius=0.1, min_width=0.2):
    """Check a stress pattern on the pocketed plate and correct it.

    Each round: work out the stress with the pockets cut (``solve_pocketed``),
    read the highest stress in every cell's walls, and change the walls. A
    cell over the allowed stress gets thicker walls, one under it thinner
    ones, never thinner than ``wall_min``. (The stress read is corrected for
    walls that are thin next to the mesh, see ``thin_wall_factor``.) The cells themselves stay where
    they are. After the last correction the result is checked once more, so
    ``rounds`` corrections take ``rounds`` + 1 stress calculations. It stops
    early when no pocket is over the limit and the walls have stopped
    changing.

    Returns the best pattern that was checked: one with pockets before one
    without, then the one with the fewest cells over the limit, and of those
    the one with the most pocket area. So more rounds never give a worse
    answer than fewer. Its ``settings`` get:

        rounds        corrections made before the pattern returned
        checks        stress calculations made
        over_limit    cells with a pocket whose walls are over ``allow``
        cell_levels   per cell, highest wall stress as a part of ``allow``
        wall_peak     highest stress in the walls of cells with pockets
        plate_peak    highest stress anywhere in the pocketed plate
        history       per check: (cells over the limit, wall_peak, pocket area)

    and ``pattern.pocketed`` is the StressResult of the pocketed plate.
    """
    started = time.perf_counter()
    sizes = pattern.settings['cell_sizes']
    walls = list(pattern.settings['walls'])
    members = cell_points(field.mesh, layout)
    first = dict(pattern.settings)
    # Per cell, what the rounds so far have shown: the most material at which
    # it was still over the limit, and the least at which it was within it.
    # A correction that would step past one of these goes halfway instead, so
    # a cell cannot keep swinging between too thin and too thick.
    over_at = [0.0] * len(walls)
    fine_at = [2.0] * len(walls)
    start = None
    best = None
    history = []
    checks = 0
    for round_number in range(max(0, rounds) + 1):
        pocketed = solve_pocketed(field, pattern.paths, depth_ratio, start)
        start = pocketed.moves
        checks += 1
        values = pocketed.node_von_mises
        levels = []
        for cell, numbers in enumerate(members):
            level = max([values[number] for number in numbers] or [0.0])
            if not numbers and sizes[cell] > 0.0:  # a cell smaller than a triangle
                level = pocketed.von_mises_at(layout.points[cell])
            if pattern.cell_pockets[cell]:
                level *= thin_wall_factor(walls[cell], field.mesh.size, depth_ratio)
            levels.append(level)
        with_pocket = [cell for cell, made in enumerate(pattern.cell_pockets) if made]
        over = [cell for cell in with_pocket if levels[cell] > allow * (1.0 + 1e-9)]
        wall_peak = max([levels[cell] for cell in with_pocket] or [0.0])
        history.append((len(over), wall_peak, pattern.pocket_area))
        # A round can overshoot and leave no pocket at all. Nothing is over
        # the limit then, but it is no answer while another round has pockets.
        rank = (not pattern.paths, len(over), wall_peak if over else 0.0,
                -pattern.pocket_area)
        if best is None or rank < best[0]:
            pattern.settings.update(
                first, walls=list(walls), wall=min(walls), wall_max=max(walls),
                rounds=round_number, over_limit=len(over), cells_over=over,
                cell_levels=[level / allow for level in levels],
                wall_peak=wall_peak, plate_peak=max(pocketed.peak, wall_peak))
            pattern.pocketed = pocketed
            best = (rank, pattern)
        if round_number >= rounds:
            break

        # Correct the walls: keep material in proportion to the load found.
        changed = 0.0
        new_walls = []
        for cell, wall in enumerate(walls):
            size = sizes[cell]
            if size <= 0.0:
                new_walls.append(wall)
                continue
            keep = was = _keep_for_wall(wall, size)
            if levels[cell] > allow:
                over_at[cell] = max(over_at[cell], was)
                if fine_at[cell] <= was:
                    fine_at[cell] = 2.0  # its neighbours changed: no longer true
            else:
                fine_at[cell] = min(fine_at[cell], was)
                if over_at[cell] >= was:
                    over_at[cell] = 0.0
            ratio = levels[cell] / (LOOP_AIM * allow)
            if ratio > 1.0:
                keep = min(1.0, was * ratio ** LOOP_THICKENING)
                if keep > fine_at[cell]:
                    keep = 0.5 * (was + fine_at[cell]) if levels[cell] <= allow else fine_at[cell]
            elif ratio < LOOP_IDLE:
                keep = was * (ratio / LOOP_IDLE) ** LOOP_THINNING
                if keep <= over_at[cell]:
                    keep = 0.5 * (was + over_at[cell])
            new = _wall_for_keep(keep, size, wall_min)
            new_walls.append(new)
            changed = max(changed, abs(min(new, size) - min(wall, size)) / size)
        if not over and changed < LOOP_SETTLED:
            break
        walls = new_walls
        pattern = geometry._fill(layout, 0.0, radius, min_width, walls=walls)

    pattern = best[1]
    pattern.settings.update(checks=checks, history=history,
                            loop_seconds=time.perf_counter() - started)
    return pattern


# --------------------------------------------------------------------------
# From stress to a pattern
# --------------------------------------------------------------------------

def wall_for(level, allow, cell_size, thinnest):
    """Wall thickness for a cell that sees stress ``level``.

    The rule: keep material in proportion to the load. If the solid plate
    carries ``level`` at a spot and the material may carry ``allow``, then
    the part ``level / allow`` of the material has to stay there. A cell of
    size s with walls w keeps about 1 - (1 - w/s)^2 of its material, which
    gives w. At ``level`` >= ``allow`` the whole cell stays solid: the wall
    returned is then twice the cell size, which leaves no pocket whatever the
    cell's shape. Never thinner than ``thinnest``.

    This is a simple rule, not a strength proof. It assumes the ribs that are
    left carry the load as well as solid material would; ribs that run across
    the load carry less. Leave room for that in ``allow``.
    """
    keep = min(1.0, max(0.0, level / allow)) if allow > 0 else 1.0
    if keep >= 1.0:
        return max(thinnest, 2.0 * cell_size)
    return max(thinnest, cell_size * (1.0 - math.sqrt(1.0 - keep)))


def build_stress_pattern(outer, holes, field, allow, count, seed=1, margin=0.3,
                         wall_min=0.1, radius=0.1, min_width=0.2, relax=3,
                         size_ratio=2.5, rounds=0, depth_ratio=1.0):
    """Pockets that are small with thick walls where the plate is loaded, and
    big with thin walls where it is not.

    field       a StressResult from ``solve_plate`` for the same face
    allow       the stress the material may carry: its yield strength divided
                by a safety factor
    wall_min    the thinnest wall to make, used wherever the load is low
    size_ratio  how many times wider a cell in an idle area is than one in a
                fully loaded area. 1 makes all cells about the same size.
    rounds      how many times to check the result on the pocketed plate and
                correct the walls (see ``refine_pattern``). 0: no check, the
                pattern comes from the solid plate's stress alone.
    depth_ratio pocket depth divided by plate thickness, for that check
    count, seed, margin, radius, min_width, relax   as in geometry.build_pattern

    Cell size goes by how loaded a spot is compared with the rest of this
    part: the least loaded areas get the widest cells, the most loaded ones
    cells ``size_ratio`` times narrower, and in between the width changes
    evenly with the stress. The total number of cells stays ``count``. (The
    lowest and highest 5% of the stress values set the two ends, so one sharp
    peak does not flatten everything else.) A part that is loaded about the
    same everywhere gets even cells.

    Wall thickness goes by the material instead: every cell gets its own
    (see ``wall_for``) from the highest stress found in it, compared with
    ``allow``. Cells that have to stay solid get no pocket.

    What was used is in ``settings``: 'wall' is the thinnest wall, 'wall_max'
    the thickest, and per cell 'walls', 'cell_sizes', 'cell_loads' (the load
    as a part of ``allow``) and 'cell_centres'.
    """
    layout = geometry._Layout(outer, holes, margin)

    low = high = 0.0
    levels = sorted(field.node_von_mises)
    if size_ratio > 1.0 and levels:
        low = levels[int(0.05 * (len(levels) - 1))]
        high = levels[int(0.95 * (len(levels) - 1))]
    if high > 0.0 and high - low > 0.1 * high:
        shrink = 1.0 - 1.0 / size_ratio

        def crowding(pt):
            """Cells per area here, 1 where idle up to size_ratio^2 where busiest."""
            busy = min(1.0, max(0.0, (field.von_mises_at(pt) - low) / (high - low)))
            width = 1.0 - shrink * busy  # cell width, as a part of the widest
            return 1.0 / (width * width)

        layout.place(count, seed, relax, crowding, size_ratio * size_ratio)
    else:
        layout.place(count, seed, relax)

    usable = layout.area()
    walls, sizes, loads, centres = [], [], [], []
    for cell in layout.cells:
        parts = geometry.clip_to_region(cell, usable) if cell else None
        parts = [part for part in (parts or []) if geometry.signed_area(part) > 0]
        area = sum(geometry.signed_area(part) for part in parts)
        if area <= 0.0:
            walls.append(wall_min)
            sizes.append(0.0)
            loads.append(0.0)
            centres.append(None)
            continue
        level = 0.0
        for part in parts:
            for pt in [geometry.centroid(part)] + part:
                level = max(level, field.von_mises_at(pt))
        size = math.sqrt(area)
        walls.append(wall_for(level, allow, size, wall_min))
        sizes.append(size)
        loads.append(min(1.0, level / allow) if allow > 0 else 1.0)
        centres.append(geometry.centroid(max(parts, key=geometry.signed_area)))

    pattern = geometry._fill(layout, 0.0, radius, min_width, walls=walls)
    pattern.settings.update(walls=walls, cell_sizes=sizes, cell_loads=loads,
                            cell_centres=centres, allow=allow, size_ratio=size_ratio)
    if rounds > 0:
        pattern = refine_pattern(layout, field, pattern, allow, rounds, depth_ratio,
                                 wall_min, radius, min_width)
    return pattern


def _plane_value(pt, samples):
    """Fit value = a + b*x + c*y through the samples (least squares) and
    return it at ``pt``, for each of the three stress parts. None if there
    are too few samples or they lie in a line."""
    if len(samples) < 5:
        return None
    n = float(len(samples))
    sx = sy = sxx = sxy = syy = 0.0
    rhs = [[0.0, 0.0, 0.0] for _ in range(3)]
    for (cx, cy), stress in samples:
        dx, dy = cx - pt[0], cy - pt[1]
        sx += dx
        sy += dy
        sxx += dx * dx
        sxy += dx * dy
        syy += dy * dy
        for part in range(3):
            rhs[part][0] += stress[part]
            rhs[part][1] += stress[part] * dx
            rhs[part][2] += stress[part] * dy
    # Solve the 3 x 3 set [n sx sy; sx sxx sxy; sy sxy syy] for "a" only.
    det = (n * (sxx * syy - sxy * sxy) - sx * (sx * syy - sxy * sy)
           + sy * (sx * sxy - sxx * sy))
    if abs(det) < 1e-9 * n * sxx * syy or det == 0.0:
        return None
    values = []
    for r0, r1, r2 in rhs:
        values.append((r0 * (sxx * syy - sxy * sxy) - sx * (r1 * syy - sxy * r2)
                       + sy * (r1 * sxy - sxx * r2)) / det)
    return tuple(values)
