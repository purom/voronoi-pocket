"""Tests for core/stress.py: the flat stress calculation is checked against
cases whose answer is known from theory. No Fusion, no extra packages.

Run with pytest, or directly:  python tests/test_stress.py
"""

import math
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import geometry as g  # noqa: E402
from core import stress as s  # noqa: E402

YOUNGS, POISSON = 7.0e6, 0.33     # aluminium, in N/cm^2


def circle(cx, cy, r, steps=64):
    return [(cx + r * math.cos(2 * math.pi * k / steps),
             cy + r * math.sin(2 * math.pi * k / steps)) for k in range(steps)]


def grid_mesh(width, height, nx, ny):
    """A plain rectangle of right-angled triangles, built by hand."""
    mesh = s.Mesh()
    mesh.size = width / nx
    for j in range(ny + 1):
        for i in range(nx + 1):
            mesh.nodes.append((width * i / nx, height * j / ny))
            mesh.on_edge.append(i in (0, nx) or j in (0, ny))
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            b, c, d = a + 1, a + nx + 1, a + nx + 2
            mesh.triangles.append((a, b, d))
            mesh.triangles.append((a, d, c))
    return mesh


def nodes_near(result, pt, radius):
    return [n for n, node in enumerate(result.mesh.nodes)
            if math.hypot(node[0] - pt[0], node[1] - pt[1]) <= radius]


# ------------------------------------------------------------------ mesh

def test_mesh_covers_the_face():
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    mesh = s.make_mesh(rect, [], 0.5)
    assert abs(mesh.area() - 60.0) < 0.01 * 60.0
    for i, j, k in mesh.triangles:
        assert s._double_area(mesh.nodes[i], mesh.nodes[j], mesh.nodes[k]) > 0
    for corner in rect:      # the four corners are kept exactly
        assert corner in mesh.nodes
    for node, on_edge in zip(mesh.nodes, mesh.on_edge):
        assert g.point_ring_distance(node, rect) < 1e-9 if on_edge else g.point_in_polygon(node, rect)


def test_mesh_follows_a_hole():
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    hole = circle(5.0, 3.0, 1.5)
    mesh = s.make_mesh(rect, [hole], 0.4)
    wanted = 60.0 - abs(g.signed_area(hole))
    assert abs(mesh.area() - wanted) < 0.02 * wanted
    centre_dist = [math.hypot(x - 5.0, y - 3.0) for x, y in mesh.nodes]
    assert min(centre_dist) > 1.5 - 0.01                 # nothing inside the hole
    assert sum(1 for d in centre_dist if abs(d - 1.5) < 0.01) >= 15   # points on its rim


def test_mesh_drops_loose_bits():
    # Two squares joined only at a corner point: the smaller one must go.
    shape = [(0, 0), (4, 0), (4, 4), (6, 4), (6, 6), (4, 6), (4, 4), (0, 4)]
    tris = [((0, 0), (1, 0), (0, 1)), ((1, 0), (1, 1), (0, 1)),      # joined by an edge
            ((5, 5), (6, 5), (5, 6))]                                # on its own
    assert s._largest_piece(tris) == tris[:2]
    assert shape  # (only the helper is tested here)


# ----------------------------------------------------- exact, by hand mesh

def test_even_pull_gives_even_stress_exactly():
    # A strip held so it can slide (left edge in x, bottom edge in y) and
    # pulled evenly on the right must show the same stress everywhere:
    # force / (height * thickness), and nothing sideways.
    width, height, thick, pull = 10.0, 4.0, 0.5, 600.0
    mesh = grid_mesh(width, height, 10, 4)
    left = [(0.0, 0.0), (0.0, height)]
    bottom = [(0.0, 0.0), (width, 0.0)]
    right = [(width, 0.0), (width, height)]
    result = s.solve_plate(None, [], [(left, (True, False)), (bottom, (False, True))],
                           [(right, (pull, 0.0))], YOUNGS, POISSON, thickness=thick,
                           mesh=mesh, tol=1e-12)
    wanted = pull / (height * thick)
    for sx, sy, sxy in result.triangle_stress:
        assert abs(sx - wanted) < 1e-6 * wanted
        assert abs(sy) < 1e-6 * wanted and abs(sxy) < 1e-6 * wanted
    # It stretches by force * length / (stiffness * cross-section) ...
    stretch = pull * width / (YOUNGS * height * thick)
    far = [result.moves[n][0] for n, node in enumerate(mesh.nodes) if node[0] == width]
    assert all(abs(m - stretch) < 1e-6 * stretch for m in far)
    # ... and narrows by Poisson's ratio.
    top = [result.moves[n][1] for n, node in enumerate(mesh.nodes) if node[1] == height]
    assert all(abs(m + POISSON * stretch * height / width) < 1e-6 * stretch for m in top)
    assert abs(result.reaction[0] + pull) < 1e-6 * pull
    assert abs(result.peak - wanted) < 1e-6 * wanted


def test_pure_shear_exactly():
    # Hold the bottom, push the top sideways, and hold every edge point from
    # moving up or down: every triangle carries the same shear stress.
    width, height, thick, push = 6.0, 3.0, 1.0, 90.0
    mesh = grid_mesh(width, height, 6, 3)
    outline = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height), (0.0, 0.0)]
    bottom = [(0.0, 0.0), (width, 0.0)]
    top = [(0.0, height), (width, height)]
    result = s.solve_plate(None, [], [bottom, (outline, (False, True))],
                           [(top, (push, 0.0))], YOUNGS, POISSON, thickness=thick,
                           mesh=mesh, tol=1e-12)
    wanted = push / (width * thick)
    inner = [st for st, tri in zip(result.triangle_stress, mesh.triangles)
             if all(1.0 <= mesh.nodes[n][0] <= width - 1.0 for n in tri)]
    assert inner
    for sx, sy, sxy in inner:
        assert abs(sxy - wanted) < 0.02 * wanted


# ------------------------------------------------------ with the mesher

def pulled_plate(force=1000.0, youngs=YOUNGS, size=0.5, hole=None):
    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    left = [(0.0, 0.0), (0.0, 10.0)]
    right = [(20.0, 0.0), (20.0, 10.0)]
    return s.solve_plate(rect, [hole] if hole else [], [left], [(right, (force, 0.0))],
                         youngs, POISSON, thickness=0.5, size=size)


def test_pulled_plate_away_from_the_grip():
    result = pulled_plate()
    nominal = 1000.0 / (10.0 * 0.5)
    for n in nodes_near(result, (12.0, 5.0), 2.0):
        sx, sy, sxy = result.node_stress[n]
        assert abs(sx - nominal) < 0.03 * nominal
        assert abs(sy) < 0.03 * nominal and abs(sxy) < 0.03 * nominal
    assert abs(result.reaction[0] + 1000.0) < 1e-3          # supports carry the pull
    assert abs(result.reaction[1]) < 1e-3
    assert abs(result.von_mises_at((12.0, 5.0)) - nominal) < 0.03 * nominal
    assert result.von_mises_at((50.0, 50.0)) == 0.0         # off the plate


def test_stress_follows_force_but_not_stiffness():
    base = pulled_plate()
    double_force = pulled_plate(force=2000.0)
    softer = pulled_plate(youngs=YOUNGS / 10.0)
    for n in range(0, len(base.mesh.nodes), 7):
        vm = base.node_von_mises[n]
        assert abs(double_force.node_von_mises[n] - 2.0 * vm) < 1e-4 * (vm + 1.0)
        assert abs(softer.node_von_mises[n] - vm) < 1e-4 * (vm + 1.0)
        assert abs(softer.moves[n][0] - 10.0 * base.moves[n][0]) < 1e-4 * (abs(base.moves[n][0]) * 10 + 1e-12)


def test_stress_around_a_hole():
    # Theory for a pulled plate with a round hole (radius a), on the line
    # across the pull at distance r from the hole's centre:
    #     stress = far stress * (1 + a^2 / (2 r^2) + 3 a^4 / (2 r^4))
    # That is 3 on the rim, 1.65 at 0.4 a from the rim, 1.22 at 1 a from it.
    # In line with the pull the rim is nearly unloaded in the pull direction.
    hole = circle(10.0, 5.0, 1.0, steps=96)
    nominal = 1000.0 / (10.0 * 0.5)

    def nearest(result, pt):
        return min(range(len(result.mesh.nodes)),
                   key=lambda n: math.hypot(result.mesh.nodes[n][0] - pt[0],
                                            result.mesh.nodes[n][1] - pt[1]))

    def beside(result):
        return max(result.node_stress[n][0]
                   for n in nodes_near(result, (10.0, 6.0), 0.2)
                   + nodes_near(result, (10.0, 4.0), 0.2)) / nominal

    coarse = pulled_plate(size=0.5, hole=hole)
    fine = pulled_plate(size=0.3, hole=hole)

    # Away from the rim, where pockets go, both meshes are close to theory.
    for result in (coarse, fine):
        assert abs(result.node_stress[nearest(result, (10.0, 6.4))][0] / nominal - 1.65) < 0.15
        assert abs(result.node_stress[nearest(result, (10.0, 7.0))][0] / nominal - 1.22) < 0.12
        assert abs(result.von_mises_at((4.0, 5.0)) / nominal - 1.0) < 0.1

    # Right on the rim the value reads low, less so on the finer mesh. This
    # triangle type smears a sharp peak; the peak is not to be trusted as a
    # strength check (see the module notes).
    assert 1.6 < beside(coarse) < beside(fine) < 3.5
    assert beside(fine) > 2.2
    in_line = [result.node_stress[n][0] / nominal for n in nodes_near(fine, (11.0, 5.0), 0.2)]
    assert in_line and max(abs(v) for v in in_line) < 0.45


def test_load_on_a_hole_rim():
    # Pull on the whole rim of a hole (a pin or bolt), plate held at the left.
    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    hole = circle(14.0, 5.0, 1.0, steps=64)
    rim = hole + [hole[0]]
    result = s.solve_plate(rect, [hole], [[(0.0, 0.0), (0.0, 10.0)]], [(rim, (500.0, 0.0))],
                           YOUNGS, POISSON, thickness=0.5, size=0.4)
    assert abs(result.reaction[0] + 500.0) < 1e-3
    between = result.von_mises_at((6.0, 5.0))      # carries the whole pull to the grip
    assert abs(between - 500.0 / (10.0 * 0.5)) < 0.1 * between
    # Past the hole nothing has to be carried, so the stress dies away.
    fading = [result.von_mises_at((x, 5.0)) for x in (15.5, 16.5, 17.5, 18.5)]
    assert fading == sorted(fading, reverse=True)
    assert between > 2.5 * fading[-1]


def test_force_at_a_point():
    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    left = [(0.0, 0.0), (0.0, 10.0)]
    nominal = 800.0 / (10.0 * 0.5)

    def pull_at(pt, force=(800.0, 0.0)):
        return s.solve_plate(rect, [], [left], [], YOUNGS, POISSON, thickness=0.5,
                             size=0.5, point_loads=[(pt, force)])

    # Pulled at one point in the middle of the plate.
    inside = pull_at((14.0, 5.0))
    assert abs(inside.reaction[0] + 800.0) < 1e-3 and abs(inside.reaction[1]) < 1e-3
    # Far from the point the pull has spread over the whole width ...
    for y in (2.0, 5.0, 8.0):
        assert abs(inside.node_stress[min(
            range(len(inside.mesh.nodes)),
            key=lambda n: math.hypot(inside.mesh.nodes[n][0] - 4.0,
                                     inside.mesh.nodes[n][1] - y))][0] - nominal) < 0.2 * nominal
    # ... next to the point the stress is several times higher (pulled behind
    # it, pushed in front of it), and past it there is little left.
    top = max(range(len(inside.mesh.nodes)), key=lambda n: inside.node_von_mises[n])
    assert inside.peak > 2.5 * nominal
    assert math.hypot(inside.mesh.nodes[top][0] - 14.0, inside.mesh.nodes[top][1] - 5.0) < 1.5
    assert inside.von_mises_at((19.0, 5.0)) < 0.5 * nominal

    # A point on the rim and a point at a corner work too.
    for pt in ((20.0, 5.0), (20.0, 10.0)):
        assert abs(pull_at(pt).reaction[0] + 800.0) < 1e-3
    # The force is spread over several mesh points, not put on one.
    assert len(s._point_shares(inside.mesh, (14.0, 5.0), 1.5 * inside.mesh.size)) >= 5
    shares = s._point_shares(inside.mesh, (20.0, 10.0), 1.5 * inside.mesh.size)
    assert abs(sum(share for _, share in shares) - 1.0) < 1e-12

    # Two points pulling against each other: nothing reaches the grip.
    pair = s.solve_plate(rect, [], [left], [], YOUNGS, POISSON, thickness=0.5, size=0.5,
                         point_loads=[((10.0, 5.0), (-800.0, 0.0)), ((16.0, 5.0), (800.0, 0.0))])
    assert abs(pair.reaction[0]) < 1e-3
    assert pair.von_mises_at((13.0, 5.0)) > 5.0 * pair.von_mises_at((2.0, 5.0))

    # Together with a force on an edge, and a point that is off the plate.
    both = s.solve_plate(rect, [], [left], [([(20.0, 0.0), (20.0, 10.0)], (200.0, 0.0))],
                         YOUNGS, POISSON, thickness=0.5, size=0.5,
                         point_loads=[((14.0, 5.0), (800.0, 0.0))])
    assert abs(both.reaction[0] + 1000.0) < 1e-3
    try:
        pull_at((40.0, 5.0))
    except s.StressError as error:
        assert 'not on the face' in str(error)
    else:
        raise AssertionError('expected StressError')


# ----------------------------------------------- from stress to a pattern

def test_wall_rule():
    assert s.wall_for(0.0, 100.0, 2.0, 0.1) == 0.1                 # idle: thinnest wall
    assert s.wall_for(100.0, 100.0, 2.0, 0.1) == 4.0               # at the limit: solid
    assert s.wall_for(500.0, 100.0, 2.0, 0.1) == 4.0
    assert s.wall_for(99.0, 100.0, 2.0, 0.1) < 2.0                 # just under: a sliver of pocket
    half = s.wall_for(50.0, 100.0, 2.0, 0.1)                       # half the material stays
    assert abs((1.0 - (1.0 - half / 2.0) ** 2) - 0.5) < 1e-12
    assert s.wall_for(10.0, 100.0, 2.0, 0.1) < s.wall_for(30.0, 100.0, 2.0, 0.1) < half


def bent_plate(size=0.4):
    """A plate held at its left edge with a downward force on its right edge.

    Like a diving board seen from the side: the top and bottom near the grip
    work hardest, the middle line and the free end hardly at all.
    """
    rect = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)]
    left = [(0.0, 0.0), (0.0, 10.0)]
    right = [(20.0, 0.0), (20.0, 10.0)]
    field = s.solve_plate(rect, [], [left], [(right, (0.0, -1000.0))],
                          YOUNGS, POISSON, thickness=0.5, size=size)
    return rect, field


def test_bent_plate_matches_beam_theory():
    rect, field = bent_plate()
    # Top edge, 1 from the grip: force * arm * distance from the middle line
    # / (thickness * height^3 / 12)  =  1000 * 19 * 4.5 / 41.67.
    assert abs(field.von_mises_at((1.0, 9.5)) - 2052.0) < 0.06 * 2052.0
    # Middle line: shear only, 1.5 * force / cross-section, times root 3.
    assert abs(field.von_mises_at((10.0, 5.0)) - 519.6) < 0.05 * 519.6
    assert abs(field.reaction[1] - 1000.0) < 1e-3


def test_stress_pattern_is_thick_where_loaded():
    rect, field = bent_plate()
    # size_ratio 1: all cells the same size, so only the walls differ here.
    kw = dict(count=70, seed=2, margin=0.4, wall_min=0.1, radius=0.1, min_width=0.2,
              size_ratio=1.0)

    pattern = s.build_stress_pattern(rect, [], field, 1500.0, **kw)
    walls = pattern.settings['walls']
    assert len(walls) == 70 and len(pattern.paths) > 50
    assert pattern.settings['wall'] >= 0.1 and pattern.settings['wall_max'] > 1.0

    # Walls follow the stress under each cell: the hardest-working quarter of
    # the cells gets walls several times thicker than the idlest quarter.
    layout = g._Layout(rect, [], 0.4)
    layout.place(70, 2, 3)
    seen = [(field.von_mises_at(g.centroid(cell)), wall)
            for cell, wall in zip(layout.cells, walls) if cell]
    seen.sort()
    quarter = len(seen) // 4
    calm = sum(w for _, w in seen[:quarter]) / quarter
    busy = sum(w for _, w in seen[-quarter:]) / quarter
    assert busy > 3.0 * calm

    # A stronger material (or lower load) needs less wall, a weaker one more.
    strong = s.build_stress_pattern(rect, [], field, 15000.0, **kw)
    weak = s.build_stress_pattern(rect, [], field, 600.0, **kw)
    assert strong.share > pattern.share > weak.share
    assert s.build_stress_pattern(rect, [], field, 1.0, **kw).paths == []   # all solid

    # With no load to speak of, it is the ordinary pattern with the thinnest wall.
    idle = s.build_stress_pattern(rect, [], field, 1e12, **kw)
    plain = g.build_pattern(rect, [], 70, seed=2, wall=0.1, margin=0.4,
                            radius=0.1, min_width=0.2, relax=3)
    assert idle.paths == plain.paths


def quarters(pattern, values):
    """Average of ``values`` over the idlest and the busiest quarter of the cells."""
    st = pattern.settings
    seen = sorted((load, value) for load, value, size in
                  zip(st['cell_loads'], values, st['cell_sizes']) if size > 0)
    quarter = len(seen) // 4
    return (sum(v for _, v in seen[:quarter]) / quarter,
            sum(v for _, v in seen[-quarter:]) / quarter)


def test_cells_are_small_where_loaded_and_big_where_idle():
    rect, field = bent_plate()
    kw = dict(count=110, seed=2, margin=0.4, wall_min=0.1, radius=0.1, min_width=0.2)

    even = s.build_stress_pattern(rect, [], field, 2500.0, size_ratio=1.0, **kw)
    calm, busy = quarters(even, even.settings['cell_sizes'])
    assert 0.8 < busy / calm < 1.25                     # same size everywhere

    varied = s.build_stress_pattern(rect, [], field, 2500.0, size_ratio=2.5, **kw)
    st = varied.settings
    assert len(st['cell_sizes']) == 110 and st['size_ratio'] == 2.5
    calm, busy = quarters(varied, st['cell_sizes'])
    assert busy < 0.65 * calm                           # loaded cells clearly smaller
    # The idle cells are bigger than in the even pattern, the loaded ones smaller.
    even_calm, even_busy = quarters(even, even.settings['cell_sizes'])
    assert calm > 1.15 * even_calm and busy < 0.9 * even_busy

    # A stronger ratio spreads the sizes further.
    stronger = s.build_stress_pattern(rect, [], field, 2500.0, size_ratio=4.0, **kw)
    calm4, busy4 = quarters(stronger, stronger.settings['cell_sizes'])
    assert busy4 / calm4 < busy / calm

    # Walls are still thicker where loaded, and the result is repeatable.
    wall_calm, wall_busy = quarters(varied, st['walls'])
    assert wall_busy > 2.0 * wall_calm
    again = s.build_stress_pattern(rect, [], field, 2500.0, size_ratio=2.5, **kw)
    assert again.paths == varied.paths
    assert varied.paths and len(varied.paths) > 60


def test_uneven_cells_keep_margin_and_walls():
    # The checks every pattern has to pass, on a face with holes.
    rect = [(0.0, 0.0), (26.0, 0.0), (26.0, 9.0), (0.0, 9.0)]
    h1, h2 = circle(4.5, 4.5, 1.6, 80), circle(21.5, 4.5, 1.6, 80)
    field = s.solve_plate(rect, [h1, h2], [h1 + [h1[0]]], [(h2 + [h2[0]], (4000.0, 0.0))],
                          YOUNGS, POISSON, thickness=0.5, size=0.5)
    margin = 0.4
    pattern = s.build_stress_pattern(rect, [h1, h2], field, 2500.0, count=80, seed=5,
                                     margin=margin, wall_min=0.12, radius=0.12,
                                     min_width=0.25, size_ratio=2.5)
    assert len(pattern.paths) > 40
    pockets = [g.path_points(p) for p in pattern.paths]
    for pocket in pockets:
        assert g.signed_area(pocket) > 0
        for pt in pocket:
            assert g.point_in_polygon(pt, rect)
            assert min(g.point_ring_distance(pt, ring) for ring in (rect, h1, h2)) >= margin - 1e-6
    boxes = [g.bounding_box(p) for p in pockets]
    for i in range(len(pockets)):
        for j in range(i + 1, len(pockets)):
            a, b = boxes[i], boxes[j]
            if a[0] > b[2] + 0.2 or b[0] > a[2] + 0.2 or a[1] > b[3] + 0.2 or b[1] > a[3] + 0.2:
                continue
            assert g.polygon_outline_distance(pockets[i], pockets[j]) >= 0.12 - 1e-6


def test_bad_inputs_are_explained():
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    left = [(0.0, 0.0), (0.0, 6.0)]
    right = [(10.0, 0.0), (10.0, 6.0)]
    for supports, loads in (([], [(right, (1.0, 0.0))]),          # not held
                            ([left], []),                         # no force
                            ([left], [(right, (0.0, 0.0))])):     # zero force
        try:
            s.solve_plate(rect, [], supports, loads, YOUNGS, POISSON, size=1.0)
        except s.StressError:
            continue
        raise AssertionError('expected StressError')
    try:
        s.solve_plate(rect, [], [left], [(right, (1.0, 0.0))], YOUNGS, 0.7, size=1.0)
    except s.StressError:
        pass
    else:
        raise AssertionError('expected StressError')


if __name__ == '__main__':
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith('test_') and callable(func):
            try:
                func()
                print('ok    ' + name)
            except Exception:
                failures += 1
                print('FAIL  ' + name)
                traceback.print_exc()
    print('%d failed' % failures if failures else 'all passed')
    sys.exit(1 if failures else 0)
