"""Tests for core/geometry.py. They need no Fusion and no extra packages.

Run with pytest, or directly:  python tests/test_geometry.py
"""

import math
import os
import random
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import geometry as g  # noqa: E402

RECT = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
L_SHAPE = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 8.0), (0.0, 8.0)]
BOX = (0.0, 0.0, 10.0, 6.0)


def circle(cx, cy, r, steps=48):
    return [(cx + r * math.cos(2 * math.pi * k / steps),
             cy + r * math.sin(2 * math.pi * k / steps)) for k in range(steps)]


def peanut(steps=240):
    """A smooth outline with two lobes and a waist that curves inward."""
    pts = []
    for k in range(steps):
        a = 2 * math.pi * k / steps
        r = 3.0 + 1.4 * math.cos(2 * a)
        pts.append((6.0 + 1.5 * r * math.cos(a), 4.0 + r * math.sin(a)))
    return pts


def path_is_closed(path):
    for i, seg in enumerate(path):
        nxt = path[(i + 1) % len(path)]
        end, start = seg[-1], nxt[1]
        if math.hypot(end[0] - start[0], end[1] - start[1]) > 1e-9:
            return False
    return True


def edge_distance(pt, outer, holes=()):
    """Distance from a point to the nearest edge of a face."""
    return min(g.point_ring_distance(pt, ring) for ring in [outer] + list(holes))


def on_face(pt, outer, holes=()):
    return g.point_in_polygon(pt, outer) and not any(
        g.point_in_polygon(pt, h) for h in holes)


def inside_loops(pt, loops):
    """Inside a shape given as counter-clockwise outlines and clockwise holes."""
    count = 0
    for loop in loops:
        if g.point_in_polygon(pt, loop):
            count += 1 if g.signed_area(loop) > 0 else -1
    return count > 0


def rings_of(outer, holes=()):
    return [g.ensure_ccw(outer)] + [g.ensure_cw(h) for h in holes]


def band_share(outer, holes, pockets, margin, step=0.1, slack=0.01):
    """Share of the face's edge where the nearest pocket is ``margin`` away.

    Walks along every edge of the face; at each stop the nearest pocket is
    either about ``margin`` away (a pocket follows the edge there) or further
    (a wall between two pockets meets the border there).
    """
    boxes = [g.bounding_box(p) for p in pockets]
    reach = margin + slack
    close = total = 0
    for ring in [outer] + list(holes):
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
            for k in range(steps):
                pt = (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
                total += 1
                for pocket, (x0, y0, x1, y1) in zip(pockets, boxes):
                    if (pt[0] < x0 - reach or pt[0] > x1 + reach
                            or pt[1] < y0 - reach or pt[1] > y1 + reach):
                        continue
                    if g.point_ring_distance(pt, pocket) <= reach:
                        close += 1
                        break
    return close / float(total)


def check_pattern(pattern, outer, holes, wall, margin):
    """Every pocket is on the face, keeps the margin, and keeps the wall."""
    pockets = [g.path_points(p) for p in pattern.paths]
    for path, pocket in zip(pattern.paths, pockets):
        assert path_is_closed(path)
        assert g.signed_area(pocket) > 0
        for pt in pocket:
            assert on_face(pt, outer, holes)
            assert edge_distance(pt, outer, holes) >= margin - 1e-6
    boxes = [g.bounding_box(p) for p in pockets]
    for i in range(len(pockets)):
        for j in range(i + 1, len(pockets)):
            a, b = boxes[i], boxes[j]
            if (a[0] > b[2] + wall or b[0] > a[2] + wall
                    or a[1] > b[3] + wall or b[1] > a[3] + wall):
                continue
            assert g.polygon_outline_distance(pockets[i], pockets[j]) >= wall - 1e-6
    return pockets


# ---------------------------------------------------------------- basics

def test_area_and_winding():
    assert abs(g.signed_area(RECT) - 60.0) < 1e-12
    assert abs(g.signed_area(RECT[::-1]) + 60.0) < 1e-12
    assert g.signed_area(g.ensure_ccw(RECT[::-1])) > 0
    assert g.signed_area(g.ensure_cw(RECT)) < 0
    cx, cy = g.centroid(RECT)
    assert abs(cx - 5.0) < 1e-12 and abs(cy - 3.0) < 1e-12


def test_point_in_polygon_l_shape():
    assert g.point_in_polygon((1.0, 7.0), L_SHAPE)
    assert g.point_in_polygon((9.0, 1.0), L_SHAPE)
    assert not g.point_in_polygon((7.0, 6.0), L_SHAPE)   # in the notch
    assert not g.point_in_polygon((11.0, 1.0), L_SHAPE)


def test_convexity():
    assert g.is_convex(RECT)
    assert g.is_convex(circle(0, 0, 3))
    assert not g.is_convex(L_SHAPE)


def test_clean_polygon_drops_duplicates_and_collinear():
    messy = [(0, 0), (0, 0), (5, 0), (10, 0), (10, 6), (10, 6), (0, 6), (0, 0)]
    assert g.clean_polygon(messy) == [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
    assert g.clean_polygon([(0, 0), (1, 0), (2, 0)]) == []


def test_inset_square():
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    small = g.inset_convex(square, 1.0)
    assert abs(g.signed_area(small) - 4.0) < 1e-9
    assert all(abs(a - b) < 1e-9
               for a, b in zip(g.bounding_box(small), (1.0, 1.0, 3.0, 3.0)))
    assert g.inset_convex(square, 2.5) == []


def test_clip_convex_overlap():
    a = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    b = [(2.0, 1.0), (6.0, 1.0), (6.0, 3.0), (2.0, 3.0)]
    assert abs(g.signed_area(g.clip_convex(a, b)) - 4.0) < 1e-9
    far = [(10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0)]
    assert g.clip_convex(a, far) == []


def test_segment_distance():
    assert g.segment_distance((0, 0), (2, 0), (1, -1), (1, 1)) == 0.0
    assert abs(g.segment_distance((0, 0), (2, 0), (0, 3), (2, 3)) - 3.0) < 1e-12
    assert abs(g.segment_distance((0, 0), (1, 0), (4, 4), (4, 9)) - 5.0) < 1e-12


# ------------------------------------------------- shrinking a region

def check_shrunk(outer, holes, d, samples=3000, seed=1):
    """A point is in the shrunk region exactly when it is on the face and at
    least ``d`` from every edge."""
    region = g.Region(g.offset_region(rings_of(outer, holes), d))
    minx, miny, maxx, maxy = g.bounding_box(outer)
    rng = random.Random(seed)
    checked = 0
    for _ in range(samples):
        pt = (rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        dist = edge_distance(pt, outer, holes)
        if abs(dist - d) < 0.01:
            continue  # too close to the new edge to call
        assert region.contains(pt) == (on_face(pt, outer, holes) and dist > d), pt
        checked += 1
    assert checked > samples / 2
    return region


def test_shrink_square():
    square = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0)]
    loops = g.offset_region([square], 1.0)
    assert len(loops) == 1
    assert abs(g.signed_area(loops[0]) - 16.0) < 1e-9
    assert g.offset_region([square], 3.5) == []
    assert g.offset_region([square], 0.0) == [square]


def test_shrink_l_shape_rounds_the_inward_corner():
    region = check_shrunk(L_SHAPE, [], 0.7)
    assert len(region.loops) == 1
    # The new edge near the inward corner (4, 4) is an arc of radius 0.7,
    # built from straight pieces that touch the arc from outside.
    near = [p for p in region.loops[0]
            if 0.0 < 4.0 - p[0] < 0.69 and 0.0 < 4.0 - p[1] < 0.69]
    assert len(near) >= 3
    for p in near:
        assert 0.7 <= math.hypot(p[0] - 4.0, p[1] - 4.0) <= 0.7 + 2 * g.ARC_TOL
    loop = region.loops[0]
    for i in range(len(loop)):
        assert g._point_segment_distance((4.0, 4.0), loop[i - 1], loop[i]) >= 0.7 - 1e-9


def test_shrink_with_holes():
    holes = [circle(3.0, 3.0, 1.0), circle(7.5, 3.0, 0.4)]
    region = check_shrunk(RECT, holes, 0.5)
    assert len(region.loops) == 3
    assert [g.signed_area(l) > 0 for l in region.loops] == [True, False, False]


def test_shrink_hole_close_to_the_edge():
    # The grown hole reaches past the shrunk outline.
    check_shrunk(RECT, [circle(1.2, 3.0, 0.5)], 0.6)


def test_shrink_curved_outline():
    check_shrunk(peanut(), [], 0.5)
    check_shrunk(circle(4.0, 4.0, 4.0, 160), [circle(4.0, 4.0, 1.0)], 0.8)


def test_shrink_pinches_a_narrow_neck():
    # Two 4 x 4 squares joined by a bar 1 wide.
    dumbbell = [(0, 0), (4, 0), (4, 1.5), (8, 1.5), (8, 0), (12, 0), (12, 4),
                (8, 4), (8, 2.5), (4, 2.5), (4, 4), (0, 4)]
    region = check_shrunk(dumbbell, [], 0.6)
    assert len(region.loops) == 2
    assert all(g.signed_area(l) > 0 for l in region.loops)


def test_shrink_thin_part_vanishes():
    # An L with one arm only 1 wide.
    thin = [(0, 0), (10, 0), (10, 1), (5, 1), (5, 6), (0, 6)]
    region = check_shrunk(thin, [], 0.7)
    assert len(region.loops) == 1
    assert g.bounding_box(region.loops[0])[2] < 5.0


# --------------------------------------------- cutting to a region

def check_clip(poly, region, samples=1500, seed=2):
    """The result holds exactly the points inside both ``poly`` and ``region``."""
    loops = g.clip_to_region(poly, region)
    assert loops is not None
    minx, miny, maxx, maxy = g.bounding_box(poly)
    rng = random.Random(seed)
    for _ in range(samples):
        pt = (rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if min([g.point_ring_distance(pt, poly)]
               + [g.point_ring_distance(pt, l) for l in region.loops]) < 1e-3:
            continue
        expected = g.point_in_polygon(pt, poly) and region.contains(pt)
        assert inside_loops(pt, loops) == expected, pt
    return loops


def test_clip_far_from_any_edge():
    region = g.Region([RECT])
    inside = [(4.0, 2.0), (5.0, 2.0), (5.0, 3.0), (4.0, 3.0)]
    outside = [(20.0, 2.0), (21.0, 2.0), (21.0, 3.0), (20.0, 3.0)]
    assert g.clip_to_region(inside, region) == [inside]
    assert g.clip_to_region(outside, region) == []


def test_clip_across_a_straight_edge():
    loops = check_clip([(8.0, 1.0), (12.0, 1.0), (12.0, 3.0), (8.0, 3.0)],
                       g.Region([RECT]))
    assert len(loops) == 1 and abs(g.signed_area(loops[0]) - 4.0) < 1e-9


def test_clip_around_an_inward_corner():
    region = g.Region(g.offset_region([L_SHAPE], 0.5))
    loops = check_clip([(2.0, 2.0), (7.0, 2.0), (7.0, 6.5), (2.0, 6.5)], region)
    assert len(loops) == 1 and not g.is_convex(loops[0])


def test_clip_can_give_two_pieces():
    # A tall slim box over the notch of a U: one piece in each arm.
    u_shape = [(0, 0), (9, 0), (9, 6), (6, 6), (6, 2), (3, 2), (3, 6), (0, 6)]
    loops = check_clip([(1.0, 3.0), (8.0, 3.0), (8.0, 5.0), (1.0, 5.0)],
                       g.Region([u_shape]))
    assert len(loops) == 2
    assert all(abs(g.signed_area(l) - 4.0) < 1e-9 for l in loops)


def test_clip_with_a_hole_inside_and_one_on_the_rim():
    holes = [circle(5.0, 3.0, 0.8), circle(8.0, 3.0, 0.8)]
    region = g.Region(rings_of(RECT, holes))
    loops = check_clip([(3.0, 1.0), (8.0, 1.0), (8.0, 5.0), (3.0, 5.0)], region)
    assert sorted(g.signed_area(l) > 0 for l in loops) == [False, True]


def test_clip_where_a_grown_hole_overlaps_the_outline():
    region = g.Region(g.offset_region(rings_of(RECT, [circle(1.2, 3.0, 0.5)]), 0.6))
    check_clip([(0.2, 1.0), (4.0, 1.0), (4.0, 5.0), (0.2, 5.0)], region)


def test_clip_curved_outline_many_boxes():
    region = g.Region(g.offset_region([peanut()], 0.4))
    rng = random.Random(5)
    for _ in range(25):
        x, y = rng.uniform(0.0, 11.0), rng.uniform(0.0, 7.0)
        w, h = rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0)
        check_clip([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], region,
                   samples=300, seed=7)


# --------------------------------------------------------------- voronoi

def test_voronoi_cells_tile_the_box():
    rng = random.Random(7)
    pts = [(rng.uniform(0, 10), rng.uniform(0, 6)) for _ in range(80)]
    cells = g.voronoi_cells(pts, BOX)
    assert len(cells) == len(pts)
    assert abs(sum(g.signed_area(c) for c in cells) - 60.0) < 1e-6
    for i, (pt, cell) in enumerate(zip(pts, cells)):
        assert g.point_in_polygon(pt, cell)
        for vx, vy in cell:  # no other seed is closer to a cell corner
            own = math.hypot(vx - pt[0], vy - pt[1])
            nearest = min(math.hypot(vx - q[0], vy - q[1]) for q in pts)
            assert own <= nearest + 1e-7


def test_voronoi_single_point_gets_the_whole_box():
    cells = g.voronoi_cells([(3.0, 2.0)], BOX)
    assert abs(g.signed_area(cells[0]) - 60.0) < 1e-9


def test_voronoi_duplicate_points():
    cells = g.voronoi_cells([(3.0, 2.0), (3.0, 2.0), (7.0, 4.0)], BOX)
    assert cells[0] and cells[2]
    assert cells[1] == []
    assert abs(g.signed_area(cells[0]) + g.signed_area(cells[2]) - 60.0) < 1e-9


def test_random_points_land_inside_the_region():
    region = g.Region(rings_of(L_SHAPE, [circle(2.0, 2.0, 1.0)]))
    pts = g.random_points(region, g.bounding_box(L_SHAPE), 200, random.Random(1))
    assert len(pts) == 200
    assert all(on_face(p, L_SHAPE, [circle(2.0, 2.0, 1.0)]) for p in pts)


def test_lloyd_relax_evens_out_cells():
    region = g.Region([RECT])
    pts = g.random_points(region, BOX, 40, random.Random(3))

    def spread(points):
        areas = [g.signed_area(c) for c in g.voronoi_cells(points, BOX)]
        mean = sum(areas) / len(areas)
        return math.sqrt(sum((a - mean) ** 2 for a in areas) / len(areas))

    relaxed = g.lloyd_relax(pts, region, 5, BOX)
    assert len(relaxed) == len(pts)
    assert all(g.point_in_polygon(p, RECT) for p in relaxed)
    assert spread(relaxed) < spread(pts)


# --------------------------------------------------- growing with arcs

def test_grow_square():
    core = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    r = 0.5
    path = g.grow_loop(core, r)
    assert [s[0] for s in path] == ['arc', 'line'] * 4
    assert path_is_closed(path)
    for seg in path:
        if seg[0] == 'arc':
            for pt in seg[1:]:
                corner = min(core, key=lambda c: math.hypot(c[0] - pt[0], c[1] - pt[1]))
                assert abs(math.hypot(corner[0] - pt[0], corner[1] - pt[1]) - r) < 1e-9
    # Area of a shape grown by r: area + perimeter * r + pi * r^2.
    expected = 4.0 + 8.0 * r + math.pi * r * r
    assert abs(g.signed_area(g.path_points(path, arc_steps=400)) - expected) < 1e-4
    assert g.rounded_path(core, r) == path


def test_grow_sharp():
    path = g.grow_loop(RECT, 0.0)
    assert [s[0] for s in path] == ['line'] * 4
    assert path_is_closed(path)


def test_grow_keeps_inward_corners_sharp():
    r = 0.3
    path = g.grow_loop(L_SHAPE, r)
    assert path_is_closed(path)
    assert sum(1 for s in path if s[0] == 'arc') == 5   # the five outward corners
    grown = g.path_points(path, arc_steps=16)
    assert any(abs(p[0] - 4.3) < 1e-9 and abs(p[1] - 4.3) < 1e-9 for p in grown)
    for pt in grown:
        assert abs(g.point_ring_distance(pt, L_SHAPE) - r) < 1e-6


def test_grow_does_not_arc_small_turns_and_never_overshoots():
    r = 0.4
    core = circle(0.0, 0.0, 2.0, steps=90)        # 4 degrees per corner
    path = g.grow_loop(core, r)
    assert all(s[0] == 'line' for s in path) and len(path) == 90
    for pt in g.path_points(path):
        assert g.point_ring_distance(pt, core) <= r + 1e-9
        assert g.point_ring_distance(pt, core) >= r * 0.998


# ------------------------------------------------------------- patterns

def test_same_seed_same_pattern():
    a = g.build_pattern(RECT, [], 30, seed=5)
    b = g.build_pattern(RECT, [], 30, seed=5)
    c = g.build_pattern(RECT, [], 30, seed=6)
    assert a.paths == b.paths
    assert a.paths != c.paths


def test_no_walls_no_margin_fills_the_face():
    for outer in (RECT, L_SHAPE):
        pattern = g.build_pattern(outer, [], 40, seed=2, wall=0.0, margin=0.0,
                                  radius=0.0, min_width=0.0, relax=1)
        total = sum(g.signed_area(g.path_points(p)) for p in pattern.paths)
        assert abs(total - g.signed_area(outer)) < 1e-6


def test_rectangle_pattern():
    wall, margin = 0.3, 0.5
    pattern = g.build_pattern(RECT, [], 30, seed=11, wall=wall, margin=margin,
                              radius=0.15, min_width=0.3, relax=2)
    assert len(pattern.paths) >= 25
    pockets = check_pattern(pattern, RECT, [], wall, margin)
    assert band_share(RECT, [], pockets, margin) > 0.5


def test_corner_radius_is_exact():
    radius = 0.2
    for outer in (RECT, peanut()):
        pattern = g.build_pattern(outer, [], 25, seed=4, wall=0.3, margin=0.4,
                                  radius=radius, min_width=0.0, relax=2)
        arcs = [s for p in pattern.paths for s in p if s[0] == 'arc']
        assert len(arcs) > 50
        for _, start, mid, end in arcs:
            cx, cy = g._circle_centre(start, mid, end)
            assert abs(math.hypot(start[0] - cx, start[1] - cy) - radius) < 1e-6


def test_l_shape_border_is_even():
    wall, margin = 0.25, 0.4
    pattern = g.build_pattern(L_SHAPE, [], 60, seed=9, wall=wall, margin=margin,
                              radius=0.1, min_width=0.2, relax=2)
    assert len(pattern.paths) >= 50
    pockets = check_pattern(pattern, L_SHAPE, [], wall, margin)
    assert band_share(L_SHAPE, [], pockets, margin) > 0.5


def test_curved_outline_border_is_even():
    # The shape that showed the problem: lobes plus an inward-curving waist.
    wall, margin = 0.25, 0.4
    outer = peanut()
    pattern = g.build_pattern(outer, [], 60, seed=3, wall=wall, margin=margin,
                              radius=0.15, min_width=0.2, relax=2)
    assert len(pattern.paths) >= 50
    pockets = check_pattern(pattern, outer, [], wall, margin)
    assert band_share(outer, [], pockets, margin) > 0.5


def test_holes_get_an_even_ring():
    wall, margin = 0.25, 0.4
    holes = [circle(5.0, 3.0, 1.0), circle(1.5, 1.5, 0.3)]
    pattern = g.build_pattern(RECT, holes, 60, seed=13, wall=wall, margin=margin,
                              radius=0.1, min_width=0.2, relax=2)
    assert len(pattern.paths) >= 50
    pockets = check_pattern(pattern, RECT, holes, wall, margin)
    assert band_share(RECT, holes, pockets, margin) > 0.5
    assert band_share(holes[0], [], pockets, margin) > 0.5   # the big hole alone


def test_cell_around_a_hole_is_split_in_two():
    # One cell for the whole plate, with a hole in the middle of it.
    hole = circle(5.0, 3.0, 0.8)
    pattern = g.build_pattern(RECT, [hole], 1, wall=0.3, margin=0.4, radius=0.2,
                              min_width=0.2)
    assert len(pattern.paths) == 2
    pockets = check_pattern(pattern, RECT, [hole], 0.3, 0.4)
    assert all(not g.point_in_polygon((5.0, 3.0), p) for p in pockets)


def test_min_width_filters_small_pockets():
    min_width = 0.8
    kw = dict(seed=21, wall=0.2, margin=0.3, radius=0.1, relax=0)
    loose = g.build_pattern(RECT, [], 80, min_width=0.0, **kw)
    strict = g.build_pattern(RECT, [], 80, min_width=min_width, **kw)
    assert 0 < len(strict.paths) < len(loose.paths)
    for path in strict.paths:   # a circle of that width fits in every pocket
        pocket = g.path_points(path, arc_steps=16)
        assert g.offset_region([pocket], min_width / 2.0 - 0.01)


def test_clockwise_outline_gives_the_same_result():
    a = g.build_pattern(RECT, [], 20, seed=8)
    b = g.build_pattern(RECT[::-1], [], 20, seed=8)
    assert len(a.paths) == len(b.paths) > 0


def test_degenerate_inputs_do_not_crash():
    assert g.build_pattern(RECT, [], 20, margin=5.0).paths == []      # margin too big
    tiny = [(0.0, 0.0), (0.05, 0.0), (0.05, 0.05), (0.0, 0.05)]
    assert g.build_pattern(tiny, [], 20).paths == []                 # face too small
    assert g.build_pattern([(0, 0), (1, 0)], [], 20).paths == []     # not a polygon
    one = g.build_pattern(RECT, [], 1, wall=0.2, margin=0.5, radius=0.2)
    assert len(one.paths) == 1                                       # one big pocket


def test_pattern_reports_its_areas():
    square = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    r = 0.5
    assert abs(g.path_area(g.grow_loop(square, r)) - (4.0 + 8.0 * r + math.pi * r * r)) < 1e-9
    assert abs(g.path_area(g.grow_loop(square, 0.0)) - 4.0) < 1e-12

    hole = circle(5.0, 3.0, 1.0)
    pattern = g.build_pattern(RECT, [hole], 40, seed=2)
    assert abs(pattern.face_area - (60.0 - g.signed_area(hole))) < 1e-9
    flat = sum(g.signed_area(g.path_points(p, arc_steps=200)) for p in pattern.paths)
    assert abs(pattern.pocket_area - flat) < 1e-4
    assert 0.3 < pattern.share < 0.9
    assert pattern.settings['count'] == 40 and pattern.settings['wall'] == 0.2


# ----------------------------------------------------------- simple mode

PLATE = [(0.0, 0.0), (25.0, 0.0), (25.0, 17.0), (0.0, 17.0)]   # 250 x 170 mm


def test_simple_mode_hits_the_asked_share():
    holes = [circle(18.0, 8.0, 2.5), circle(6.0, 9.0, 0.5)]
    for outer, hs, margin, share in ((PLATE, [], 0.4, 0.5),
                                     (PLATE, holes, 0.4, 0.65),
                                     (peanut(), [], 0.3, 0.4),
                                     (L_SHAPE, [], 0.2, 0.55)):
        pattern = g.build_pattern_for_share(outer, hs, margin, share, seed=3)
        assert abs(pattern.share - share) < 0.005, (pattern.share, share)
        assert pattern.settings['target'] == share
        assert (g.SIMPLE_MIN_CELLS <= pattern.settings['count'] <= g.SIMPLE_MAX_CELLS)
        check_pattern(pattern, outer, hs, pattern.settings['wall'], margin)


def test_simple_mode_walls_are_about_as_wide_as_the_border():
    # In the middle range the cell count can be chosen freely, so the walls
    # should land near the border margin.
    for share in (0.6, 0.7, 0.8):
        pattern = g.build_pattern_for_share(PLATE, [], 0.4, share, seed=1)
        assert g.SIMPLE_MIN_CELLS < pattern.settings['count'] < g.SIMPLE_MAX_CELLS
        assert 0.8 * 0.4 < pattern.settings['wall'] < 1.2 * 0.4, pattern.settings
    # Asking for very little pocket runs into the cell limit: thicker walls.
    low = g.build_pattern_for_share(PLATE, [], 0.4, 0.3, seed=1)
    assert low.settings['count'] == g.SIMPLE_MAX_CELLS
    assert low.settings['wall'] > 0.4 and abs(low.share - 0.3) < 0.005


def test_simple_mode_more_share_means_more_pocket():
    shares = [g.build_pattern_for_share(PLATE, [], 0.4, s, seed=2).share
              for s in (0.3, 0.5, 0.7, 0.8)]
    assert shares == sorted(shares)
    assert shares[-1] - shares[0] > 0.4


def test_simple_mode_says_when_the_share_cannot_be_reached():
    # A 1 cm border on a 10 x 6 plate leaves 8 x 4 = 53% of the face at most.
    pattern = g.build_pattern_for_share(RECT, [], 1.0, 0.9, seed=1)
    assert pattern.paths
    assert pattern.share < 32.0 / 60.0
    assert pattern.share > 0.3
    assert pattern.settings['target'] == 0.9


def test_simple_mode_same_seed_same_pattern_and_empty_inputs():
    a = g.build_pattern_for_share(PLATE, [], 0.4, 0.5, seed=4)
    b = g.build_pattern_for_share(PLATE, [], 0.4, 0.5, seed=4)
    c = g.build_pattern_for_share(PLATE, [], 0.4, 0.5, seed=5)
    assert a.paths == b.paths and a.paths != c.paths
    assert g.build_pattern_for_share(RECT, [], 5.0, 0.5).paths == []          # margin too big
    assert g.build_pattern_for_share([(0, 0), (1, 0)], [], 0.1, 0.5).paths == []


# ------------------------------------------------------- edge stitching

def test_chain_polylines_handles_order_and_direction():
    edges = [
        [(0, 0, 0), (5, 0, 0), (10, 0, 0)],
        [(10, 6, 0), (10, 0, 0)],            # reversed
        [(0, 6, 0), (0, 0, 0)],              # out of order
        [(10, 6, 0), (5, 6, 0), (0, 6, 0)],
    ]
    loop = g.chain_polylines(edges)
    assert len(loop) == 6
    assert abs(abs(g.signed_area([(p[0], p[1]) for p in loop])) - 60.0) < 1e-9


def test_chain_polylines_single_closed_edge():
    ring = circle(0, 0, 2, steps=24)
    loop = g.chain_polylines([ring + [ring[0]]])
    assert len(loop) == 24


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
