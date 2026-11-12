"""The Voronoi Pocket command: toolbar button, dialog and event handlers."""

import datetime
import json
import os
import time
import traceback

import adsk.core
import adsk.fusion

from ... import config
from ...core import fusion_bridge, settings

_app = adsk.core.Application.get()
_ui = _app.userInterface

# Fusion only keeps weak references to event handlers, so they are stored
# here to stop Python from garbage-collecting them.
_handlers = []
_command_handlers = []

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESOURCES = os.path.join(_HERE, 'resources', '')
# Every preview and run is written here, errors with their full traceback.
_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'voronoi_pocket.log')
# Outline of the face of the last OK (2D points only), for looking into problems.
_FACE_PATH = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'voronoi_last_face.json')

# The dialog's inputs by their id, filled when the dialog is built. The
# inputs live on two tabs, and this is the one place that knows them all.
_inputs = {}
# Last text shown in the status lines (only changed when it differs).
_last_status = [None]
# What was read from the picked body's material.
_material = {'poisson': 0.3}

# Dialog inputs by kind.
_LENGTH_INPUTS = ('margin', 'depth', 'wall', 'radius', 'minWidth', 'wallMin')
_INT_INPUTS = ('seed', 'pocketing', 'cellCount', 'relax', 'iterations')
_BOOL_INPUTS = ('simple', 'cutPockets', 'stressOn', 'checkPreview')
_NUMBER_INPUTS = ('force', 'safety', 'yieldMPa', 'sizeRatio')  # plain numbers, no unit
# Shown only while the stress-driven pattern is switched on.
_STRESS_ONLY = ('stressHelp', 'held', 'loaded', 'force', 'angle', 'loadPoints',
                'thickness', 'material', 'yieldMPa', 'safety', 'wallMin', 'sizeRatio',
                'iterations', 'checkPreview', 'colours')

# Choices of the Colours drop-down on the Stress tab, in order.
_COLOUR_NAMES = ('Stress', 'Safety factor', 'Off')
_COLOUR_KEYS = ('stress', 'safety', None)


def _point_ids(number):
    """Ids of the force and direction inputs of load point ``number`` (1...)."""
    return 'pForce%d' % number, 'pAngle%d' % number

# Choices of the Preview drop-down, in order.
_PREVIEW_OUTLINES, _PREVIEW_FULL, _PREVIEW_OFF = 0, 1, 2
_PREVIEW_NAMES = ('Outlines (fast)', 'Real cut (slow)', 'Off')
# Set if drawing outline graphics ever fails; the real cut is shown instead.
_outlines_broken = [False]


def _inp(input_id):
    return _inputs[input_id]


# --------------------------------------------------------------------------
# Add-in start / stop
# --------------------------------------------------------------------------

def start():
    """Create the command and put its button in Solid > Create."""
    stale = _ui.commandDefinitions.itemById(config.CMD_ID)
    if stale:
        stale.deleteMe()
    definition = _ui.commandDefinitions.addButtonDefinition(
        config.CMD_ID, config.CMD_NAME, config.CMD_DESCRIPTION, _RESOURCES)

    handler = _CommandCreated()
    definition.commandCreated.add(handler)
    _handlers.append(handler)

    panel = _ui.allToolbarPanels.itemById(config.PANEL_ID)
    if panel:
        old = panel.controls.itemById(config.CMD_ID)
        if old:
            old.deleteMe()
        panel.controls.addCommand(definition)


def stop():
    """Remove the button and the command again."""
    panel = _ui.allToolbarPanels.itemById(config.PANEL_ID)
    if panel:
        control = panel.controls.itemById(config.CMD_ID)
        if control:
            control.deleteMe()
    definition = _ui.commandDefinitions.itemById(config.CMD_ID)
    if definition:
        definition.deleteMe()
    del _handlers[:]
    del _command_handlers[:]
    _inputs.clear()


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------

def _keep(item, tooltip=None):
    """Remember an input under its id."""
    if tooltip:
        item.tooltip = tooltip
    _inputs[item.id] = item
    return item


def _add_length(into, input_id, name, units, saved, tooltip):
    value = adsk.core.ValueInput.createByString(saved[input_id])
    item = into.addValueInput(input_id, name, units, value)
    if not item.isValidExpression:
        # A saved expression can name a parameter this design does not have.
        item.expression = config.DEFAULTS[input_id]
    return _keep(item, tooltip)


def _add_number(into, input_id, name, saved, tooltip):
    """A plain number without a unit."""
    item = into.addValueInput(input_id, name, '', adsk.core.ValueInput.createByReal(saved[input_id]))
    return _keep(item, tooltip)


def _add_int(into, input_id, name, low, high, step, saved, tooltip):
    start_value = max(low, min(high, saved[input_id]))
    item = into.addIntegerSpinnerCommandInput(input_id, name, low, high, step, start_value)
    return _keep(item, tooltip)


def _add_bool(into, input_id, name, saved, tooltip):
    return _keep(into.addBoolValueInput(input_id, name, True, '', saved[input_id]), tooltip)


def _add_text(into, input_id, text, rows):
    item = into.addTextBoxCommandInput(input_id, '', text, rows, True)
    item.isFullWidth = True
    return _keep(item)


def _add_edges(into, input_id, name, prompt, tooltip):
    item = into.addSelectionInput(input_id, name, prompt)
    item.addSelectionFilter('Edges')
    item.setSelectionLimits(0, 0)  # any number, none needed
    return _keep(item, tooltip)


def _add_depth(into, saved):
    """The pocket depth: a value box plus a drag arrow on the model."""
    value = adsk.core.ValueInput.createByString(saved['depth'])
    item = into.addDistanceValueCommandInput('depth', 'Pocket depth', value)
    if not item.isValidExpression:
        item.expression = config.DEFAULTS['depth']
    item.minimumValue = 0.0
    item.isMinimumValueInclusive = False
    # The arrow gets its real place once a face is picked (_on_face_picked).
    item.setManipulator(adsk.core.Point3D.create(0, 0, 0),
                        adsk.core.Vector3D.create(0, 0, -1))
    item.isVisible = False
    return _keep(item, 'How deep the pockets are cut into the body. Type a value '
                       'or drag the arrow. The depth is saved as the user '
                       'parameter "VoronoiDepth", so it can be changed later in '
                       'Modify > Change Parameters.')


def _add_angle(into, input_id, name, expression, tooltip):
    """Direction of a force, as an angle in the face's plane, with a handle
    on the model to turn it."""
    value = adsk.core.ValueInput.createByString(expression)
    try:
        item = into.addAngleValueCommandInput(input_id, name, value)
        item.setManipulator(adsk.core.Point3D.create(0, 0, 0),
                            adsk.core.Vector3D.create(1, 0, 0),
                            adsk.core.Vector3D.create(0, 1, 0))
        item.hasMinimumValue = False
        item.hasMaximumValue = False
    except Exception:
        _log('angle input with a handle not available, using a plain box:\n'
             + traceback.format_exc())
        item = into.addValueInput(input_id, name, 'deg', value)
    return _keep(item, tooltip)


def _build_pattern_tab(tab, units, saved):
    face = tab.addSelectionInput('face', 'Face', 'Select a flat face')
    face.addSelectionFilter('PlanarFaces')
    face.setSelectionLimits(1, 1)
    _keep(face)

    _add_bool(tab, 'simple', 'Simple mode', saved,
              'On: give only the border margin and how much to pocket; '
              'the number of cells, the wall thickness and the corner '
              'radius are worked out for you. Off: set everything yourself.')
    _add_length(tab, 'margin', 'Border margin', units, saved,
                'Solid band kept along the face edge and around holes.')
    _add_int(tab, 'pocketing', 'Pocketing %', 5, 95, 5, saved,
             'How much of the face becomes pocket. 80 means the '
             'material left on the face is 20% of its area.')
    _add_int(tab, 'cellCount', 'Cells', 1, config.MAX_CELLS, 1, saved,
             'How many Voronoi cells to place on the face.')
    _add_int(tab, 'relax', 'Smoothing', 0, 20, 1, saved,
             '0 gives raw random cells. Higher values make the '
             'cells more even in size.')
    _add_length(tab, 'wall', 'Wall thickness', units, saved,
                'Width of the rib left between two pockets.')
    _add_length(tab, 'radius', 'Corner radius', units, saved,
                'Radius of every pocket corner. For milling, use at '
                'least the tool radius. 0 gives sharp corners.')
    _add_length(tab, 'minWidth', 'Min pocket width', units, saved,
                'Pockets narrower than this are left out.')
    _add_int(tab, 'seed', 'Seed', 0, 999999, 1, saved,
             'Random seed. Change it for a different pattern; '
             'the same seed always gives the same pattern.')
    _add_bool(tab, 'cutPockets', 'Cut pockets', saved,
              'Turn off to get only the sketch.')
    _add_depth(tab, saved)

    preview = tab.addDropDownCommandInput(
        'previewMode', 'Preview', adsk.core.DropDownStyles.TextListDropDownStyle)
    chosen = saved['previewMode'] if 0 <= saved['previewMode'] < len(_PREVIEW_NAMES) else 0
    for index, name in enumerate(_PREVIEW_NAMES):
        preview.listItems.add(name, index == chosen, '')
    _keep(preview, 'Outlines: the pocket shapes are drawn on the face at once; '
                   'the sketch and the cut are made when you press OK. Real '
                   'cut: every change rebuilds the sketch and the cut, which '
                   'takes several seconds.')
    _add_text(tab, 'status', 'Select a flat face.', 5)


def _build_stress_tab(tab, units, saved):
    _add_bool(tab, 'stressOn', 'Shape by stress', saved,
              'On: the add-in works out how the plate is loaded and makes '
              'the pockets small with thick walls where it works hard, and '
              'big with thin walls where it is idle.')
    _add_text(tab, 'stressHelp',
              'Pick the edges of the face that are held. Then put forces on '
              'it: on edges, at points, or both. In the preview: green = '
              'held, red = loaded, red arrows = the forces.', 3)
    _add_edges(tab, 'held', 'Held edges', 'Select edges of the face that are held',
               'Edges of the selected face that cannot move: where the part '
               'is bolted, clamped or welded. A hole rim counts as an edge.')
    _add_edges(tab, 'loaded', 'Loaded edges', 'Select edges of the face the force acts on',
               'Edges of the selected face that the edge force pushes or pulls '
               'on. With several edges the force is shared by their length. '
               'Leave empty to use only forces at points.')
    _add_number(tab, 'force', 'Edge force (N)', saved,
                'The total force on the loaded edges, in newtons. 1 kg of '
                'weight is about 10 N.')
    _add_angle(tab, 'angle', 'Edge force direction', saved['angle'],
               'Which way the edge force pushes, turned within the face. The '
               'red arrow in the preview shows the result.')

    points = tab.addSelectionInput('loadPoints', 'Load points',
                                   'Select points on the face to put a force on')
    for kind in ('Vertices', 'SketchPoints', 'ConstructionPoints'):
        points.addSelectionFilter(kind)
    points.setSelectionLimits(0, config.MAX_LOAD_POINTS)
    _keep(points, 'Points on the selected face that carry a force of their '
                  'own: corners of the face, sketch points or construction '
                  'points. Each one gets its own force and direction below. '
                  'For a point in the middle of the face, draw a sketch point '
                  'there first.')
    for number in range(1, config.MAX_LOAD_POINTS + 1):
        force_id, angle_id = _point_ids(number)
        item = tab.addValueInput(force_id, 'Point %d force (N)' % number, '',
                                 adsk.core.ValueInput.createByReal(saved['pointForce']))
        _keep(item, 'The force at load point %d, in newtons. 0 switches it off.' % number)
        _add_angle(tab, angle_id, 'Point %d direction' % number, '0 deg',
                   'Which way the force at load point %d pushes. Turn the '
                   'handle at the point, or type an angle.' % number)

    _add_length(tab, 'thickness', 'Plate thickness', units, saved,
                'How thick the body is under the face. Filled in from the '
                'body when a face is picked; correct it if it is wrong.')
    _add_text(tab, 'material', 'Material: pick a face first.', 2)
    _add_number(tab, 'yieldMPa', 'Yield strength (MPa)', saved,
                'The stress at which the material starts to bend for good. '
                'Filled in from the body\'s material when it can be read; '
                'check it against the material\'s data.')
    _add_number(tab, 'safety', 'Target safety factor', saved,
                'The safety factor the pattern is sized for: the material is '
                'allowed to carry its yield strength divided by this. 2 means '
                'it is loaded to half of what it can take. The status line '
                'shows the lowest safety factor the solid plate has.')
    _add_length(tab, 'wallMin', 'Thinnest wall', units, saved,
                'The wall thickness used where the plate is hardly loaded.')
    _add_number(tab, 'sizeRatio', 'Size ratio', saved,
                'How many times wider the pockets in idle areas are than in '
                'the most loaded areas. 1 makes all pockets about one size.')
    _add_int(tab, 'iterations', 'Iterations', 0, config.MAX_ITERATIONS, 1, saved,
             'How many times the result is checked and corrected. Each round '
             'works out the stress again with the pockets cut (the pocket '
             'depth counts: a floor left under the pockets carries load), '
             'thickens the walls of pockets that are over the allowed stress '
             'and thins those far under it. It stops early when nothing is '
             'over the limit. Each round takes a few seconds. 0 switches the '
             'check off: the pattern then comes from the solid plate alone.')
    _add_bool(tab, 'checkPreview', 'Check in preview', saved,
              'Off: the preview shows the pattern before the check, which is '
              'quick, and the check runs when you press OK. On: the preview '
              'runs the check too, so you see the final pattern, but every '
              'change takes several seconds.')
    colours = tab.addDropDownCommandInput(
        'colours', 'Colours', adsk.core.DropDownStyles.TextListDropDownStyle)
    chosen = saved['colours'] if 0 <= saved['colours'] < len(_COLOUR_NAMES) else 0
    for index, name in enumerate(_COLOUR_NAMES):
        colours.listItems.add(name, index == chosen, '')
    _keep(colours, 'What the face is coloured by in the Outlines preview. '
                   'Stress: blue is idle, red is at the allowed stress. '
                   'Safety factor (yield strength divided by stress): red is '
                   '1 or below, yellow about 2, green about 4, blue 8 and '
                   'above. Both show the plate with its pockets once that '
                   'has been checked (see Iterations), and the solid plate '
                   'before.')
    _add_text(tab, 'stressStatus', '', 6)


def _selected_face():
    selection = _inp('face')
    if selection.selectionCount != 1:
        return None
    return adsk.fusion.BRepFace.cast(selection.selection(0).entity)


def _selected_edges(input_id):
    selection = _inp(input_id)
    return [selection.selection(k).entity for k in range(selection.selectionCount)]


def _on_face_picked():
    """Things that follow the face: the depth arrow, the force handle, the
    plate thickness and the material."""
    face = _selected_face()
    if not face:
        return
    try:
        arrow = fusion_bridge.inward_arrow(face)
        if arrow:
            _inp('depth').setManipulator(arrow[0], arrow[1])
            origin, u, v, _ = fusion_bridge.face_frame(face)
            _inp('angle').setManipulator(arrow[0], adsk.core.Vector3D.create(*u),
                                         adsk.core.Vector3D.create(*v))
    except Exception:
        _log('could not place the handles:\n' + traceback.format_exc())

    try:
        native = fusion_bridge.native_face(face)
        thickness = fusion_bridge.plate_thickness(native)
        if thickness:
            _inp('thickness').value = thickness
        notes = []
        design = adsk.fusion.Design.cast(_app.activeProduct)
        name, yield_mpa, poisson = fusion_bridge.material_data(
            native.body, design.unitsManager, notes)
        _material['poisson'] = poisson if poisson else 0.3
        if yield_mpa:
            _inp('yieldMPa').value = yield_mpa
            text = 'Material: %s\nYield strength read from it: %.0f MPa' % (name, yield_mpa)
        else:
            text = ('Material: %s\nIts yield strength could not be read. '
                    'Enter it below.' % (name or 'unknown'))
        _inp('material').text = text
        _log('face picked: thickness %s cm, material %r, yield %s MPa, poisson %s [%s]'
             % (thickness, name, yield_mpa, poisson, '; '.join(notes)))
    except Exception:
        _log('could not read thickness or material:\n' + traceback.format_exc())


def _place_point_handles():
    """Put each load point's direction handle on its point."""
    face = _selected_face()
    if not face:
        return
    try:
        origin, u, v, _ = fusion_bridge.face_frame(face)
        for number, entity in enumerate(_selected_edges('loadPoints'), 1):
            where = None
            for name in ('worldGeometry', 'geometry'):
                where = getattr(entity, name, None)
                if where is not None and hasattr(where, 'z'):
                    break
            if where is not None:
                _inp(_point_ids(number)[1]).setManipulator(
                    where, adsk.core.Vector3D.create(*u), adsk.core.Vector3D.create(*v))
    except Exception:
        _log('could not place the load point handles:\n' + traceback.format_exc())


def _point_forces():
    """(force, angle) of every picked load point, in picking order."""
    count = min(_inp('loadPoints').selectionCount, config.MAX_LOAD_POINTS)
    forces = []
    for number in range(1, count + 1):
        force_id, angle_id = _point_ids(number)
        forces.append((_inp(force_id).value, _inp(angle_id).value))
    return forces


def _show_inputs_for_mode():
    """Show only the inputs that matter right now."""
    stress_on = _inp('stressOn').value
    simple = _inp('simple').value and not stress_on
    _inp('simple').isVisible = not stress_on
    _inp('pocketing').isVisible = simple
    for input_id in ('cellCount', 'radius', 'minWidth'):
        _inp(input_id).isVisible = not simple
    for input_id in ('relax', 'wall'):  # stress sets the walls itself
        _inp(input_id).isVisible = not simple and not stress_on
    for input_id in _STRESS_ONLY:
        _inp(input_id).isVisible = stress_on
    # One force and one direction for every load point that is picked.
    picked = _inp('loadPoints').selectionCount if stress_on else 0
    for number in range(1, config.MAX_LOAD_POINTS + 1):
        for input_id in _point_ids(number):
            _inp(input_id).isVisible = number <= picked
    # The arrow needs a face to sit on, so depth appears once one is picked.
    _inp('depth').isVisible = _inp('cutPockets').value and _selected_face() is not None


class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            if not design:
                _ui.messageBox('Voronoi Pocket works in the Design workspace.')
                return

            command = args.command
            command.isExecutedWhenPreEmpted = False
            units = design.unitsManager.defaultLengthUnits
            saved = settings.load(config.DEFAULTS)

            _inputs.clear()
            _material['poisson'] = 0.3
            tabs = command.commandInputs
            _build_pattern_tab(tabs.addTabCommandInput('tabPattern', 'Pattern').children,
                               units, saved)
            _build_stress_tab(tabs.addTabCommandInput('tabStress', 'Stress').children,
                              units, saved)
            _last_status[0] = None
            _show_inputs_for_mode()

            # Start clean: nothing remembered from an earlier face or design.
            fusion_bridge.clear_cache()
            fusion_bridge.clear_preview()

            del _command_handlers[:]
            for event, handler in ((command.execute, _Execute()),
                                   (command.executePreview, _Preview()),
                                   (command.inputChanged, _InputChanged()),
                                   (command.validateInputs, _Validate()),
                                   (command.destroy, _Destroy())):
                event.add(handler)
                _command_handlers.append(handler)
        except Exception:
            _log('could not open the dialog:\n' + traceback.format_exc())
            _ui.messageBox('Voronoi Pocket could not open:\n\n' + traceback.format_exc())


# --------------------------------------------------------------------------
# Running the command
# --------------------------------------------------------------------------

def _params():
    """Dialog values as plain numbers (lengths in cm, angles in radians)."""
    depth = _inp('depth')
    return {
        'stress': _inp('stressOn').value,
        'simple': _inp('simple').value,
        'margin': _inp('margin').value,
        'share': _inp('pocketing').value / 100.0,
        'count': _inp('cellCount').value,
        'relax': _inp('relax').value,
        'wall': _inp('wall').value,
        'radius': _inp('radius').value,
        'min_width': _inp('minWidth').value,
        'seed': _inp('seed').value,
        'depth': depth.value,
        'depth_expression': depth.expression,
        # Stress-driven pattern only:
        'force': _inp('force').value,
        'force_angle': _inp('angle').value,
        'thickness': _inp('thickness').value,
        'yield_mpa': _inp('yieldMPa').value,
        'safety': _inp('safety').value,
        'wall_min': _inp('wallMin').value,
        'size_ratio': _inp('sizeRatio').value,
        'iterations': _inp('iterations').value,
        'check_in_preview': _inp('checkPreview').value,
        'cut': _inp('cutPockets').value,
        'poisson': _material.get('poisson', 0.3),
        'point_forces': _point_forces(),
    }


def _colours():
    """What to colour the face by: 'stress', 'safety' or None."""
    try:
        return _COLOUR_KEYS[_inp('colours').selectedItem.index]
    except Exception:
        return 'stress'


def _preview_mode():
    try:
        return _inp('previewMode').selectedItem.index
    except Exception:
        return _PREVIEW_OUTLINES


def _remember():
    values = {'previewMode': _preview_mode(),
              'colours': _inp('colours').selectedItem.index,
              'pointForce': float(_inp(_point_ids(1)[0]).value)}
    for input_id in _INT_INPUTS + _BOOL_INPUTS:
        values[input_id] = _inp(input_id).value
    for input_id in _NUMBER_INPUTS:
        values[input_id] = float(_inp(input_id).value)
    for input_id in _LENGTH_INPUTS + ('angle',):
        values[input_id] = _inp(input_id).expression
    settings.save(values)


def _run(params, final, outlines):
    design = adsk.fusion.Design.cast(_app.activeProduct)
    face = _selected_face()
    if not face:
        raise fusion_bridge.PatternError('Select a flat face first.')
    setup = None
    if params['stress']:
        setup = {'held': _selected_edges('held'), 'loaded': _selected_edges('loaded'),
                 'points': [(entity, force, angle) for entity, (force, angle)
                            in zip(_selected_edges('loadPoints'), params['point_forces'])]}
    if outlines:
        try:
            result = fusion_bridge.preview_outlines(face, params, setup, _colours())
            _refresh_view()
            return result
        except fusion_bridge.PatternError:
            _refresh_view()
            raise
        except Exception:
            # Outline graphics did not work here: show the real thing instead,
            # now and for the rest of this session.
            _outlines_broken[0] = True
            _log('outline preview FAILED, using the real cut:\n' + traceback.format_exc())
            fusion_bridge.clear_preview()
    return fusion_bridge.build(design, face, params,
                               make_pocket=_inp('cutPockets').value, final=final, setup=setup)


def _refresh_view():
    try:
        _app.activeViewport.refresh()
    except Exception:
        pass


def _clear_outlines():
    fusion_bridge.clear_preview()
    _refresh_view()


def _fail(args, message):
    """Abort the command and show ``message``."""
    try:
        args.executeFailed = True
        args.executeFailedMessage = message
    except Exception:
        _ui.messageBox(message)


def _log(text):
    """Write a line to voronoi_pocket.log and to Fusion's Text Commands panel."""
    try:
        _app.log('Voronoi Pocket: ' + text)
    except Exception:
        pass
    try:
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(_LOG_PATH, 'a', encoding='utf-8') as handle:
            handle.write('%s  %s\n' % (stamp, text))
    except OSError:
        pass


def _set_status(text):
    """Show ``text`` at the bottom of both tabs."""
    if text == _last_status[0]:
        return  # changing an input can restart the preview, so only on change
    try:
        _inp('status').text = text
        _inp('stressStatus').text = text
        _last_status[0] = text
    except Exception:
        pass


def _length_text(centimetres):
    """A length in the design's own units, e.g. "2.35 mm"."""
    try:
        manager = adsk.fusion.Design.cast(_app.activeProduct).unitsManager
        units = manager.defaultLengthUnits
        return '%.2f %s' % (manager.convert(centimetres, 'cm', units), units)
    except Exception:
        return '%.2f mm' % (centimetres * 10.0)


def _summary(result, params, seconds, cut, outlines=False):
    lines = ['%d pockets covering %.1f%% of the face%s  -  %.1f s'
             % (result.pocket_count, 100.0 * result.share,
                '' if cut or outlines else ' (sketch only)', seconds)]
    chosen = result.settings
    if params.get('stress'):
        allowed = params['yield_mpa'] / params['safety']
        peak = result.stress_peak_mpa or 0.0
        lines.append('Stress: highest %.0f MPa, allowed %.0f MPa. Thinnest wall %s.'
                     % (peak, allowed, _length_text(chosen.get('wall', 0.0))))
        check = result.check
        if peak > 0.0 and check and check['done'] and check['plate_peak_mpa'] > 0.0:
            lines.append('Lowest safety factor: solid plate %.1f, with pockets %.1f (target %.1f).'
                         % (params['yield_mpa'] / peak,
                            params['yield_mpa'] / check['plate_peak_mpa'], params['safety']))
        elif peak > 0.0:
            lines.append('Lowest safety factor of the solid plate: %.1f (target %.1f).'
                         % (params['yield_mpa'] / peak, params['safety']))
        solid = sum(1 for load in chosen.get('cell_loads', ()) if load >= 1.0)
        if solid:
            lines.append('%d cells are at or above the allowed stress and stay solid.' % solid)
        if check and check['done']:
            if check['over_limit']:
                lines.append('Checked with pockets (%d rounds, %.0f s): %d pockets are still '
                             'over the allowed stress (red).'
                             % (check['rounds'], check['seconds'], check['over_limit']))
            else:
                lines.append('Checked with pockets (%d rounds, %.0f s): no pocket is over '
                             'the allowed stress.' % (check['rounds'], check['seconds']))
        elif check:
            lines.append('Not checked with pockets yet. OK does it (up to %d rounds); '
                         'tick "Check in preview" to see it first.' % params['iterations'])
    elif params.get('simple'):
        lines.append('Chosen: %d cells, wall %s, corner radius %s'
                     % (chosen.get('count', 0), _length_text(chosen.get('wall', 0.0)),
                        _length_text(chosen.get('radius', 0.0))))
        if params['share'] - result.share > 0.005:
            lines.append('%.0f%% does not fit with this border margin; '
                         'this is the most that does.' % (100.0 * params['share']))
    elif result.dropped:
        lines.append('%d cells were too small for a pocket.' % result.dropped)
    if outlines:
        lines.append('Outlines only. OK makes the sketch%s.' % (' and the cut' if cut else ''))
    return '\n'.join(lines)


def _warn_if_over_limit(result, params):
    """After OK the dialog is gone, and its status line with it. So if the
    check of the pocketed plate ended with pockets still over the allowed
    stress, say so in a message."""
    check = result.check
    if not (check and check['done'] and check['over_limit']):
        return
    try:
        _ui.messageBox(
            'The pockets were made, but %d of them are still over the allowed stress '
            'after %d rounds of correction.\n\n'
            'Highest stress in their walls: %.0f MPa. Allowed: %.0f MPa.\n\n'
            'To fix it, undo and try more Iterations, a thicker "Thinnest wall", '
            'a shallower pocket, or fewer cells.'
            % (check['over_limit'], check['rounds'], check['wall_peak_mpa'],
               params['yield_mpa'] / params['safety']),
            'Voronoi Pocket')
    except Exception:
        pass


def _short_error(trace):
    """Last line of a traceback, e.g. "AttributeError: ..."."""
    lines = [line for line in trace.strip().splitlines() if line.strip()]
    return lines[-1] if lines else 'unknown error'


def _attempt(args, final, outlines=False):
    """Build the result for the preview or for OK, and report what happened.

    ``outlines``: only draw the pocket outlines as graphics (quick preview).
    """
    label = 'run' if final else ('outlines' if outlines else 'preview')
    cut = _inp('cutPockets').value
    started = time.perf_counter()
    params = {}
    try:
        params = _params()
        if not outlines:
            _log('%s started: %s, cut=%s' % (label, params, cut))
        result = _run(params, final, outlines)
    except fusion_bridge.PatternError as error:
        message = str(error)
        _log('%s stopped: %s (%s)' % (label, message.replace('\n', ' '), params))
        _set_status(message.split('\n')[0])
        if final:
            _fail(args, message)
        return
    except Exception:
        trace = traceback.format_exc()
        _log('%s FAILED (%s):\n%s' % (label, params, trace))
        _set_status('Error: %s\nFull details: voronoi_pocket.log' % _short_error(trace))
        if final:
            _fail(args, 'Voronoi Pocket failed:\n\n' + trace)
        return
    if final:
        _save_face(params)
        _warn_if_over_limit(result, params)
    # The outline preview may have fallen back to the real cut.
    outlines = outlines and result.sketch is None
    summary = _summary(result, params, time.perf_counter() - started, cut, outlines)
    # Dragging the depth arrow redraws the same outlines many times a second;
    # only write to the log when something was worked out again.
    fresh = any(note.startswith(('pattern computed', 'stress computed', 'stress map',
                                 'stress marks')) for note in result.notes)
    if not outlines or fresh:
        _log('%s done: %s [%s] %s' % (label, summary.replace('\n', ' | '),
                                      '; '.join(result.notes), params if outlines else ''))
    _set_status(summary)


class _Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        _clear_outlines()
        try:
            _remember()
        except Exception:
            _log('could not save the settings:\n' + traceback.format_exc())
        try:
            _attempt(args, final=True)
        except Exception:
            trace = traceback.format_exc()
            _log('run FAILED:\n' + trace)
            _fail(args, 'Voronoi Pocket failed:\n\n' + trace)


class _Preview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            mode = _preview_mode()
            if mode == _PREVIEW_OFF:
                _clear_outlines()
                _set_status('Preview is off. Press OK to build.')
            elif mode == _PREVIEW_OUTLINES and not _outlines_broken[0]:
                _attempt(args, final=False, outlines=True)
            else:
                _clear_outlines()
                _attempt(args, final=False)
        except Exception:
            _log('preview FAILED:\n' + traceback.format_exc())


class _Destroy(adsk.core.CommandEventHandler):
    """The dialog closed (OK or Cancel): take the outline graphics away."""

    def notify(self, args):
        try:
            _clear_outlines()
            fusion_bridge.clear_cache()
            # The time between "run done" and this line is Fusion finishing
            # the command on its own, after the add-in has returned.
            _log('dialog closed')
        except Exception:
            _log('clean-up FAILED:\n' + traceback.format_exc())


def _save_face(params):
    """Keep the outline of the face just used, so a problem with one part can
    be looked at later without the part itself."""
    try:
        outline = fusion_bridge.cached_outline()
        if outline:
            with open(_FACE_PATH, 'w', encoding='utf-8') as handle:
                json.dump({'outer': outline[0], 'holes': outline[1], 'params': params}, handle)
    except Exception:
        pass


class _InputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            changed = args.input.id
            if changed == 'face':
                _on_face_picked()
            if changed in ('face', 'loadPoints'):
                _place_point_handles()
            if changed in ('face', 'simple', 'cutPockets', 'stressOn', 'loadPoints'):
                _show_inputs_for_mode()
        except Exception:
            _log('input change FAILED:\n' + traceback.format_exc())


class _Validate(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            stress_on = _inp('stressOn').value
            simple = _inp('simple').value and not stress_on
            cut = _inp('cutPockets').value
            needed = ['margin']
            if not simple:
                needed += ['radius', 'minWidth']
                needed += ['wallMin', 'thickness', 'angle'] if stress_on else ['wall']
            if cut:
                needed.append('depth')
            values = {}
            for input_id in needed:
                item = _inp(input_id)
                if not item.isValidExpression:
                    args.areInputsValid = False
                    return
                values[input_id] = item.value
            valid = values['margin'] >= 0
            if not simple:
                valid = valid and values['radius'] >= 0 and values['minWidth'] >= 0
            if stress_on:
                valid = (valid and values['wallMin'] > 0 and values['thickness'] > 0
                         and all(_inp(input_id).isValidExpression for input_id in _NUMBER_INPUTS)
                         and _inp('force').value > 0 and _inp('yieldMPa').value > 0
                         and _inp('safety').value > 0 and _inp('sizeRatio').value >= 1)
                picked = min(_inp('loadPoints').selectionCount, config.MAX_LOAD_POINTS)
                for number in range(1, picked + 1):
                    force, angle = (_inp(input_id) for input_id in _point_ids(number))
                    valid = (valid and force.isValidExpression and angle.isValidExpression
                             and force.value >= 0)
            elif not simple:
                valid = valid and values['wall'] > 0
            if cut:
                valid = valid and values['depth'] > 0
            args.areInputsValid = valid
        except Exception:
            args.areInputsValid = False
