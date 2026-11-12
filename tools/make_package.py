"""Packs the add-in into one zip that installs on Windows and macOS.

    python tools/make_package.py

The zip holds a single ``VoronoiPocket`` folder with only what Fusion needs:
no caches, no log, no saved dialog settings, no tests. Standard library only,
so this script runs on either system too.
"""

import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(ROOT, 'VoronoiPocket')
OUT_DIR = os.path.join(ROOT, 'dist')

SKIP_DIRS = {'__pycache__', 'tests', '.vscode'}  # .vscode: Fusion's own, per machine
SKIP_FILES = {'settings.json', 'voronoi_pocket.log', 'voronoi_last_face.json', '.DS_Store'}
SKIP_ENDINGS = ('.pyc', '.log')


def main():
    with open(os.path.join(ADDIN, 'VoronoiPocket.manifest'), encoding='utf-8') as handle:
        version = json.load(handle)['version']
    os.makedirs(OUT_DIR, exist_ok=True)
    target = os.path.join(OUT_DIR, 'VoronoiPocket-%s.zip' % version)

    names = []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDIN):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name in SKIP_FILES or name.endswith(SKIP_ENDINGS):
                    continue
                path = os.path.join(folder, name)
                # Zip paths always use "/", whatever system made the zip.
                inside = 'VoronoiPocket/' + os.path.relpath(path, ADDIN).replace(os.sep, '/')
                archive.write(path, inside)
                names.append(inside)

    for name in names:
        print('  ' + name)
    print('%d files -> %s (%.0f kB)' % (len(names), target, os.path.getsize(target) / 1024.0))


if __name__ == '__main__':
    main()
