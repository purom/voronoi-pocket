"""Voronoi Pocket - a Fusion 360 add-in.

Adds a "Voronoi Pocket" command to Solid > Create. It draws a Voronoi cell
pattern on a flat face and cuts the cells into the body.
"""

import importlib
import traceback

import adsk.core

from . import config
from .core import geometry, stress, settings, fusion_bridge
from .commands.voronoiPocket import entry

# Fusion keeps modules loaded between Stop and Run. Reloading them here means
# code edits take effect without restarting Fusion. Order matters: a module
# comes after the modules it imports.
_MODULES = (config, geometry, stress, settings, fusion_bridge, entry)


def run(context):
    try:
        for module in _MODULES:
            importlib.reload(module)
        entry.start()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Voronoi Pocket failed to start:\n\n' + traceback.format_exc())


def stop(context):
    try:
        entry.stop()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Voronoi Pocket failed to stop:\n\n' + traceback.format_exc())
