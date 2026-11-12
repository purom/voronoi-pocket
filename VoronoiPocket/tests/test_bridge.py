"""Smoke tests for core/fusion_bridge.py using a stand-in for the Fusion API.

These only check our own logic (stitching face edges, sharing sketch points
so every outline is closed). They cannot tell whether the real Fusion API
behaves as expected - that needs a run inside Fusion.

Run with pytest, or directly:  python tests/test_bridge.py
"""

import math
import os
import sys
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------------- stand-in "adsk"

class Point3D(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    @staticmethod
    def create(x, y, z):
        return Point3D(x, y, z)


class SketchPoint(object):
    def __init__(self, x, y):
        self.geometry = Point3D(x, y, 0.0)


def _as_sketch_point(value):
    return value if isinstance(value, SketchPoint) else SketchPoint(value.x, value.y)


class Curve(object):
    def __init__(self, start, end):
        self.startSketchPoint = start
        self.endSketchPoint = end


class Lines(object):
    def __init__(self, curves):
        self.curves = curves

    def addByTwoPoints(self, start, end):
        curve = Curve(_as_sketch_point(start), _as_sketch_point(end))
        self.curves.append(curve)
        return curve


class Arcs(object):
    """Every second arc reports its ends swapped, as Fusion is free to do."""

    def __init__(self, curves):
        self.curves = curves

    def addByThreePoints(self, start, mid, end):
        ends = [_as_sketch_point(start), _as_sketch_point(end)]
        if len(self.curves) % 2:
            ends.reverse()
        curve = Curve(ends[0], ends[1])
        self.curves.append(curve)
        return curve


class Sketch(object):
    """``mirror``: the sketch's X axis runs the other way than the model's,
    as Fusion's can on some faces."""

    def __init__(self, offset=(0.0, 0.0), mirror=False):
        self.curves = []
        self.sketchCurves = types.SimpleNamespace(
            sketchLines=Lines(self.curves), sketchArcs=Arcs(self.curves))
        self.isComputeDeferred = False
        self.offset = offset
        self.mirror = mirror
        self.name = ''
        self.saved = {}
        self.attributes = types.SimpleNamespace(
            add=lambda group, name, value: self.saved.update({name: value}))
        self.transform = types.SimpleNamespace(
            getAsCoordinateSystem=lambda: (None, None, None, Vector(1.0)))

    @property
    def profiles(self):
        return ['profile'] * 3

    def modelToSketchSpace(self, point):
        x = point.x - self.offset[0]
        return Point3D(-x if self.mirror else x, point.y - self.offset[1], 0.0)


class Evaluator(object):
    def __init__(self, points):
        self.points = points

    def getParameterExtents(self):
        return True, 0.0, 1.0

    def getStrokes(self, start, end, tolerance):
        return True, [Point3D(*p) for p in self.points]


_edge_numbers = [100]


def _edge(*points):
    _edge_numbers[0] += 1
    return types.SimpleNamespace(evaluator=Evaluator(points), tempId=_edge_numbers[0],
                                 nativeObject=None)


class EdgeList(list):
    @property
    def count(self):
        return len(self)


class Coordinates(list):
    """Stands in for CustomGraphicsCoordinates: a list that can carry colours."""
    colors = None


class ObjectCollection(list):
    @staticmethod
    def create():
        return ObjectCollection()

    def add(self, item):
        self.append(item)

    @property
    def count(self):
        return len(self)


class Vector(object):
    def __init__(self, z):
        self.z = z

    def dotProduct(self, other):
        return self.z * other.z


_adsk = types.ModuleType('adsk')
_adsk.core = types.ModuleType('adsk.core')
_adsk.fusion = types.ModuleType('adsk.fusion')
_adsk.core.Point3D = Point3D
_adsk.core.ObjectCollection = ObjectCollection
_adsk.core.ValueInput = types.SimpleNamespace(
    createByReal=lambda v: v, createByString=lambda s: 'expression:' + s)
_adsk.core.Vector3D = types.SimpleNamespace(
    create=lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z))
_adsk.core.SurfaceTypes = types.SimpleNamespace(PlaneSurfaceType='plane')
_adsk.core.Color = types.SimpleNamespace(create=lambda r, g, b, a: (r, g, b, a))
_adsk.fusion.CustomGraphicsCoordinates = types.SimpleNamespace(
    create=lambda values: Coordinates(values))
_adsk.fusion.CustomGraphicsSolidColorEffect = types.SimpleNamespace(create=lambda colour: colour)
_adsk.fusion.CustomGraphicsVertexColorEffect = types.SimpleNamespace(create=lambda: 'by point')
_adsk.fusion.DesignTypes = types.SimpleNamespace(ParametricDesignType='parametric')
_adsk.fusion.ExtentDirections = types.SimpleNamespace(
    NegativeExtentDirection='neg', PositiveExtentDirection='pos')
_adsk.fusion.FeatureOperations = types.SimpleNamespace(CutFeatureOperation='cut')
_adsk.fusion.DistanceExtentDefinition = types.SimpleNamespace(create=lambda v: v)
_adsk.fusion.FeatureHealthStates = types.SimpleNamespace(HealthyFeatureHealthState='ok')
sys.modules.setdefault('adsk', _adsk)
sys.modules.setdefault('adsk.core', _adsk.core)
sys.modules.setdefault('adsk.fusion', _adsk.fusion)

from core import fusion_bridge as bridge  # noqa: E402
from core import geometry as g  # noqa: E402

RECT = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]


# ------------------------------------------------------------------ tests

def _check_closed_loops(sketch, paths):
    """Every sketch point must join exactly two curves."""
    assert len(sketch.curves) == sum(len(p) for p in paths)
    uses = {}
    for curve in sketch.curves:
        for point in (curve.startSketchPoint, curve.endSketchPoint):
            uses[id(point)] = uses.get(id(point), 0) + 1
    assert uses and all(count == 2 for count in uses.values())
    assert sketch.isComputeDeferred is False


def test_rounded_outlines_are_drawn_closed():
    pattern = g.build_pattern(RECT, [], 30, seed=3, radius=0.15)
    assert pattern.paths
    sketch = Sketch()
    bridge.draw_paths(sketch, pattern.paths)
    _check_closed_loops(sketch, pattern.paths)


def test_sharp_outlines_are_drawn_closed():
    pattern = g.build_pattern(RECT, [], 30, seed=3, radius=0.0)
    assert pattern.paths
    sketch = Sketch()
    bridge.draw_paths(sketch, pattern.paths)
    _check_closed_loops(sketch, pattern.paths)


def test_drawn_points_match_the_pattern():
    path = g.rounded_path([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)], 0.5)
    sketch = Sketch()
    bridge.draw_paths(sketch, [path])
    wanted = sorted((round(s[1][0], 9), round(s[1][1], 9)) for s in path)
    points = {}
    for curve in sketch.curves:
        for point in (curve.startSketchPoint, curve.endSketchPoint):
            points[id(point)] = (round(point.geometry.x, 9), round(point.geometry.y, 9))
    assert sorted(points.values()) == wanted


def test_face_outline_reads_outer_loop_and_holes():
    outer = types.SimpleNamespace(isOuter=True, edges=[
        _edge((1, 1, 5), (11, 1, 5)),
        _edge((11, 7, 5), (11, 1, 5)),             # reversed edge
        _edge((11, 7, 5), (6, 7, 5), (1, 7, 5)),
        _edge((1, 7, 5), (1, 1, 5)),
    ])
    ring = [(6 + math.cos(a), 4 + math.sin(a), 5)
            for a in [2 * math.pi * k / 16 for k in range(17)]]
    hole = types.SimpleNamespace(isOuter=False, edges=[_edge(*ring)])
    face = types.SimpleNamespace(loops=[hole, outer])

    frame = ((1.0, 1.0, 5.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    outline, holes = bridge.face_outline(face, frame)
    assert abs(abs(g.signed_area(outline)) - 60.0) < 1e-9
    assert g.bounding_box(outline) == (0.0, 0.0, 10.0, 6.0)
    assert len(holes) == 1 and len(holes[0]) == 16
    assert g.point_in_polygon((5.0, 3.0), holes[0])


# ------------------------------------------------------------ cutting

class FakeBody(object):
    def __init__(self):
        self.isSolid = True
        self.volume = 100.0


class FakeFeature(object):
    def __init__(self, body, removed):
        self.body = body
        self.removed = removed
        self.healthState = 'ok'
        self.errorOrWarningMessage = ''
        body.volume -= removed

    def deleteMe(self):
        self.body.volume += self.removed


class FakeCutInput(object):
    def setOneSideExtent(self, extent, direction):
        self.extent = extent
        self.direction = direction


class FakeExtrudes(object):
    """``behaviour`` maps a direction to: volume removed, or an exception."""

    def __init__(self, body, behaviour):
        self.body = body
        self.behaviour = behaviour
        self.tried = []
        self.distances = []

    def createInput(self, profiles, operation):
        return FakeCutInput()

    def add(self, cut):
        self.tried.append(cut.direction)
        self.distances.append(cut.extent)
        outcome = self.behaviour[cut.direction]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeFeature(self.body, outcome)


def _cut_setup(behaviour, sketch_z=1.0, face_z=1.0):
    body = FakeBody()
    extrudes = FakeExtrudes(body, behaviour)
    component = types.SimpleNamespace(
        bRepBodies=[body], features=types.SimpleNamespace(extrudeFeatures=extrudes))
    face = types.SimpleNamespace(
        body=body, pointOnFace=None,
        evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p: (True, Vector(face_z))))
    transform = types.SimpleNamespace(
        getAsCoordinateSystem=lambda: (None, None, None, Vector(sketch_z)))
    sketch = types.SimpleNamespace(profiles=['p1', 'p2'], transform=transform)
    return component, sketch, face, extrudes, body


def test_cut_goes_into_the_body_first_try():
    component, sketch, face, extrudes, body = _cut_setup({'neg': 5.0, 'pos': 0.0})
    notes = []
    bridge.cut_pockets(component, sketch, face, 0.5, notes)
    assert extrudes.tried == ['neg']
    assert body.volume == 95.0


def test_cut_that_removes_nothing_is_undone_and_flipped():
    # Fusion accepts the first cut but it removes no material.
    component, sketch, face, extrudes, body = _cut_setup({'neg': 0.0, 'pos': 5.0})
    notes = []
    bridge.cut_pockets(component, sketch, face, 0.5, notes)
    assert extrudes.tried == ['neg', 'pos']
    assert body.volume == 95.0
    assert any('removed 0.000000' in n for n in notes)


def test_sketch_pointing_into_the_body_tries_positive_first():
    component, sketch, face, extrudes, body = _cut_setup(
        {'neg': 0.0, 'pos': 5.0}, sketch_z=-1.0)
    bridge.cut_pockets(component, sketch, face, 0.5, [])
    assert extrudes.tried == ['pos']


def test_cut_error_then_success():
    component, sketch, face, extrudes, body = _cut_setup(
        {'neg': RuntimeError('No target body found to cut'), 'pos': 5.0})
    notes = []
    bridge.cut_pockets(component, sketch, face, 0.5, notes)
    assert extrudes.tried == ['neg', 'pos']
    assert body.volume == 95.0


def test_cut_fails_both_ways_with_details():
    component, sketch, face, extrudes, body = _cut_setup(
        {'neg': 0.0, 'pos': RuntimeError('boom')})
    try:
        bridge.cut_pockets(component, sketch, face, 0.5, [])
    except bridge.PatternError as error:
        text = str(error)
        assert 'negative direction: removed' in text and 'boom' in text
    else:
        raise AssertionError('expected PatternError')
    assert body.volume == 100.0   # the empty cut was deleted again


# ------------------------------------------------- depth parameter, arrow

class FakeParameters(object):
    def __init__(self, names=()):
        self.names = set(names)
        self.added = []

    def itemByName(self, name):
        return name if name in self.names else None

    def add(self, name, value, units, comment):
        self.names.add(name)
        self.added.append((name, value, units))


def _design(existing=(), kind='parametric'):
    parameters = FakeParameters(existing)
    return types.SimpleNamespace(
        designType=kind, allParameters=parameters, userParameters=parameters,
        unitsManager=types.SimpleNamespace(defaultLengthUnits='mm'))


def test_depth_parameter_is_created_with_the_typed_expression():
    design = _design()
    notes = []
    name = bridge.create_depth_parameter(design, 'thickness / 2', notes)
    assert name == 'VoronoiDepth'
    assert design.userParameters.added == [
        ('VoronoiDepth', 'expression:thickness / 2', 'mm')]


def test_depth_parameter_gets_a_free_name():
    design = _design(existing=['VoronoiDepth', 'VoronoiDepth_2'])
    assert bridge.create_depth_parameter(design, '5 mm', []) == 'VoronoiDepth_3'


def test_no_depth_parameter_without_a_timeline():
    design = _design(kind='direct')
    notes = []
    assert bridge.create_depth_parameter(design, '5 mm', notes) is None
    assert design.userParameters.added == [] and notes


def test_cut_uses_the_parameter_name_as_its_distance():
    component, sketch, face, extrudes, body = _cut_setup({'neg': 0.0, 'pos': 5.0})
    bridge.cut_pockets(component, sketch, face, 0.5, [], 'VoronoiDepth')
    assert extrudes.distances == ['expression:VoronoiDepth'] * 2   # both tries
    component, sketch, face, extrudes, body = _cut_setup({'neg': 5.0, 'pos': 0.0})
    bridge.cut_pockets(component, sketch, face, 0.5, [])
    assert extrudes.distances == [0.5]


def test_depth_arrow_points_into_the_body():
    face = types.SimpleNamespace(
        pointOnFace='a point',
        evaluator=types.SimpleNamespace(
            getNormalAtPoint=lambda p: (True, types.SimpleNamespace(x=0.0, y=0.6, z=0.8))))
    origin, direction = bridge.inward_arrow(face)
    assert origin == 'a point'
    assert (direction.x, direction.y, direction.z) == (-0.0, -0.6, -0.8)
    broken = types.SimpleNamespace(
        pointOnFace=None,
        evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p: (False, None)))
    assert bridge.inward_arrow(broken) is None


# ------------------------------------- a whole plate: plan, outlines, build

class FakeGraphics(object):
    """Stands in for a component's customGraphicsGroups."""

    def __init__(self):
        self.groups = []

    def add(self):
        group = types.SimpleNamespace(isValid=True, lines=[], meshes=[], drawn=[], id='')

        def add_lines(coords, indices, strip):
            look = types.SimpleNamespace()
            group.lines.append((list(coords), list(indices), strip))
            group.drawn.append(look)
            return look

        def add_mesh(coords, indices, normals, normal_indices):
            group.meshes.append((list(coords), list(indices), list(coords.colors)))
            return types.SimpleNamespace()

        group.addLines = add_lines
        group.addMesh = add_mesh
        group.deleteMe = lambda: (setattr(group, 'isValid', False), self.groups.remove(group))
        self.groups.append(group)
        return group


def _plate(mirror=False, temp_id=7):
    """A 10 x 6 plate whose top face is at z = 5, starting at (1, 1)."""
    corners = [(1, 1, 5), (11, 1, 5), (11, 7, 5), (1, 7, 5)]
    loop = types.SimpleNamespace(isOuter=True, edges=[
        _edge(corners[i], corners[(i + 1) % 4]) for i in range(4)])
    body = FakeBody()
    extrudes = FakeExtrudes(body, {'neg': 5.0, 'pos': 0.0})
    sketches = []

    def add_sketch(face):
        sketches.append(Sketch(offset=(1.0, 1.0), mirror=mirror))
        return sketches[-1]

    component = types.SimpleNamespace(
        bRepBodies=[body], customGraphicsGroups=FakeGraphics(),
        features=types.SimpleNamespace(extrudeFeatures=extrudes),
        sketches=types.SimpleNamespace(addWithoutEdges=add_sketch))
    body.parentComponent = component
    plane = types.SimpleNamespace(
        surfaceType='plane',
        origin=types.SimpleNamespace(x=1.0, y=1.0, z=5.0),
        normal=types.SimpleNamespace(x=0.0, y=0.0, z=2.0),
        uDirection=types.SimpleNamespace(x=3.0, y=0.0, z=0.5))   # not square on purpose
    face = types.SimpleNamespace(
        nativeObject=None, geometry=plane, loops=[loop], body=body,
        tempId=temp_id, edges=EdgeList(loop.edges), area=60.0,
        centroid=types.SimpleNamespace(x=6.0, y=4.0, z=5.0), pointOnFace='p',
        evaluator=types.SimpleNamespace(
            getNormalAtPoint=lambda p: (True, types.SimpleNamespace(x=0.0, y=0.0, z=1.0))))
    return face, component, sketches, extrudes


PARAMS = {'simple': False, 'margin': 0.4, 'share': 0.5, 'count': 20, 'relax': 2,
          'wall': 0.3, 'radius': 0.15, 'min_width': 0.2, 'seed': 4,
          'depth': 0.5, 'depth_expression': '5 mm'}


def test_frame_is_square_and_outline_is_read_in_it():
    face = _plate()[0]
    origin, u, v, n = bridge.face_frame(face)
    assert origin == (1.0, 1.0, 5.0)
    assert u == (1.0, 0.0, 0.0) and v == (0.0, 1.0, 0.0) and n == (0.0, 0.0, 1.0)
    outer, holes = bridge.face_outline(face, (origin, u, v, n))
    assert g.bounding_box(outer) == (0.0, 0.0, 10.0, 6.0) and holes == []


def test_pattern_is_worked_out_once_while_only_depth_changes():
    bridge.clear_cache()
    face = _plate()[0]
    notes = []
    frame, first = bridge.plan(face, PARAMS, notes)
    deeper = dict(PARAMS, depth=2.0, depth_expression='20 mm')
    assert bridge.plan(face, deeper, notes)[1] is first
    assert notes == ['pattern computed', 'pattern reused']
    assert bridge.plan(face, dict(PARAMS, wall=0.5), notes)[1] is not first   # wall changed
    assert bridge.plan(_plate(temp_id=8)[0], dict(PARAMS, wall=0.5), notes)[1] is not None
    assert notes[2:] == ['pattern computed', 'pattern computed']             # other face
    simple = dict(PARAMS, simple=True)
    pattern = bridge.plan(face, simple, [])[1]
    assert abs(pattern.share - 0.5) < 0.005
    assert bridge.plan(face, dict(simple, wall=9.9), [])[1] is pattern       # wall unused there
    bridge.clear_cache()


def test_outline_preview_draws_closed_loops_just_above_the_face():
    bridge.clear_cache()
    face, component, sketches, extrudes = _plate()
    result = bridge.preview_outlines(face, PARAMS)
    assert sketches == [] and extrudes.tried == []        # nothing added to the design
    assert result.sketch is None and result.pocket_count >= 15
    assert 0.2 < result.share < 0.8

    group, = component.customGraphicsGroups.groups
    (coords, indices, strip), = group.lines
    assert strip is False and len(coords) % 3 == 0 and len(indices) % 2 == 0
    points = [tuple(coords[i:i + 3]) for i in range(0, len(coords), 3)]
    assert all(abs(p[2] - (5.0 + bridge.PREVIEW_LIFT)) < 1e-12 for p in points)
    assert all(1.4 - 1e-9 <= p[0] <= 10.6 + 1e-9 and 1.4 - 1e-9 <= p[1] <= 6.6 + 1e-9
               for p in points)                           # inside the 0.4 margin
    ends = {}
    for k in range(0, len(indices), 2):                   # each point: one line in, one out
        ends[indices[k]] = ends.get(indices[k], 0) + 1
        ends[indices[k + 1]] = ends.get(indices[k + 1], 0) + 1
    assert len(ends) == len(points) and set(ends.values()) == {2}

    bridge.preview_outlines(face, dict(PARAMS, depth=3.0))
    assert len(component.customGraphicsGroups.groups) == 1   # the old one was removed
    bridge.clear_preview()
    assert component.customGraphicsGroups.groups == []
    bridge.clear_cache()


def test_build_draws_the_same_pattern_in_a_straight_or_mirrored_sketch():
    for mirror in (False, True):
        bridge.clear_cache()
        face, component, sketches, extrudes = _plate(mirror=mirror)
        design = _design()
        result = bridge.build(design, face, PARAMS, make_pocket=True, final=True)
        sketch, = sketches
        pattern = bridge.plan(face, PARAMS, [])[1]
        assert result.pocket_count == len(pattern.paths) >= 15
        _check_closed_loops(sketch, pattern.paths)
        # Every drawn point is a pattern point, mirrored when the sketch is.
        wanted = set()
        for path in pattern.paths:
            for seg in path:
                x, y = seg[1]
                wanted.add((round(-x if mirror else x, 9), round(y, 9)))
        drawn = set()
        for curve in sketch.curves:
            for point in (curve.startSketchPoint, curve.endSketchPoint):
                drawn.add((round(point.geometry.x, 9), round(point.geometry.y, 9)))
        assert drawn == wanted
        assert extrudes.distances == ['expression:VoronoiDepth']
        assert design.userParameters.added[0][:2] == ('VoronoiDepth', 'expression:5 mm')
        assert 'chosen' in sketch.saved['params']
    bridge.clear_cache()


def test_preview_build_makes_no_parameter():
    bridge.clear_cache()
    face, component, sketches, extrudes = _plate()
    design = _design()
    bridge.build(design, face, PARAMS, make_pocket=True, final=False)
    assert design.userParameters.added == [] and extrudes.distances == [0.5]
    bridge.build(design, face, PARAMS, make_pocket=False, final=True)
    assert len(sketches) == 2 and extrudes.tried == ['neg']   # second run: sketch only
    # A sketch that was cut is hidden, as Fusion's own Extrude does. A sketch
    # made on its own stays visible.
    assert sketches[0].isLightBulbOn is False
    assert not hasattr(sketches[1], 'isLightBulbOn')
    assert bridge.cached_outline()[0] and bridge.cached_outline()[1] == []
    bridge.clear_cache()
    assert bridge.cached_outline() is None


# ------------------------------------------------------ stress-driven mode

# The plate's edges, in the order _plate() makes them: bottom, right, top, left.
BOTTOM, RIGHT, TOP, LEFT = 0, 1, 2, 3

STRESS = dict(PARAMS, stress=True, count=30, wall_min=0.1, size_ratio=2.0,
              force=600.0, force_angle=0.0, thickness=0.5, yield_mpa=4.0,
              safety=1.0, poisson=0.33)


def _stress_plate():
    """The plate, held along its left edge and pulled on its right edge."""
    bridge.clear_cache()
    bridge.STRESS_MESH_PARTS = 22.0   # a coarse mesh keeps these tests quick
    face, component, sketches, extrudes = _plate()
    setup = {'held': [face.edges[LEFT]], 'loaded': [face.edges[RIGHT]]}
    return face, component, sketches, extrudes, setup


def test_only_edges_of_the_face_are_used():
    face = _plate()[0]
    frame = bridge.face_frame(face)
    stranger = _edge((0, 0, 0), (1, 0, 0))
    proxy = types.SimpleNamespace(nativeObject=face.edges[TOP])   # picked in an assembly
    lines, ids = bridge.edge_polylines(
        face, [face.edges[RIGHT], stranger, proxy, face.edges[RIGHT]], frame)
    assert lines == [[(10.0, 0.0), (10.0, 6.0)], [(10.0, 6.0), (0.0, 6.0)]]
    assert ids == tuple(sorted((face.edges[RIGHT].tempId, face.edges[TOP].tempId)))


def test_stress_is_worked_out_once_per_load():
    face, component, sketches, extrudes, setup = _stress_plate()
    notes = []
    frame, pattern = bridge.plan(face, STRESS, notes, setup)
    assert notes[0].startswith('stress computed') and notes[1] == 'pattern computed'
    assert pattern.paths and pattern.settings['size_ratio'] == 2.0
    # 600 N on a 6 cm x 0.5 cm section is 200 N/cm^2 = 2 MPa.
    result = bridge.BuildResult()
    bridge._fill_result(result, pattern)
    assert 1.9 < result.stress_peak_mpa < 3.2

    notes = []
    bridge.plan(face, dict(STRESS, depth=2.0), notes, setup)          # only depth changed
    assert notes == ['stress reused', 'pattern reused']
    notes = []
    bridge.plan(face, dict(STRESS, count=40), notes, setup)           # pattern value changed
    assert notes == ['stress reused', 'pattern computed']
    notes = []
    weaker = bridge.plan(face, dict(STRESS, yield_mpa=2.5), notes, setup)[1]
    assert notes == ['stress reused', 'pattern computed']
    assert weaker.share < pattern.share                               # weaker material: more wall
    notes = []
    bridge.plan(face, dict(STRESS, force=900.0), notes, setup)        # load changed
    assert notes[0].startswith('stress computed') and notes[1] == 'pattern computed'
    notes = []
    bridge.plan(face, dict(STRESS, force=900.0, force_angle=1.5), notes, setup)
    assert notes[0].startswith('stress computed')
    notes = []
    other = {'held': [face.edges[BOTTOM]], 'loaded': [face.edges[RIGHT]]}
    bridge.plan(face, dict(STRESS, force=900.0, force_angle=1.5), notes, other)
    assert notes[0].startswith('stress computed')                     # other edges held

    # Back to an ordinary pattern: no stress left in the result.
    plain = bridge.BuildResult()
    bridge._fill_result(plain, bridge.plan(face, PARAMS, [])[1])
    assert plain.stress_peak_mpa is None
    bridge.clear_cache()


def test_stress_needs_held_and_loaded_edges():
    face, component, sketches, extrudes, setup = _stress_plate()
    for bad, word in (({'held': [], 'loaded': setup['loaded']}, 'held'),
                      ({'held': setup['held'], 'loaded': []}, 'force'),
                      ({'held': setup['held'], 'loaded': setup['held']}, 'both'),
                      (None, 'held')):
        try:
            bridge.plan(face, STRESS, [], bad)
        except bridge.PatternError as error:
            assert word in str(error), str(error)
        else:
            raise AssertionError('expected PatternError')
    # A force nothing can carry leaves no pocket; that is said plainly.
    try:
        bridge.preview_outlines(face, dict(STRESS, force=1.0e7), setup)
    except bridge.PatternError as error:
        assert 'solid' in str(error)
    else:
        raise AssertionError('expected PatternError')
    group, = component.customGraphicsGroups.groups   # the stress picture stays up
    assert group.lines
    bridge.clear_preview()
    bridge.clear_cache()


def test_stress_preview_shows_edges_force_and_colours():
    face, component, sketches, extrudes, setup = _stress_plate()
    result = bridge.preview_outlines(face, STRESS, setup, colours='stress')
    assert result.pocket_count > 10 and result.stress_peak_mpa > 1.9
    assert not [note for note in result.notes if 'not drawn' in note], result.notes
    group, = component.customGraphicsGroups.groups

    # Colours: one triangle mesh with a colour for every point, below the lines.
    (coords, indices, colours), = group.meshes
    field = bridge._cache['field']
    assert len(coords) == 3 * len(field.mesh.nodes) and len(colours) == 4 * len(field.mesh.nodes)
    assert len(indices) == 3 * len(field.mesh.triangles)
    assert all(0 <= value <= 255 for value in colours)
    assert all(abs(coords[k] - (5.0 + 0.5 * bridge.PREVIEW_LIFT)) < 1e-12
               for k in range(2, len(coords), 3))

    # Lines: held edge, loaded edge, force arrow, pocket outlines.
    held, loaded, arrow, outlines = group.lines
    assert held[0][0::3] == [1.0, 1.0] and loaded[0][0::3] == [11.0, 11.0]
    assert [look.color[:3] for look in group.drawn[:3]] == [
        (30, 170, 70), (220, 40, 40), (220, 40, 40)]
    xs, ys = arrow[0][0::3], arrow[0][1::3]
    assert xs[0] == 11.0 and xs[1] > xs[0] and ys[0] == ys[1]      # points along +u
    assert len(outlines[1]) > 100

    # Without colours, and with the force turned a quarter: the arrow follows.
    # (A sideways force bends the plate, which loads it far more: use less.)
    bridge.preview_outlines(face, dict(STRESS, force=40.0, force_angle=math.pi / 2), setup,
                            colours=None)
    group, = component.customGraphicsGroups.groups
    assert group.meshes == []
    xs, ys = group.lines[2][0][0::3], group.lines[2][0][1::3]
    assert abs(xs[1] - xs[0]) < 1e-9 and ys[1] > ys[0]
    bridge.clear_preview()
    bridge.clear_cache()


def test_stress_colours_run_from_blue_to_red():
    assert bridge.stress_colour(0.0) == (30, 60, 200)
    assert bridge.stress_colour(1.0) == bridge.stress_colour(5.0) == (215, 40, 40)
    assert bridge.stress_colour(-1.0) == (30, 60, 200)
    middle = bridge.stress_colour(0.5)
    assert middle[1] > middle[0] and middle[1] > middle[2]         # green


def test_safety_colours_run_from_red_to_blue():
    red, blue = (215, 40, 40), (30, 60, 200)
    assert bridge.safety_colour(0.4) == bridge.safety_colour(1.0) == red     # not safe
    assert bridge.safety_colour(8.0) == bridge.safety_colour(50.0) == blue   # plenty
    assert bridge.safety_colour(float('inf')) == blue                        # no stress at all
    assert bridge.safety_colour(2.0) == bridge.stress_colour(2.0 / 3.0)      # towards yellow
    four = bridge.safety_colour(4.0)
    assert four[1] > four[0]                                                 # green side


def _vertex(x, y, z):
    return types.SimpleNamespace(geometry=types.SimpleNamespace(x=x, y=y, z=z), nativeObject=None)


def test_forces_at_points():
    face, component, sketches, extrudes, setup = _stress_plate()
    frame = bridge.face_frame(face)
    outline = bridge.face_outline(face, frame)

    corner = _vertex(11.0, 7.0, 5.0)                       # a corner of the face
    sketch_point = types.SimpleNamespace(                  # a sketch point in the middle:
        geometry=types.SimpleNamespace(x=99.0, y=99.0, z=0.0),      # its sketch coordinates
        worldGeometry=types.SimpleNamespace(x=6.0, y=4.0, z=5.0),   # where it really is
        nativeObject=None)
    loads = bridge.point_loads([(corner, 300.0, 0.0), (sketch_point, 200.0, math.pi / 2),
                                (corner, 0.0, 1.0)],       # no force: left out
                               frame, outline)
    assert len(loads) == 2
    assert loads[0][0] == (10.0, 6.0) and loads[0][1] == (300.0, 0.0)
    assert loads[1][0] == (5.0, 3.0)
    assert abs(loads[1][1][0]) < 1e-9 and abs(loads[1][1][1] - 200.0) < 1e-9

    for bad, word in ((_vertex(30.0, 4.0, 5.0), 'not on the selected face'),   # beside it
                      (_vertex(6.0, 4.0, 4.0), 'not on the selected face'),    # below it
                      (types.SimpleNamespace(nativeObject=None), 'could not be read')):
        try:
            bridge.point_loads([(bad, 100.0, 0.0)], frame, outline)
        except bridge.PatternError as error:
            assert word in str(error) and 'load point 1' in str(error)
        else:
            raise AssertionError('expected PatternError')

    # Points alone are enough: no loaded edge needed, and the edge force is not used.
    points_only = {'held': setup['held'], 'loaded': [],
                   'points': [(corner, 300.0, 0.0), (sketch_point, 200.0, math.pi / 2)]}
    notes = []
    pattern = bridge.plan(face, STRESS, notes, points_only)[1]
    assert notes[0].startswith('stress computed') and pattern.paths
    held, loaded, force, at_points = bridge._cache['marks']
    assert loaded == [] and force == (0.0, 0.0) and len(at_points) == 2
    reaction = bridge._cache['field'].reaction
    assert abs(reaction[0] + 300.0) < 1e-3 and abs(reaction[1] + 200.0) < 1e-3

    # Changing one point's force or direction works the stress out again.
    for change in ((corner, 350.0, 0.0), (corner, 350.0, 0.5)):
        notes = []
        bridge.plan(face, STRESS, notes, dict(points_only, points=[change, points_only['points'][1]]))
        assert notes[0].startswith('stress computed')
    notes = []
    bridge.plan(face, dict(STRESS, depth=3.0), notes,
                dict(points_only, points=[(corner, 350.0, 0.5), points_only['points'][1]]))
    assert notes == ['stress reused', 'pattern reused']

    # With an edge force as well: both act, and every force gets an arrow.
    both = dict(setup, points=[(sketch_point, 200.0, math.pi / 2)])
    result = bridge.preview_outlines(face, STRESS, both, colours='safety')
    assert not [note for note in result.notes if 'not drawn' in note], result.notes
    reaction = bridge._cache['field'].reaction
    assert abs(reaction[0] + 600.0) < 1e-3 and abs(reaction[1] + 200.0) < 1e-3
    group, = component.customGraphicsGroups.groups
    arrows = group.lines[2]
    assert len(arrows[0]) == 2 * 4 * 3 and len(arrows[1]) == 2 * 6     # two arrows
    xs, ys = arrows[0][0::3], arrows[0][1::3]
    assert (xs[4], ys[4]) == (6.0, 4.0) and ys[5] > ys[4]              # at the point, along +v
    # The smaller force gets the shorter arrow.
    assert (ys[5] - ys[4]) < (xs[1] - xs[0])
    # Safety colours: every point of the mesh gets one, and they differ
    # across the plate (the stress is not the same everywhere).
    (coords, indices, colours), = group.meshes
    field = bridge._cache['field']
    assert len(colours) == 4 * len(field.mesh.nodes)
    shades = set(tuple(colours[k:k + 3]) for k in range(0, len(colours), 4))
    assert len(shades) > 10
    # They are the safety colours, not the stress ones: check one point.
    strength = STRESS['yield_mpa'] * bridge.N_PER_CM2_IN_MPA
    assert tuple(colours[0:3]) == bridge.safety_colour(strength / field.node_von_mises[0])
    bridge.clear_preview()
    bridge.clear_cache()
    bridge.STRESS_MESH_PARTS = 65.0


def test_build_with_stress_makes_sketch_and_cut():
    face, component, sketches, extrudes, setup = _stress_plate()
    design = _design()
    result = bridge.build(design, face, STRESS, make_pocket=True, final=True, setup=setup)
    sketch, = sketches
    assert result.pocket_count > 10 and result.stress_peak_mpa > 1.9
    assert len(sketch.curves) == sum(len(path) for path in bridge._cache['pattern'].paths)
    assert extrudes.distances == ['expression:VoronoiDepth']
    assert sketch.isLightBulbOn is False
    bridge.clear_cache()
    bridge.STRESS_MESH_PARTS = 65.0


def test_pocketed_plate_is_checked_when_asked():
    face, component, sketches, extrudes, setup = _stress_plate()
    # Pockets as deep as the plate is thick: cut right through.
    params = dict(STRESS, iterations=1, depth=0.5, cut=True)
    assert bridge.depth_ratio(params) == 1.0
    assert bridge.depth_ratio(dict(params, depth=0.2)) == 0.4
    assert bridge.depth_ratio(dict(params, depth=3.0)) == 1.0          # deeper than the plate
    assert bridge.depth_ratio(dict(params, depth=0.2, cut=False)) == 1.0   # depth not known

    # Not asked to run it: the quick pattern, marked as not checked yet.
    notes = []
    plain = bridge.plan(face, params, notes, setup, run_check=False)[1]
    assert notes[1] == 'pattern computed' and 'checks' not in plain.settings
    result = bridge.BuildResult()
    bridge._fill_result(result, plain, params)
    assert result.check == {'done': False}
    bridge._fill_result(result, plain, dict(params, iterations=0))
    assert result.check == {'done': False}                             # (kept from before)
    fresh = bridge.BuildResult()
    bridge._fill_result(fresh, plain, dict(params, iterations=0))
    assert fresh.check is None

    # Asked: checked and corrected, and kept for the next time.
    notes = []
    checked = bridge.plan(face, params, notes, setup)[1]
    assert notes[0] == 'stress reused' and notes[1].startswith('pattern computed and checked')
    assert checked.settings['checks'] == 2 and checked.pocketed is not None
    assert checked.share < plain.share            # cut through, the first walls were too thin
    result = bridge.BuildResult()
    bridge._fill_result(result, checked, params)
    assert result.check['done'] and result.check['checks'] == 2
    assert result.check['plate_peak_mpa'] >= result.check['wall_peak_mpa'] > 0.0
    notes = []
    assert bridge.plan(face, params, notes, setup, run_check=False)[1] is checked
    assert notes == ['stress reused', 'checked pattern reused']

    # Another depth is another check; until it has run, the quick pattern shows.
    notes = []
    assert bridge.plan(face, dict(params, depth=0.2), notes, setup, run_check=False)[1] is plain
    assert notes == ['stress reused', 'pattern reused']
    shallow = bridge.plan(face, dict(params, depth=0.2), [], setup)[1]
    assert shallow.share > checked.share          # the floor carries part of the load
    # A new load throws the check away with the stress.
    notes = []
    bridge.plan(face, dict(params, force=500.0), notes, setup, run_check=False)
    assert notes[0].startswith('stress computed') and notes[1] == 'pattern computed'
    bridge.clear_cache()
    bridge.STRESS_MESH_PARTS = 65.0


def test_preview_marks_pockets_over_the_limit():
    face, component, sketches, extrudes, setup = _stress_plate()
    params = dict(STRESS, iterations=1, depth=0.5, cut=True)

    # The preview leaves the check alone unless told to run it.
    result = bridge.preview_outlines(face, params, setup, colours='stress')
    assert result.check == {'done': False}
    group, = component.customGraphicsGroups.groups
    assert len(group.lines) == 4
    solid_colours = list(group.meshes[0][2])

    result = bridge.preview_outlines(face, dict(params, check_in_preview=True), setup,
                                     colours='stress')
    assert result.check['done'] and result.check['over_limit'] == 0
    group, = component.customGraphicsGroups.groups
    assert len(group.lines) == 4                  # nothing over the limit, nothing in red
    # The colours now show the plate with its pockets.
    assert list(group.meshes[0][2]) != solid_colours

    # Say two cells were left over the limit: their pockets are outlined in red.
    checked = bridge._cache['checked']
    two = sorted(set(checked.path_cells))[:2]
    checked.settings.update(cells_over=two, over_limit=2)
    result = bridge.preview_outlines(face, dict(params, check_in_preview=True), setup)
    assert result.check['over_limit'] == 2
    group, = component.customGraphicsGroups.groups
    held, loaded, arrow, outlines, marked = group.lines
    assert group.drawn[-1].color[:3] == (230, 30, 30) and group.drawn[-1].weight == 4
    red = sum(len(g.path_points(path, arc_steps=6))
              for path, cell in zip(checked.paths, checked.path_cells) if cell in two)
    assert len(marked[0]) == 3 * red and len(outlines[0]) > len(marked[0])

    # OK runs the check whatever the preview did.
    bridge.clear_preview()
    bridge.clear_cache()
    built = bridge.build(_design(), face, params, make_pocket=True, final=True, setup=setup)
    assert built.check['done'] and built.check['checks'] == 2
    # ...and the real-cut preview only when told to.
    bridge.clear_cache()
    shown = bridge.build(_design(), face, params, make_pocket=True, final=False, setup=setup)
    assert shown.check == {'done': False}
    bridge.clear_cache()
    bridge.STRESS_MESH_PARTS = 65.0


def test_material_is_read_by_name():
    def prop(ident, name, value, units=''):
        return types.SimpleNamespace(id=ident, name=name, value=value, units=units)

    listed = [prop('structural_Density', 'Density', 2.7e-3, 'kg/cm^3'),
              prop('structural_Young_modulus', "Young's Modulus", 6.9e10, 'Pa'),
              prop('structural_Poisson_ratio', "Poisson's Ratio", 0.33),
              prop('physmat_Appearance', 'Appearance', 'Satin'),             # not a number
              prop('structural_Minimum_tensile_yield_strength', 'Yield Strength', 2.75e8, 'Pa'),
              prop('structural_Minimum_tensile_strength', 'Tensile Strength', 3.1e8, 'Pa')]
    properties = types.SimpleNamespace(count=len(listed), item=lambda index: listed[index])
    body = types.SimpleNamespace(material=types.SimpleNamespace(
        name='Aluminum 6061', materialProperties=properties))
    to_mpa = {'Pa': 1e-6, 'MPa': 1.0}
    units = types.SimpleNamespace(convert=lambda value, source, target: value * to_mpa[source])

    notes = []
    name, yield_mpa, poisson = bridge.material_data(body, units, notes)
    assert name == 'Aluminum 6061' and abs(yield_mpa - 275.0) < 1e-9 and poisson == 0.33
    assert len(notes) == 4          # the four stiffness and strength values, for the log

    # As Fusion really reports steel: the unit is named in words, which its
    # own converter refuses. (Seen in the log: 207000.0 [Kilopascal].)
    def refuses(value, source, target):
        raise RuntimeError('The units parameter is not a valid unit string')

    listed[4] = prop('structural_minimum_yield_stress', 'Yield Strength', 207000.0, 'Kilopascal')
    strict = types.SimpleNamespace(convert=refuses)
    assert abs(bridge.material_data(body, strict, [])[1] - 207.0) < 1e-9
    for unit, value, wanted in (('Megapascal', 250.0, 250.0), ('Pascal', 2.5e8, 250.0),
                                ('Gigapascal', 0.25, 250.0), ('psi', 36000.0, 248.2)):
        listed[4] = prop('structural_minimum_yield_stress', 'Yield Strength', value, unit)
        assert abs(bridge.material_data(body, strict, [])[1] - wanted) < 0.1

    # Units Fusion cannot convert: the yield strength is left for the user.
    listed[4] = prop('structural_Minimum_tensile_yield_strength', 'Yield Strength', 2.75e8, 'psi?')
    notes = []
    assert bridge.material_data(body, units, notes)[1] is None
    assert any('not converted' in note for note in notes)
    # No material at all.
    assert bridge.material_data(types.SimpleNamespace(), units, [])[1:] == (None, None)


def test_plate_thickness_from_the_body_box():
    point = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)
    face = types.SimpleNamespace(
        pointOnFace='p',
        evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p: (True, point(0.0, 0.0, 1.0))),
        body=types.SimpleNamespace(boundingBox=types.SimpleNamespace(
            minPoint=point(-5.0, -3.0, 4.2), maxPoint=point(5.0, 3.0, 5.0))))
    assert abs(bridge.plate_thickness(face) - 0.8) < 1e-12
    face.evaluator = types.SimpleNamespace(getNormalAtPoint=lambda p: (False, None))
    assert bridge.plate_thickness(face) is None


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
