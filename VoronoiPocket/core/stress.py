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
* The stress is that of the solid plate, before any pockets are cut.
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

    def area(self):
        return sum(_double_area(self.nodes[i], self.nodes[j], self.nodes[k])
                   for i, j, k in self.triangles) / 2.0


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


def _conjugate_gradient(rows, diagonal, rhs, tol, limit):
    """Solve ``rows * x = rhs`` step by step (conjugate gradients).

    ``rows`` is the matrix, one list of (column, value) per row. Each step
    divides by the diagonal first, which evens out stiff and soft spots and
    cuts the number of steps. Returns (x, steps taken).
    """
    n = len(rhs)
    x = [0.0] * n
    size = math.sqrt(_dot(rhs, rhs))
    if size == 0.0:
        return x, 0
    r = list(rhs)
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
        self._buckets = None

    @property
    def peak(self):
        return max(self.node_von_mises) if self.node_von_mises else 0.0

    def _find(self, pt):
        """(triangle, three weights) for the triangle the point is in, or None."""
        mesh = self.mesh
        if self._buckets is None:
            self._cell = 2.0 * mesh.size
            self._buckets = {}
            for index, (i, j, k) in enumerate(mesh.triangles):
                xs = (mesh.nodes[i][0], mesh.nodes[j][0], mesh.nodes[k][0])
                ys = (mesh.nodes[i][1], mesh.nodes[j][1], mesh.nodes[k][1])
                for cx in range(int(math.floor(min(xs) / self._cell)),
                                int(math.floor(max(xs) / self._cell)) + 1):
                    for cy in range(int(math.floor(min(ys) / self._cell)),
                                    int(math.floor(max(ys) / self._cell)) + 1):
                        self._buckets.setdefault((cx, cy), []).append(index)
        key = (int(math.floor(pt[0] / self._cell)), int(math.floor(pt[1] / self._cell)))
        nearest = None
        for index in self._buckets.get(key, ()):
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


def solve_plate(outer, holes, supports, loads, youngs, poisson,
                thickness=1.0, size=None, mesh=None, tol=1e-7, point_loads=()):
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

    # Stiffness of every triangle, added into one big table.
    scale = youngs / (1.0 - poisson * poisson)
    d11, d12, d33 = scale, scale * poisson, scale * (1.0 - poisson) / 2.0
    table = [dict() for _ in range(2 * count)]
    shapes = []  # per triangle: (b, c, twice its area), for the stress later
    for tri in mesh.triangles:
        (xi, yi), (xj, yj), (xk, yk) = nodes[tri[0]], nodes[tri[1]], nodes[tri[2]]
        b = (yj - yk, yk - yi, yi - yj)
        c = (xk - xj, xi - xk, xj - xi)
        double = xi * b[0] + xj * b[1] + xk * b[2]
        shapes.append((b, c, double))
        factor = thickness / (2.0 * double)
        for p in range(3):
            row_x, row_y = table[2 * tri[p]], table[2 * tri[p] + 1]
            for q in range(3):
                cx, cy = 2 * tri[q], 2 * tri[q] + 1
                row_x[cx] = row_x.get(cx, 0.0) + factor * (d11 * b[p] * b[q] + d33 * c[p] * c[q])
                row_x[cy] = row_x.get(cy, 0.0) + factor * (d12 * b[p] * c[q] + d33 * c[p] * b[q])
                row_y[cx] = row_y.get(cx, 0.0) + factor * (d12 * c[p] * b[q] + d33 * b[p] * c[q])
                row_y[cy] = row_y.get(cy, 0.0) + factor * (d11 * c[p] * c[q] + d33 * b[p] * b[q])

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
        shares = _point_shares(mesh, pt, 1.5 * mesh.size)
        if not shares:
            raise StressError('A load point is not on the face.')
        for number, share in shares:
            force[2 * number] += fx * share
            force[2 * number + 1] += fy * share
    if not any(force):
        raise StressError('There is no force on the part.')

    # Leave the held directions out and solve for the rest.
    free = [d for d in range(2 * count) if d not in held]
    place = {d: n for n, d in enumerate(free)}
    rows = [[(place[col], value) for col, value in table[d].items() if col in place]
            for d in free]
    diagonal = [table[d][d] for d in free]
    solved, steps = _conjugate_gradient(rows, diagonal, [force[d] for d in free],
                                        tol, 20 * len(free) + 100)
    moved = [0.0] * (2 * count)
    for d, value in zip(free, solved):
        moved[d] = value

    result = StressResult()
    result.mesh = mesh
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

    # Stress per triangle (it is the same all over one triangle).
    sums = [[0.0, 0.0, 0.0, 0.0] for _ in range(count)]
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
        for number in tri:
            around[number].append(index)
            total = sums[number]
            total[0] += stress[0] * double
            total[1] += stress[1] * double
            total[2] += stress[2] * double
            total[3] += double

    # Stress at the points. Inside the plate a point is surrounded by
    # triangles, and their average (weighted by area) is a good value. A
    # point on the edge only has triangles on one side, so an average reads
    # too low exactly where stress peaks (beside a hole). There a sloping
    # plane is fitted through the nearby triangles and read off at the point.
    for number in range(count):
        total = sums[number]
        stress = (total[0] / total[3], total[1] / total[3], total[2] / total[3])
        if mesh.on_edge[number]:
            patch = set()
            for index in around[number]:
                for corner in mesh.triangles[index]:
                    patch.update(around[corner])
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
                         size_ratio=2.5):
    """Pockets that are small with thick walls where the plate is loaded, and
    big with thin walls where it is not.

    field       a StressResult from ``solve_plate`` for the same face
    allow       the stress the material may carry: its yield strength divided
                by a safety factor
    wall_min    the thinnest wall to make, used wherever the load is low
    size_ratio  how many times wider a cell in an idle area is than one in a
                fully loaded area. 1 makes all cells about the same size.
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
