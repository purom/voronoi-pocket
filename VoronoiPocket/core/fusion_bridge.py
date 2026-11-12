"""Everything that touches the Fusion API: face -> outline, outline -> sketch,
sketch -> cut. The maths lives in geometry.py.

All lengths here are in centimetres, Fusion's internal unit.
"""

import json
import math
import time

import adsk.core
import adsk.fusion

from . import geometry
from . import stress

# How closely curved face edges are followed when turned into points (cm).
STROKE_TOLERANCE = 0.002

SKETCH_NAME = 'Voronoi Pocket'
ATTRIBUTE_GROUP = 'VoronoiPocket'
# Name of the user parameter that holds the pocket depth. A second pattern
# in the same design gets VoronoiDepth_2, and so on.
DEPTH_PARAMETER = 'VoronoiDepth'


class PatternError(Exception):
    """A problem the user can fix; the message is shown as-is."""


class BuildResult(object):
    def __init__(self):
        self.sketch = None
        self.extrude = None
        self.pocket_count = 0
        self.dropped = 0
        self.share = 0.0     # part of the face that became pocket, 0..1
        self.settings = {}   # cells, wall, radius used (chosen in simple mode)
        self.stress_peak_mpa = None  # highest stress found, stress mode only
        self.notes = []      # what happened, for the log file


# --------------------------------------------------------------------------
# Face -> 2D outline
# --------------------------------------------------------------------------

def native_face(face):
    """The face inside its own component (unwraps an assembly proxy)."""
    return face.nativeObject or face


def _edge_points(edge):
    evaluator = edge.evaluator
    ok, start, end = evaluator.getParameterExtents()
    if not ok:
        raise PatternError('Could not read an edge of the selected face.')
    ok, points = evaluator.getStrokes(start, end, STROKE_TOLERANCE)
    if not ok:
        raise PatternError('Could not read an edge of the selected face.')
    return [(p.x, p.y, p.z) for p in points]


def face_frame(face):
    """A flat coordinate system on the face: (origin, u, v, normal).

    All four are (x, y, z) tuples in model space; u, v and normal are unit
    vectors at right angles. A point (x, y) of the pattern sits at
    origin + x * u + y * v. The pattern is worked out in this frame so it does
    not depend on a sketch existing.
    """
    plane = face.geometry
    origin = (plane.origin.x, plane.origin.y, plane.origin.z)
    n = _unit((plane.normal.x, plane.normal.y, plane.normal.z))
    u = (plane.uDirection.x, plane.uDirection.y, plane.uDirection.z)
    along = u[0] * n[0] + u[1] * n[1] + u[2] * n[2]
    u = _unit((u[0] - along * n[0], u[1] - along * n[1], u[2] - along * n[2]))
    v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])
    return origin, u, v, n


def _unit(vector):
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def face_outline(face, frame):
    """Outer outline and hole outlines of a face, as 2D points in ``frame``."""
    origin, u, v, _ = frame
    outer = None
    holes = []
    for loop in face.loops:
        chains = [_edge_points(edge) for edge in loop.edges]
        points = []
        for x, y, z in geometry.chain_polylines(chains):
            dx, dy, dz = x - origin[0], y - origin[1], z - origin[2]
            points.append((dx * u[0] + dy * u[1] + dz * u[2],
                           dx * v[0] + dy * v[1] + dz * v[2]))
        if loop.isOuter:
            outer = points
        else:
            holes.append(points)
    if not outer:
        raise PatternError('Could not read the outline of the selected face.')
    return outer, holes


# --------------------------------------------------------------------------
# The pattern for a face, kept between previews
# --------------------------------------------------------------------------

# While the dialog is open the face does not change, and while only the depth
# changes neither does the pattern. Reading the face and working out the
# pattern are therefore done once and kept here.
_cache = {}


def clear_cache():
    _cache.clear()


def cached_outline():
    """(outer, holes) of the face last worked on, or None."""
    return _cache.get('outline')


def _face_key(face):
    """Something that tells one face from another, or None if unknown."""
    try:
        centre = face.centroid
        return (face.tempId, face.edges.count, round(face.area, 9),
                round(centre.x, 7), round(centre.y, 7), round(centre.z, 7))
    except Exception:
        return None


def _pattern_key(params):
    """The dialog values the pattern depends on (depth is not one of them)."""
    if params.get('stress'):
        names = ('count', 'seed', 'margin', 'radius', 'min_width',
                 'wall_min', 'size_ratio', 'yield_mpa', 'safety')
        kind = 'stress'
    elif params.get('simple'):
        names = ('margin', 'share', 'seed')
        kind = 'simple'
    else:
        names = ('count', 'seed', 'relax', 'wall', 'margin', 'radius', 'min_width')
        kind = 'all'
    return (kind,) + tuple(round(float(params[n]), 9) for n in names)


# Stress is worked out in N and cm, so it comes out in N/cm^2.
N_PER_CM2_IN_MPA = 100.0
# Triangle size for the stress calculation: the face's diagonal divided by
# this. 65 gives about 2,000 points and 2 to 3 seconds; see stress.py.
STRESS_MESH_PARTS = 65.0
# Pressure units as Fusion's material data names them, in MPa.
PRESSURE_IN_MPA = {
    'pascal': 1e-6, 'pa': 1e-6,
    'kilopascal': 1e-3, 'kpa': 1e-3,
    'megapascal': 1.0, 'mpa': 1.0,
    'gigapascal': 1e3, 'gpa': 1e3,
    'bar': 0.1,
    'psi': 0.00689476, 'pound per square inch': 0.00689476,
    'pounds per square inch': 0.00689476,
    'ksi': 6.89476, 'kip per square inch': 6.89476, 'kips per square inch': 6.89476,
}


def edge_polylines(face, edges, frame):
    """The picked edges as lines of 2D points in ``frame``.

    Returns (polylines, ids). Edges that do not belong to the face are left
    out; ``ids`` identifies the ones used, for telling one choice from another.
    """
    origin, u, v, _ = frame
    own = {}
    for edge in face.edges:
        own[edge.tempId] = edge
    polylines = []
    ids = []
    for picked in edges:
        picked = picked.nativeObject or picked
        edge = own.get(picked.tempId)
        if edge is None or picked.tempId in ids:
            continue
        ids.append(picked.tempId)
        line = []
        for x, y, z in _edge_points(edge):
            dx, dy, dz = x - origin[0], y - origin[1], z - origin[2]
            line.append((dx * u[0] + dy * u[1] + dz * u[2],
                         dx * v[0] + dy * v[1] + dz * v[2]))
        polylines.append(line)
    return polylines, tuple(sorted(ids))


def _point_of(entity):
    """Where a picked point is: (x, y, z), or None if it is not a point."""
    entity = getattr(entity, 'nativeObject', None) or entity
    # A sketch point's own "geometry" is in sketch coordinates; its
    # worldGeometry is where it sits in the model. Vertices and construction
    # points only have "geometry".
    for name in ('worldGeometry', 'geometry'):
        point = getattr(entity, name, None)
        if point is not None and hasattr(point, 'z'):
            return (point.x, point.y, point.z)
    return None


def point_loads(points, frame, outline):
    """Forces at picked points, in the face's frame: [((x, y), (fx, fy))].

    ``points`` is a list of (picked point, force in N, angle in radians).
    Points with no force are skipped. A point that is not on the face is an
    error the user can fix.
    """
    origin, u, v, n = frame
    outer, holes = outline
    loads = []
    for number, (entity, size, angle) in enumerate(points, 1):
        if size <= 0:
            continue
        where = _point_of(entity)
        if where is None:
            raise PatternError('Stress: load point %d could not be read.' % number)
        dx, dy, dz = where[0] - origin[0], where[1] - origin[1], where[2] - origin[2]
        pt = (dx * u[0] + dy * u[1] + dz * u[2], dx * v[0] + dy * v[1] + dz * v[2])
        off_plane = abs(dx * n[0] + dy * n[1] + dz * n[2])
        on_face = geometry.point_in_polygon(pt, outer) and not any(
            geometry.point_in_polygon(pt, hole) for hole in holes)
        on_rim = min(geometry.point_ring_distance(pt, ring) for ring in [outer] + list(holes)) < 1e-3
        if off_plane > 1e-2 or not (on_face or on_rim):
            raise PatternError('Stress: load point %d is not on the selected face.' % number)
        loads.append((pt, (size * math.cos(angle), size * math.sin(angle))))
    return loads


def _stress_field(face, params, setup, notes):
    """The stress in the face for the picked supports and forces, kept until
    one of them changes (it takes a few seconds to work out)."""
    frame = _cache['frame']
    held, held_ids = edge_polylines(face, setup.get('held', ()), frame)
    loaded, loaded_ids = edge_polylines(face, setup.get('loaded', ()), frame)
    if not held:
        raise PatternError('Stress: pick at least one edge of this face that is held.')
    if set(held_ids) & set(loaded_ids):
        raise PatternError('Stress: an edge cannot be both held and loaded.')
    at_points = point_loads(setup.get('points', ()), frame, _cache['outline'])
    if not loaded and not at_points:
        raise PatternError('Stress: pick a loaded edge or a load point for the force to act on.')

    angle = float(params['force_angle'])
    force = (params['force'] * math.cos(angle), params['force'] * math.sin(angle))
    if not loaded:
        force = (0.0, 0.0)  # the edge force has no edge to act on
    key = (held_ids, loaded_ids, round(force[0], 6), round(force[1], 6),
           tuple((round(pt[0], 7), round(pt[1], 7), round(f[0], 6), round(f[1], 6))
                 for pt, f in at_points),
           round(float(params['thickness']), 9), round(float(params['poisson']), 6))
    _cache['marks'] = (held, loaded, force, at_points)
    if _cache.get('field_key') == key:
        notes.append('stress reused')
        return _cache['field']

    outer, holes = _cache['outline']
    # The force is shared between the loaded edges by their length.
    lengths = [sum(math.hypot(line[k + 1][0] - line[k][0], line[k + 1][1] - line[k][1])
                   for k in range(len(line) - 1)) for line in loaded]
    whole = sum(lengths) or 1.0
    loads = [(line, (force[0] * length / whole, force[1] * length / whole))
             for line, length in zip(loaded, lengths)]
    minx, miny, maxx, maxy = geometry.bounding_box(outer)
    try:
        # The stiffness does not change the stress for a given force, only
        # how far things move, so any sensible value will do.
        field = stress.solve_plate(
            outer, holes, held, loads, 7.0e6, params['poisson'],
            thickness=params['thickness'],
            size=math.hypot(maxx - minx, maxy - miny) / STRESS_MESH_PARTS,
            point_loads=at_points)
    except stress.StressError as error:
        raise PatternError('Stress: %s' % error)
    _cache['field_key'] = key
    _cache['field'] = field
    _cache.pop('pattern_key', None)  # the pattern depends on the stress
    notes.append('stress computed: %d points, %d steps, %.2f s, peak %.1f MPa'
                 % (len(field.mesh.nodes), field.steps, field.seconds,
                    field.peak / N_PER_CM2_IN_MPA))
    return field


def plan(face, params, notes, setup=None):
    """(frame, pattern) for a face in its own component, from the cache when
    nothing it depends on has changed.

    ``setup`` holds the picked edges for the stress-driven pattern:
    {'held': [edges], 'loaded': [edges]}.
    """
    face_key = _face_key(face)
    if face_key is None or _cache.get('face') != face_key:
        frame = face_frame(face)
        _cache.clear()
        _cache.update(face=face_key, frame=frame, outline=face_outline(face, frame))

    field = None
    if params.get('stress'):
        field = _stress_field(face, params, setup or {}, notes)
    else:
        _cache.pop('marks', None)

    pattern_key = _pattern_key(params)
    if _cache.get('pattern_key') == pattern_key:
        notes.append('pattern reused')
        return _cache['frame'], _cache['pattern']

    outer, holes = _cache['outline']
    if field is not None:
        allow = params['yield_mpa'] * N_PER_CM2_IN_MPA / params['safety']
        pattern = stress.build_stress_pattern(
            outer, holes, field, allow, params['count'],
            seed=params['seed'], margin=params['margin'], wall_min=params['wall_min'],
            radius=params['radius'], min_width=params['min_width'],
            size_ratio=params['size_ratio'])
    elif params.get('simple'):
        pattern = geometry.build_pattern_for_share(
            outer, holes, params['margin'], params['share'], seed=params['seed'])
    else:
        pattern = geometry.build_pattern(
            outer, holes, params['count'],
            seed=params['seed'], wall=params['wall'], margin=params['margin'],
            radius=params['radius'], min_width=params['min_width'],
            relax=params['relax'])
    _cache['pattern_key'] = pattern_key
    _cache['pattern'] = pattern
    notes.append('pattern computed')
    return _cache['frame'], pattern


def _require_pockets(pattern, params):
    if pattern.paths:
        return
    if params.get('stress'):
        raise PatternError(
            'No pockets fit: with this force and material the whole face has to '
            'stay solid.\n\nCheck the force, the plate thickness and the yield '
            'strength, or lower the safety factor.')
    if params.get('simple'):
        raise PatternError('No pockets fit on this face.\n\nTry a smaller border margin.')
    raise PatternError(
        'No pockets fit on this face with these settings.\n\n'
        'Try a smaller border margin, wall thickness, corner radius or '
        'minimum pocket width, or fewer cells.')


def _fill_result(result, pattern):
    result.pocket_count = len(pattern.paths)
    result.dropped = pattern.dropped
    result.share = pattern.share
    result.settings = dict(pattern.settings)
    field = _cache.get('field') if 'marks' in _cache else None
    if field is not None:
        result.stress_peak_mpa = field.peak / N_PER_CM2_IN_MPA


# --------------------------------------------------------------------------
# What the stress calculation needs from the body
# --------------------------------------------------------------------------

def material_data(body, units_manager, notes):
    """(material name, yield strength in MPa or None, Poisson's ratio or None).

    Fusion's material properties are looked through by name, since their
    identifiers differ between material libraries. Everything found that
    looks like a strength or stiffness goes into ``notes`` for the log.
    """
    name = ''
    yield_mpa = None
    poisson = None
    try:
        material = body.material
        name = material.name
        properties = material.materialProperties
        for index in range(properties.count):
            prop = properties.item(index)
            try:
                label = ('%s %s' % (prop.id, prop.name)).lower()
                value = prop.value
            except Exception:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            units = ''
            try:
                units = prop.units or ''
            except Exception:
                pass
            if any(word in label for word in ('yield', 'poisson', 'young', 'tensile')):
                notes.append('material %s = %s [%s]' % (label.strip(), value, units))
            if 'poisson' in label and 0.0 < value < 0.5:
                poisson = float(value)
            elif 'yield' in label and yield_mpa is None and units:
                converted = None
                # Fusion names the unit in words ("Kilopascal"), which its own
                # converter does not accept, so the usual ones are listed here.
                factor = PRESSURE_IN_MPA.get(units.strip().lower())
                if factor is not None:
                    converted = value * factor
                else:
                    try:
                        converted = units_manager.convert(value, units, 'MPa')
                    except Exception as error:
                        notes.append('yield strength not converted: %s' % error)
                if converted is not None and 1.0 <= converted <= 10000.0:
                    yield_mpa = float(converted)
    except Exception as error:
        notes.append('material not read: %s' % error)
    return name, yield_mpa, poisson


def plate_thickness(face):
    """How thick the body is under a flat face (cm), or None.

    Measured as the size of the body's bounding box along the face's normal,
    which is right for a plate that lies square to the axes and too big for
    one that is turned. The dialog shows the value so it can be corrected.
    """
    try:
        ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if not ok:
            return None
        box = face.body.boundingBox
        low, high = box.minPoint, box.maxPoint
        reach = [x * normal.x + y * normal.y + z * normal.z
                 for x in (low.x, high.x) for y in (low.y, high.y) for z in (low.z, high.z)]
        thickness = max(reach) - min(reach)
        return thickness if thickness > 1e-6 else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Quick preview: pocket outlines drawn as graphics, no sketch and no cut
# --------------------------------------------------------------------------

PREVIEW_ID = 'VoronoiPocketPreview'
PREVIEW_LIFT = 0.005  # cm above the face, so the lines do not flicker in it
_preview_group = [None]


def clear_preview():
    """Remove the outline graphics, if any are showing."""
    group = _preview_group[0]
    _preview_group[0] = None
    if group is None:
        return
    try:
        if group.isValid:
            group.deleteMe()
    except Exception:
        pass


def _add_lines(group, coordinates, indices, colour=(20, 110, 240), weight=2):
    """Draw line pieces; ``indices`` pairs up the points to join."""
    points = adsk.fusion.CustomGraphicsCoordinates.create(coordinates)
    try:
        lines = group.addLines(points, indices, False)
    except TypeError:
        lines = group.addLines(points, indices, False, [])
    try:  # looks only; the lines show without it
        lines.weight = weight
        lines.depthPriority = 1
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(colour[0], colour[1], colour[2], 255))
    except Exception:
        pass
    return lines


def stress_colour(part):
    """Blue (idle) through green and yellow to red (at the limit), as on the
    usual stress plots. ``part`` is the stress divided by what is allowed."""
    part = max(0.0, min(1.0, part))
    stops = ((0.0, (30, 60, 200)), (0.25, (40, 190, 230)), (0.5, (90, 200, 90)),
             (0.75, (240, 220, 60)), (1.0, (215, 40, 40)))
    for (low, first), (high, second) in zip(stops, stops[1:]):
        if part <= high:
            t = (part - low) / (high - low)
            return tuple(int(round(first[k] + t * (second[k] - first[k]))) for k in range(3))
    return stops[-1][1]


def safety_colour(factor):
    """Colour for a safety factor (yield strength divided by stress): red at
    1 or below, yellow around 2, green around 4, blue at 8 and above. Each
    doubling moves the same distance along the scale."""
    if factor <= 1.0:
        return stress_colour(1.0)
    return stress_colour(1.0 - math.log(factor, 2.0) / 3.0)


def _draw_stress_map(group, place, field, colour_of):
    """Colour the face: one small triangle mesh with a colour at every point.
    ``colour_of`` turns the stress at a point into (red, green, blue)."""
    coordinates = []
    colours = []
    for (x, y), level in zip(field.mesh.nodes, field.node_von_mises):
        coordinates.extend(place(x, y, 0.5))
        colours.extend(tuple(colour_of(level)) + (255,))
    indices = [number for triangle in field.mesh.triangles for number in triangle]
    points = adsk.fusion.CustomGraphicsCoordinates.create(coordinates)
    points.colors = colours
    mesh = group.addMesh(points, indices, [], [])
    mesh.color = adsk.fusion.CustomGraphicsVertexColorEffect.create()
    return mesh


def _draw_marks(group, place, marks, reach):
    """Show what the stress calculation was told: held edges in green, loaded
    edges in red, and a red arrow for every force."""
    held, loaded, force, at_points = marks
    for lines, colour in ((held, (30, 170, 70)), (loaded, (220, 40, 40))):
        coordinates = []
        indices = []
        for line in lines:
            first = len(coordinates) // 3
            for x, y in line:
                coordinates.extend(place(x, y, 2.0))
            for k in range(len(line) - 1):
                indices.extend((first + k, first + k + 1))
        if indices:
            _add_lines(group, coordinates, indices, colour, 5)

    # Arrows: (where it starts, the force). One at the middle of each loaded
    # edge for the edge force, one at each load point.
    arrows = []
    if math.hypot(force[0], force[1]) > 0.0:
        for line in loaded:
            start = line[len(line) // 2]
            if len(line) > 2 and math.hypot(line[0][0] - line[-1][0],
                                            line[0][1] - line[-1][1]) < 1e-9:
                start = (sum(p[0] for p in line[:-1]) / (len(line) - 1),   # a ring:
                         sum(p[1] for p in line[:-1]) / (len(line) - 1))   # its centre
            arrows.append((start, force))
    arrows.extend(at_points)
    if not arrows:
        return
    biggest = max(math.hypot(f[0], f[1]) for _, f in arrows)
    coordinates = []
    indices = []
    for (mx, my), (fx, fy) in arrows:
        size = math.hypot(fx, fy)
        if size <= 0.0:
            continue
        dx, dy = fx / size, fy / size
        # A bigger force gets a longer arrow, but a small one stays visible.
        length = 0.18 * reach * (0.5 + 0.5 * size / biggest)
        head = 0.3 * length
        tx, ty = mx + dx * length, my + dy * length
        first = len(coordinates) // 3
        for x, y in ((mx, my), (tx, ty),
                     (tx - head * (dx + 0.5 * dy), ty - head * (dy - 0.5 * dx)),
                     (tx - head * (dx - 0.5 * dy), ty - head * (dy + 0.5 * dx))):
            coordinates.extend(place(x, y, 3.0))
        indices.extend((first, first + 1, first + 1, first + 2, first + 1, first + 3))
    if indices:
        _add_lines(group, coordinates, indices, (220, 40, 40), 4)


def preview_outlines(selected_face, params, setup=None, colours=None):
    """Show the pocket outlines on the face as plain graphics.

    This is what makes the dialog quick: nothing is added to the design, so
    Fusion has no sketch to solve and no body to cut. The graphics stay until
    ``clear_preview`` is called.

    With the stress-driven pattern the held and loaded edges and the forces
    are drawn too, and the face can be coloured: ``colours`` is 'stress' (blue
    idle, red at the allowed stress), 'safety' (by safety factor, red at 1 or
    below, blue at 8 and above) or None.
    """
    clock = time.perf_counter()
    face = native_face(selected_face)
    if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
        raise PatternError('Please select a flat face.')
    result = BuildResult()
    clear_preview()
    frame, pattern = plan(face, params, result.notes, setup)
    maths = time.perf_counter() - clock

    clock = time.perf_counter()
    origin, u, v, _ = frame
    out = (0.0, 0.0, 0.0)
    arrow = inward_arrow(face)
    if arrow:
        out = (-arrow[1].x, -arrow[1].y, -arrow[1].z)

    def place(x, y, layer=1.0):
        """A pattern point in model space, a hair above the face. Higher
        layers lie on top of lower ones."""
        lift = PREVIEW_LIFT * layer
        return (origin[0] + x * u[0] + y * v[0] + lift * out[0],
                origin[1] + x * u[1] + y * v[1] + lift * out[1],
                origin[2] + x * u[2] + y * v[2] + lift * out[2])

    group = face.body.parentComponent.customGraphicsGroups.add()
    _preview_group[0] = group
    try:
        group.id = PREVIEW_ID
    except Exception:
        pass

    marks = _cache.get('marks') if params.get('stress') else None
    if marks:
        minx, miny, maxx, maxy = geometry.bounding_box(_cache['outline'][0])
        try:
            strength = params['yield_mpa'] * N_PER_CM2_IN_MPA
            if colours == 'stress':
                allow = strength / params['safety']
                _draw_stress_map(group, place, _cache['field'],
                                 lambda level: stress_colour(level / allow))
            elif colours == 'safety':
                _draw_stress_map(group, place, _cache['field'],
                                 lambda level: safety_colour(strength / level if level > 0
                                                             else float('inf')))
        except Exception as error:
            result.notes.append('stress map not drawn: %r' % (error,))
        try:
            _draw_marks(group, place, marks, math.hypot(maxx - minx, maxy - miny))
        except Exception as error:
            result.notes.append('stress marks not drawn: %r' % (error,))

    coordinates = []
    indices = []
    for path in pattern.paths:
        first = len(coordinates) // 3
        points = geometry.path_points(path, arc_steps=6)
        for x, y in points:
            coordinates.extend(place(x, y))
        count = len(points)
        for k in range(count):
            indices.append(first + k)
            indices.append(first + (k + 1) % count)
    if indices:
        _add_lines(group, coordinates, indices)

    _fill_result(result, pattern)
    result.notes.append('%d outline points; maths %.2f s, graphics %.2f s'
                        % (len(coordinates) // 3, maths, time.perf_counter() - clock))
    # Raised only now, so the stress picture stays up to show why.
    _require_pockets(pattern, params)
    return result


# --------------------------------------------------------------------------
# Outline -> sketch
# --------------------------------------------------------------------------

def _p3(point):
    return adsk.core.Point3D.create(point[0], point[1], 0.0)


def _nearest_end(arc, target):
    """The arc's end sketch point that sits at ``target`` (start or end)."""
    start = arc.startSketchPoint
    end = arc.endSketchPoint
    ds = math.hypot(start.geometry.x - target[0], start.geometry.y - target[1])
    de = math.hypot(end.geometry.x - target[0], end.geometry.y - target[1])
    return start if ds <= de else end


def _draw_path(lines, arcs, path):
    """Draw one closed pocket outline. Neighbouring curves share end points."""
    count = len(path)
    drawn = [None] * count

    # Arcs first, so the lines can attach to the arcs' end points.
    for i, seg in enumerate(path):
        if seg[0] == 'arc':
            drawn[i] = arcs.addByThreePoints(_p3(seg[1]), _p3(seg[2]), _p3(seg[3]))

    for i, seg in enumerate(path):
        if seg[0] != 'line':
            continue
        before = (i - 1) % count
        after = (i + 1) % count

        start = _p3(seg[1])
        if drawn[before] is not None and before != i:
            if path[before][0] == 'arc':
                start = _nearest_end(drawn[before], seg[1])
            else:
                start = drawn[before].endSketchPoint

        end = _p3(seg[2])
        if drawn[after] is not None and after != i:
            if path[after][0] == 'arc':
                end = _nearest_end(drawn[after], seg[2])
            else:
                end = drawn[after].startSketchPoint

        drawn[i] = lines.addByTwoPoints(start, end)


def draw_paths(sketch, paths):
    """Draw every pocket outline into the sketch."""
    lines = sketch.sketchCurves.sketchLines
    arcs = sketch.sketchCurves.sketchArcs
    # Without this Fusion recomputes the sketch after every single curve.
    sketch.isComputeDeferred = True
    try:
        for path in paths:
            _draw_path(lines, arcs, path)
    finally:
        sketch.isComputeDeferred = False


# --------------------------------------------------------------------------
# Sketch -> cut
# --------------------------------------------------------------------------

def _sketch_normal(sketch):
    """The sketch's Z axis: the direction a positive extrude goes."""
    try:
        return sketch.transform.getAsCoordinateSystem()[-1]
    except Exception:
        return sketch.profiles.item(0).plane.normal


def _sketch_faces_outward(face, sketch):
    """True if the sketch's Z axis points out of the body, False if into it,
    None if that could not be worked out."""
    try:
        ok, outward = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if not ok:
            return None
        return _sketch_normal(sketch).dotProduct(outward) > 0
    except Exception:
        return None


def _solid_volume(component):
    """Total volume of the component's solid bodies (cm^3)."""
    total = 0.0
    for body in component.bRepBodies:
        try:
            if body.isSolid:
                total += body.volume
        except Exception:
            pass
    return total


def _feature_problem(feature):
    """Fusion's error or warning text for a feature, or ''."""
    try:
        if feature.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            return ''
        return feature.errorOrWarningMessage or 'feature is not healthy'
    except Exception:
        return ''


def inward_arrow(face):
    """Where to put the depth arrow for a face: (point on it, direction into
    the body), or None if that cannot be worked out."""
    try:
        origin = face.pointOnFace
        ok, outward = face.evaluator.getNormalAtPoint(origin)
        if not ok:
            return None
        return origin, adsk.core.Vector3D.create(-outward.x, -outward.y, -outward.z)
    except Exception:
        return None


def create_depth_parameter(design, expression, notes):
    """Add a user parameter that holds the pocket depth; returns its name.

    The parameter shows up under User Parameters in Modify > Change
    Parameters. Returns None (and the cut then uses a plain number) in a
    design without a timeline or if Fusion refuses the expression.
    """
    try:
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            notes.append('no depth parameter: design has no timeline')
            return None
        name = DEPTH_PARAMETER
        number = 1
        while design.allParameters.itemByName(name):
            number += 1
            name = '%s_%d' % (DEPTH_PARAMETER, number)
        design.userParameters.add(
            name, adsk.core.ValueInput.createByString(expression),
            design.unitsManager.defaultLengthUnits, 'Voronoi Pocket: pocket depth')
        notes.append('depth parameter %s = %s' % (name, expression))
        return name
    except Exception as error:
        notes.append('no depth parameter: %s' % error)
        return None


def cut_pockets(component, sketch, face, depth, notes, depth_parameter=None):
    """Cut every profile of the sketch into the face's body.

    Fusion sometimes accepts a cut that points away from the body and simply
    removes nothing, so after each try the body volume is checked. If it did
    not go down, the cut is deleted and the other direction is tried.
    """
    profiles = adsk.core.ObjectCollection.create()
    for profile in sketch.profiles:
        profiles.add(profile)
    if profiles.count == 0:
        raise PatternError('The sketch has no closed shapes to cut.')
    notes.append('%d profiles' % profiles.count)

    negative = adsk.fusion.ExtentDirections.NegativeExtentDirection
    positive = adsk.fusion.ExtentDirections.PositiveExtentDirection
    outward = _sketch_faces_outward(face, sketch)
    notes.append('sketch Z points %s' % {True: 'out of the body', False: 'into the body',
                                         None: 'unknown'}[outward])
    order = [positive, negative] if outward is False else [negative, positive]

    body = face.body
    extrudes = component.features.extrudeFeatures
    before = _solid_volume(component)
    failures = []
    for direction in order:
        name = 'negative' if direction == negative else 'positive'
        try:
            cut = extrudes.createInput(
                profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
            if depth_parameter:
                # The cut's distance is the parameter's name, so editing the
                # parameter later changes the pocket depth.
                distance = adsk.core.ValueInput.createByString(depth_parameter)
            else:
                distance = adsk.core.ValueInput.createByReal(depth)
            extent = adsk.fusion.DistanceExtentDefinition.create(distance)
            cut.setOneSideExtent(extent, direction)
            cut.participantBodies = [body]
            feature = extrudes.add(cut)
        except Exception as error:
            failures.append('%s direction: %s' % (name, error))
            continue

        removed = before - _solid_volume(component)
        problem = _feature_problem(feature)
        if removed > 1e-9 and not problem:
            notes.extend(failures)
            notes.append('cut %s direction, removed %.3f cm3' % (name, removed))
            return feature
        failures.append('%s direction: removed %.6f cm3%s'
                        % (name, removed, ', ' + problem if problem else ''))
        try:
            feature.deleteMe()
        except Exception as error:
            failures.append('could not delete the empty cut: %s' % error)

    notes.extend(failures)
    raise PatternError('The pockets could not be cut into the body.\n\n'
                       + '\n'.join(failures))


# --------------------------------------------------------------------------
# The whole operation
# --------------------------------------------------------------------------

def _frame_to_sketch(sketch, frame):
    """A function that turns a pattern point (x, y) into sketch coordinates.

    The sketch's own axes on the face need not match the frame's (they can be
    turned or mirrored), so three points are sent through Fusion once and
    every other point is placed from those.
    """
    origin, u, v, _ = frame

    def through_fusion(point):
        local = sketch.modelToSketchSpace(adsk.core.Point3D.create(*point))
        return (local.x, local.y)

    base = through_fusion(origin)
    along_u = through_fusion((origin[0] + u[0], origin[1] + u[1], origin[2] + u[2]))
    along_v = through_fusion((origin[0] + v[0], origin[1] + v[1], origin[2] + v[2]))
    ax, ay = along_u[0] - base[0], along_u[1] - base[1]
    bx, by = along_v[0] - base[0], along_v[1] - base[1]

    if (abs(math.hypot(ax, ay) - 1.0) > 1e-6 or abs(math.hypot(bx, by) - 1.0) > 1e-6
            or abs(ax * bx + ay * by) > 1e-6):
        # Not expected: the sketch is not a plain turn or mirror of the frame.
        # Ask Fusion for every point instead.
        return lambda pt: through_fusion((origin[0] + pt[0] * u[0] + pt[1] * v[0],
                                          origin[1] + pt[0] * u[1] + pt[1] * v[1],
                                          origin[2] + pt[0] * u[2] + pt[1] * v[2]))
    return lambda pt: (base[0] + pt[0] * ax + pt[1] * bx, base[1] + pt[0] * ay + pt[1] * by)


def build(design, selected_face, params, make_pocket=True, final=True, setup=None):
    """Create the Voronoi sketch on a face and, optionally, cut the pockets.

    ``params`` keys:
        stress            True: the stress-driven pattern (see ``plan`` and
                          ``setup``); uses count, margin, radius, min_width,
                          seed, wall_min, size_ratio, force (N), force_angle
                          (radians, in the face's frame), thickness (cm),
                          yield_mpa, safety, poisson
        simple            True: only margin and share are used for the pattern
        margin            border margin (cm)
        share             simple mode: part of the face to remove, 0..1
        count, relax      advanced mode: cells and smoothing passes
        wall, radius, min_width   advanced mode (cm)
        seed              random seed
        depth             pocket depth (cm)
        depth_expression  the depth as typed, e.g. "10 mm" or "thickness / 2"

    ``final`` is False for the dialog's preview: no user parameter and no
    timeline group are made then, since Fusion throws the preview away.
    """
    clock = time.perf_counter()
    face = native_face(selected_face)
    if face.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
        raise PatternError('Please select a flat face.')
    component = face.body.parentComponent
    result = BuildResult()
    frame, pattern = plan(face, params, result.notes, setup)
    _require_pockets(pattern, params)
    maths = time.perf_counter() - clock

    clock = time.perf_counter()
    # addWithoutEdges: plain add() would copy the face edges into the sketch,
    # and they would become one more (unwanted) profile.
    sketch = component.sketches.addWithoutEdges(face)
    sketch.name = SKETCH_NAME
    to_sketch = _frame_to_sketch(sketch, frame)
    draw_paths(sketch, [[(seg[0],) + tuple(to_sketch(pt) for pt in seg[1:]) for seg in path]
                        for path in pattern.paths])
    try:
        saved = dict(params)
        saved['chosen'] = pattern.settings
        sketch.attributes.add(ATTRIBUTE_GROUP, 'params', json.dumps(saved))
    except Exception:
        pass  # only used for re-editing later
    drawing = time.perf_counter() - clock

    result.sketch = sketch
    _fill_result(result, pattern)
    result.notes.append('%d sketch curves' % sum(len(path) for path in pattern.paths))

    cutting = 0.0
    if make_pocket:
        clock = time.perf_counter()
        parameter = None
        if final and params.get('depth_expression'):
            parameter = create_depth_parameter(design, params['depth_expression'], result.notes)
        try:
            result.extrude = cut_pockets(component, sketch, face, params['depth'],
                                         result.notes, parameter)
        except PatternError:
            if not parameter:
                raise
            # Not expected, but a plain number is better than no pockets.
            result.notes.append('cut through the depth parameter failed; using a plain number')
            try:
                design.userParameters.itemByName(parameter).deleteMe()
            except Exception:
                pass
            result.extrude = cut_pockets(component, sketch, face, params['depth'],
                                         result.notes, None)
        # Fusion's own Extrude hides the sketch it used. Doing the same keeps
        # 1000+ sketch curves from being drawn on top of the finished pockets.
        try:
            sketch.isLightBulbOn = False
        except Exception:
            pass
        if final:
            _group_in_timeline(design, sketch, result.extrude)
        cutting = time.perf_counter() - clock
    result.notes.append('maths %.2f s, drawing %.2f s, cutting %.2f s'
                        % (maths, drawing, cutting))
    return result


def _group_in_timeline(design, sketch, extrude):
    """Fold the sketch and the cut into one timeline group (cosmetic)."""
    try:
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            return
        group = design.timeline.timelineGroups.add(
            sketch.timelineObject.index, extrude.timelineObject.index)
        group.name = SKETCH_NAME
    except Exception:
        pass
