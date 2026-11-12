"""Walks through the dialog (commands/voronoiPocket/entry.py) with a stand-in
for Fusion's dialog inputs: build it, pick a face, preview, switch modes, press
OK. This catches mistakes in our own dialog code. It cannot tell whether the
real Fusion inputs behave like the stand-ins.

Run with pytest, or directly:  python tests/test_dialog.py
"""

import math
import os
import sys
import tempfile
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import test_bridge as tb  # noqa: E402  (also puts the stand-in "adsk" in place)

adsk = sys.modules['adsk']

# The dialog code uses imports like "from ... import config", which only work
# inside a package. Fusion loads the add-in folder as one; do the same here.
_package = types.ModuleType('VoronoiPocket')
_package.__path__ = [ADDIN]
sys.modules['VoronoiPocket'] = _package


# ------------------------------------------------- stand-in dialog inputs

def _number(expression):
    """What Fusion would make of an expression: lengths in cm, angles in radians."""
    parts = str(expression).replace('expression:', '').split()
    scale = {'mm': 0.1, 'cm': 1.0, 'deg': math.pi / 180.0, '': 1.0}[parts[1] if len(parts) > 1 else '']
    return float(parts[0]) * scale


class Input(object):
    def __init__(self, input_id, **extra):
        self.id = input_id
        self.isVisible = True
        self.isValidExpression = True
        self.isFullWidth = False
        self.tooltip = ''
        self.manipulator = None
        self.__dict__.update(extra)

    def setManipulator(self, *where):
        self.manipulator = where


class ValueBox(Input):
    def __init__(self, input_id, start):
        Input.__init__(self, input_id)
        if isinstance(start, str):
            self.expression = start.replace('expression:', '')
            self.value = _number(start)
        else:
            self.expression = repr(start)
            self.value = start


class Selection(Input):
    def __init__(self, input_id):
        Input.__init__(self, input_id, picked=[], filters=[], limits=None)

    def addSelectionFilter(self, name):
        self.filters.append(name)

    def setSelectionLimits(self, low, high):
        self.limits = (low, high)

    @property
    def selectionCount(self):
        return len(self.picked)

    def selection(self, index):
        return types.SimpleNamespace(entity=self.picked[index])


class DropDown(Input):
    def __init__(self, input_id):
        Input.__init__(self, input_id, names=[], chosen=0)
        self.listItems = types.SimpleNamespace(add=self._add)

    def _add(self, name, selected, icon):
        if selected:
            self.chosen = len(self.names)
        self.names.append(name)

    @property
    def selectedItem(self):
        return types.SimpleNamespace(index=self.chosen, name=self.names[self.chosen])


class Inputs(object):
    """Stands in for CommandInputs, at the top of the dialog or inside a tab."""

    def __init__(self, made):
        self.made = made   # every input of the dialog, in the order made

    def _new(self, item):
        self.made.append(item)
        return item

    def addTabCommandInput(self, input_id, name):
        return self._new(Input(input_id, name=name, children=Inputs(self.made)))

    def addSelectionInput(self, input_id, name, prompt):
        return self._new(Selection(input_id))

    def addBoolValueInput(self, input_id, name, is_check, resources, start):
        return self._new(Input(input_id, value=start))

    def addValueInput(self, input_id, name, units, start):
        return self._new(ValueBox(input_id, start))

    def addDistanceValueCommandInput(self, input_id, name, start):
        return self._new(ValueBox(input_id, start))

    def addAngleValueCommandInput(self, input_id, name, start):
        return self._new(ValueBox(input_id, start))

    def addIntegerSpinnerCommandInput(self, input_id, name, low, high, step, start):
        return self._new(Input(input_id, value=start, low=low, high=high))

    def addDropDownCommandInput(self, input_id, name, style):
        return self._new(DropDown(input_id))

    def addTextBoxCommandInput(self, input_id, name, text, rows, read_only):
        return self._new(Input(input_id, text=text, rows=rows))


class Handler(object):
    """Base for the add-in's event handler classes."""

    def __init__(self):
        pass


# What the dialog code needs from "adsk" on top of what test_bridge set up.
design = tb._design()
design.unitsManager = types.SimpleNamespace(
    defaultLengthUnits='mm', convert=lambda value, source, target: value * 10.0)
logged = []
app = types.SimpleNamespace(
    activeProduct=design, userInterface=types.SimpleNamespace(messageBox=logged.append),
    log=lambda text: None, activeViewport=types.SimpleNamespace(refresh=lambda: None))
adsk.core.Application = types.SimpleNamespace(get=lambda: app)
adsk.core.DropDownStyles = types.SimpleNamespace(TextListDropDownStyle='list')
for name in ('CommandCreatedEventHandler', 'CommandEventHandler',
             'InputChangedEventHandler', 'ValidateInputsEventHandler'):
    setattr(adsk.core, name, Handler)
adsk.fusion.Design = types.SimpleNamespace(cast=lambda thing: thing)
adsk.fusion.BRepFace = types.SimpleNamespace(cast=lambda thing: thing)

from VoronoiPocket import config  # noqa: E402
from VoronoiPocket.commands.voronoiPocket import entry  # noqa: E402
from VoronoiPocket.core import fusion_bridge as bridge  # noqa: E402
from VoronoiPocket.core import settings  # noqa: E402

# Keep the user's own settings, log and saved face out of it.
SCRATCH = tempfile.mkdtemp(prefix='voronoi_dialog_test_')
settings._PATH = os.path.join(SCRATCH, 'settings.json')
entry._LOG_PATH = os.path.join(SCRATCH, 'voronoi_pocket.log')
entry._FACE_PATH = os.path.join(SCRATCH, 'voronoi_last_face.json')


def open_dialog():
    """Build the dialog and return (command, {name: handler})."""
    made = []
    handlers = {}

    def event(name):
        return types.SimpleNamespace(add=lambda handler: handlers.__setitem__(name, handler))

    command = types.SimpleNamespace(
        commandInputs=Inputs(made), isExecutedWhenPreEmpted=True, made=made,
        execute=event('execute'), executePreview=event('preview'),
        inputChanged=event('changed'), validateInputs=event('validate'),
        destroy=event('destroy'))
    entry._CommandCreated().notify(types.SimpleNamespace(command=command))
    assert not logged, logged
    return command, handlers


def fire(handlers, name, command, changed=None):
    args = types.SimpleNamespace(command=command, input=changed, areInputsValid=None,
                                 executeFailed=False, executeFailedMessage='')
    handlers[name].notify(args)
    return args


def change(handlers, command, input_id, **values):
    """Set something on an input the way a user would, and let the dialog react."""
    item = entry._inp(input_id)
    for key, value in values.items():
        setattr(item, key, value)
    fire(handlers, 'changed', command, item)
    assert fire(handlers, 'validate', command).areInputsValid, input_id
    fire(handlers, 'preview', command)
    return entry._inp('status').text


def plate():
    face, component, sketches, extrudes = tb._plate()
    point = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)
    face.body.boundingBox = types.SimpleNamespace(minPoint=point(1, 1, 4.4), maxPoint=point(11, 7, 5))
    return face, component, sketches, extrudes


def shown():
    return sorted(key for key, item in entry._inputs.items() if item.isVisible)


# ------------------------------------------------------------------ tests

def test_dialog_has_two_tabs_and_every_default():
    command, handlers = open_dialog()
    tabs = [item for item in command.made if hasattr(item, 'children')]
    assert [(tab.id, tab.name) for tab in tabs] == [('tabPattern', 'Pattern'), ('tabStress', 'Stress')]
    assert sorted(handlers) == ['changed', 'destroy', 'execute', 'preview', 'validate']
    assert command.isExecutedWhenPreEmpted is False

    # Every saved setting has an input, and the other way round. (The one
    # saved point force is the starting value of all the point rows.)
    stored = set(config.DEFAULTS)
    inputs = set(entry._inputs)
    rows = set()
    for number in range(1, config.MAX_LOAD_POINTS + 1):
        rows.update(entry._point_ids(number))
    assert len(rows) == 2 * config.MAX_LOAD_POINTS
    assert stored - inputs == {'pointForce'}, stored - inputs
    assert inputs - stored == {'face', 'held', 'loaded', 'loadPoints', 'status',
                               'stressStatus', 'stressHelp', 'material'} | rows
    assert entry._inp('loadPoints').limits == (0, config.MAX_LOAD_POINTS)
    assert entry._inp('loadPoints').filters == ['Vertices', 'SketchPoints', 'ConstructionPoints']
    assert entry._inp('colours').names == ['Stress', 'Safety factor', 'Off']
    assert entry._inp('pForce1').value == 500.0 and entry._inp('pAngle3').value == 0.0
    assert entry._inp('face').limits == (1, 1) and entry._inp('face').filters == ['PlanarFaces']
    assert entry._inp('held').limits == (0, 0) and entry._inp('held').filters == ['Edges']
    assert entry._inp('previewMode').names == list(entry._PREVIEW_NAMES)
    assert abs(entry._inp('margin').value - 0.3) < 1e-12        # '3 mm' in cm
    assert entry._inp('force').value == 1000.0

    # Simple mode is the default: only what it needs is showing.
    assert shown() == ['cutPockets', 'face', 'margin', 'pocketing', 'previewMode', 'seed',
                       'simple', 'status', 'stressOn', 'stressStatus']
    fire(handlers, 'destroy', command)


def test_pick_a_face_preview_and_ok_in_each_mode():
    command, handlers = open_dialog()
    face, component, sketches, extrudes = plate()
    bridge.STRESS_MESH_PARTS = 22.0

    # Pick the face: depth appears, thickness and material are read.
    entry._inp('face').picked = [face]
    status = change(handlers, command, 'face')
    assert 'pockets covering' in status and 'Chosen:' in status and 'Outlines only' in status
    assert entry._inp('depth').isVisible and entry._inp('depth').manipulator is not None
    assert abs(entry._inp('thickness').value - 0.6) < 1e-9       # from the body's box
    assert 'could not be read' in entry._inp('material').text    # the stand-in has no material
    assert entry._inp('stressStatus').text == status
    assert len(component.customGraphicsGroups.groups) == 1 and sketches == []

    # All settings by hand.
    status = change(handlers, command, 'simple', value=False)
    assert 'Chosen:' not in status and 'pockets covering' in status
    assert 'wall' in shown() and 'pocketing' not in shown()
    wide = change(handlers, command, 'wall', value=0.6, expression='6 mm')
    assert wide != status

    # Stress: first it says what is missing, then it shows the stress.
    status = change(handlers, command, 'stressOn', value=True)
    assert status == 'Stress: pick at least one edge of this face that is held.'
    for input_id in entry._STRESS_ONLY:
        assert entry._inp(input_id).isVisible
    assert 'simple' not in shown() and 'wall' not in shown() and 'cellCount' in shown()
    entry._inp('held').picked = [face.edges[tb.LEFT]]
    status = change(handlers, command, 'held')
    assert status == 'Stress: pick a loaded edge or a load point for the force to act on.'
    entry._inp('loaded').picked = [face.edges[tb.RIGHT]]
    status = change(handlers, command, 'loaded')
    assert 'Stress: highest' in status and 'allowed 125 MPa' in status   # 250 / 2
    group, = component.customGraphicsGroups.groups
    assert len(group.meshes) == 1 and len(group.lines) == 4
    # 1000 N on 6 cm x 0.6 cm is 2.8 MPa: far below the limit, so thin walls
    # and a big safety factor.
    assert 'Thinnest wall 1.50 mm' in status
    assert 'Lowest safety factor of the solid plate:' in status and '(target 2.0)' in status
    lowest = float(status.split('solid plate: ')[1].split(' ')[0])
    assert 30.0 < lowest < 95.0                                          # 250 / about 3 to 8

    # A force at a point: its own force and direction appear, with a handle
    # on the point, and it adds to the edge force.
    assert 'pForce1' not in shown() and 'pAngle1' not in shown()
    middle = tb._vertex(6.0, 4.0, 5.0)
    entry._inp('loadPoints').picked = [middle]
    with_point = change(handlers, command, 'loadPoints')
    assert 'pForce1' in shown() and 'pAngle1' in shown() and 'pForce2' not in shown()
    where = entry._inp('pAngle1').manipulator[0]
    assert (where.x, where.y, where.z) == (6.0, 4.0, 5.0)
    assert with_point != status and 'Stress: highest' in with_point
    arrows = component.customGraphicsGroups.groups[0].lines[2]
    assert len(arrows[1]) == 2 * 6                                       # two arrows now
    stronger = change(handlers, command, 'pForce1', value=5000.0)
    assert stronger != with_point
    turned = change(handlers, command, 'pAngle1', value=math.pi / 2)
    assert turned != stronger
    # Switched off again by a force of 0, or by a negative one being refused.
    assert change(handlers, command, 'pForce1', value=0.0).split('  -  ')[0] == status.split('  -  ')[0]
    entry._inp('pForce1').value = -5.0
    assert fire(handlers, 'validate', command).areInputsValid is False
    entry._inp('loadPoints').picked = []
    assert change(handlers, command, 'loadPoints').split('  -  ')[0] == status.split('  -  ')[0]
    assert 'pForce1' not in shown()

    # Colours: by stress, by safety factor, or none.
    by_stress = list(component.customGraphicsGroups.groups[0].meshes[0][2])
    entry._inp('colours').chosen = 1
    change(handlers, command, 'colours')
    by_safety = list(component.customGraphicsGroups.groups[0].meshes[0][2])
    assert by_safety != by_stress and len(by_safety) == len(by_stress)
    entry._inp('colours').chosen = 2
    change(handlers, command, 'colours')
    assert component.customGraphicsGroups.groups[0].meshes == []
    entry._inp('colours').chosen = 1
    change(handlers, command, 'colours')
    # A weak material: much of the plate has to stay solid.
    weak = change(handlers, command, 'yieldMPa', value=6.0)
    assert 'allowed 3 MPa' in weak and 'stay solid' in weak
    # Something impossible is refused before anything runs.
    entry._inp('force').value = 0.0
    assert fire(handlers, 'validate', command).areInputsValid is False
    entry._inp('force').value = 1000.0

    # OK: outlines go, a sketch and a cut are made, the settings are kept.
    args = fire(handlers, 'execute', command)
    assert args.executeFailed is False, args.executeFailedMessage
    assert len(sketches) == 1 and extrudes.distances == ['expression:VoronoiDepth']
    assert component.customGraphicsGroups.groups == []
    assert 'Outlines only' not in entry._inp('status').text
    saved = settings.load(config.DEFAULTS)
    assert saved['stressOn'] is True and saved['yieldMPa'] == 6.0 and saved['wall'] == '6 mm'
    assert saved['colours'] == 1
    assert os.path.exists(entry._FACE_PATH)
    fire(handlers, 'destroy', command)

    # The next time the dialog opens with those values.
    command, handlers = open_dialog()
    assert entry._inp('stressOn').value is True and entry._inp('yieldMPa').value == 6.0
    assert 'held' in shown() and 'simple' not in shown()
    fire(handlers, 'destroy', command)
    bridge.STRESS_MESH_PARTS = 65.0
    log = open(entry._LOG_PATH, encoding='utf-8').read()
    assert 'FAILED' not in log, log
    assert 'stress computed' in log and 'run done' in log and 'dialog closed' in log


def test_real_cut_preview_and_off():
    os.path.exists(settings._PATH) and os.remove(settings._PATH)
    command, handlers = open_dialog()
    face, component, sketches, extrudes = plate()
    entry._inp('face').picked = [face]
    change(handlers, command, 'face')

    entry._inp('previewMode').chosen = entry._PREVIEW_FULL
    status = change(handlers, command, 'previewMode')
    assert len(sketches) == 1 and extrudes.distances == [entry._inp('depth').value]
    assert component.customGraphicsGroups.groups == [] and 'Outlines only' not in status

    entry._inp('previewMode').chosen = entry._PREVIEW_OFF
    assert change(handlers, command, 'previewMode') == 'Preview is off. Press OK to build.'

    # Sketch only: no depth needed, and none shown.
    change(handlers, command, 'cutPockets', value=False)
    assert 'depth' not in shown()
    entry._inp('depth').isValidExpression = False
    assert fire(handlers, 'validate', command).areInputsValid is True
    fire(handlers, 'destroy', command)


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
