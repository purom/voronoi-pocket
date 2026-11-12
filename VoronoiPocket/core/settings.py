"""Remembers the last dialog values between Fusion sessions."""

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'settings.json')


def load(defaults):
    """The defaults, overridden by any saved values of the same type."""
    values = dict(defaults)
    try:
        with open(_PATH, 'r', encoding='utf-8') as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return values
    if isinstance(saved, dict):
        for key, default in defaults.items():
            if key in saved and type(saved[key]) is type(default):
                values[key] = saved[key]
    return values


def save(values):
    try:
        with open(_PATH, 'w', encoding='utf-8') as handle:
            json.dump(values, handle, indent=2)
    except OSError:
        pass  # a read-only install folder is not worth an error message
