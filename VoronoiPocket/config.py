"""Names, IDs and default values for the Voronoi Pocket add-in."""

ADDIN_NAME = 'VoronoiPocket'

CMD_ID = 'voronoiPocket_cmd'
CMD_NAME = 'Voronoi Pocket'
CMD_DESCRIPTION = ('Draws a Voronoi cell pattern on a flat face and cuts '
                   'the cells into the body as pockets.')

# Where the button goes: Design workspace > Solid tab > Create panel.
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidCreatePanel'

MAX_CELLS = 1000
# How many load points the Stress tab can take, each with its own force.
MAX_ITERATIONS = 10
MAX_LOAD_POINTS = 6

# Default dialog values. Lengths are expressions, so they work in any
# document unit. The last values the user ran with override these.
DEFAULTS = {
    'simple': True,      # simple mode: only border margin and pocketing %
    'pocketing': 50,     # simple mode: percent of the face to pocket
    'cellCount': 60,
    'seed': 1,
    'relax': 2,
    'wall': '2 mm',
    'margin': '3 mm',
    'radius': '1 mm',
    'minWidth': '2 mm',
    'depth': '2 mm',
    'cutPockets': True,
    'previewMode': 0,    # 0 outlines (fast), 1 real cut (slow), 2 off
    # Stress tab.
    'stressOn': False,   # shape the pattern by stress
    'force': 1000.0,     # newtons
    'angle': '0 deg',    # direction of the force within the face
    'thickness': '5 mm', # replaced by the body's own when a face is picked
    'yieldMPa': 250.0,   # replaced by the material's own when it can be read
    'safety': 2.0,
    'wallMin': '1.5 mm',
    'sizeRatio': 2.5,    # idle pockets are this many times wider than loaded ones
    'iterations': 3,     # rounds of checking the pocketed plate and correcting; 0 = off
    'checkPreview': False,  # run that check in the preview too (slow), not only on OK
    'colours': 0,        # outline preview coloured by: 0 stress, 1 safety factor, 2 off
    'pointForce': 500.0, # newtons, the starting value for a load point
}
