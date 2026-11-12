"""Turns the SVG figures of docs/figures into PDF files, for LaTeX.

    py docs\\svg_to_pdf.py

LaTeX cannot place SVG files without extra programs. This converter handles
exactly the kinds of shapes make_figures.py draws (paths of straight lines,
lines, circles, polylines, text) and nothing more. Standard library only.
"""

import math
import os
import re
import sys
import xml.etree.ElementTree as ET
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FIGURES = os.path.join(HERE, 'figures')
NS = '{http://www.w3.org/2000/svg}'

# Rough widths of Helvetica letters, in parts of the font size.
NARROW, WIDE = set("iljtf.,:;'|!I()[] "), set('mwMW%')


def text_width(text, size):
    total = 0.0
    for ch in text:
        if ch in NARROW:
            total += 0.28
        elif ch in WIDE:
            total += 0.85
        elif ch.isupper():
            total += 0.67
        else:
            total += 0.54
    return total * size


def rgb(colour):
    return tuple(int(colour[k:k + 2], 16) / 255.0 for k in (1, 3, 5))


def num(value, default=0.0):
    return float(value) if value not in (None, '') else default


def convert(svg_path, pdf_path):
    root = ET.parse(svg_path).getroot()
    _, _, width, height = (float(v) for v in root.get('viewBox').split())
    out = ['1 0 0 -1 0 %.2f cm' % height]  # y runs downward in SVG
    alphas = {}

    def paint(el, shape):
        fill, stroke = el.get('fill', 'none'), el.get('stroke', 'none')
        ops = ['q']
        opacity = el.get('fill-opacity')
        if opacity:
            name = alphas.setdefault(opacity, 'GS%d' % len(alphas))
            ops.append('/%s gs' % name)
        if fill != 'none':
            ops.append('%.3f %.3f %.3f rg' % rgb(fill))
        if stroke != 'none':
            ops.append('%.3f %.3f %.3f RG %s w 1 j' % (rgb(stroke) + (el.get('stroke-width', '1'),)))
            dash = el.get('stroke-dasharray')
            if dash:
                ops.append('[%s] 0 d' % dash)
        ops.append(shape)
        if fill != 'none' and stroke != 'none':
            ops.append('B*')
        elif fill != 'none':
            ops.append('f*')
        elif stroke != 'none':
            ops.append('S')
        else:
            ops.append('n')
        ops.append('Q')
        out.append(' '.join(ops))

    for el in root:
        tag = el.tag.replace(NS, '')
        if tag == 'rect':
            paint(el, '0 0 %.2f %.2f re' % (width, height))
        elif tag == 'path':
            shape = []
            for sub in el.get('d').split('M')[1:]:
                points = re.findall(r'(-?[\d.]+) (-?[\d.]+)', sub)
                shape.append('%s %s m' % points[0])
                shape.extend('%s %s l' % p for p in points[1:])
                shape.append('h')
            paint(el, ' '.join(shape))
        elif tag == 'line':
            paint(el, '%s %s m %s %s l' % (el.get('x1'), el.get('y1'), el.get('x2'), el.get('y2')))
        elif tag == 'polyline':
            points = [p.split(',') for p in el.get('points').split()]
            paint(el, ' '.join('%s %s %s' % (x, y, 'm' if k == 0 else 'l') for k, (x, y) in enumerate(points)))
        elif tag == 'circle':
            cx, cy, r = num(el.get('cx')), num(el.get('cy')), num(el.get('r'))
            k = 0.5523 * r
            paint(el, '%.2f %.2f m ' % (cx + r, cy) + ' '.join(
                '%.2f %.2f %.2f %.2f %.2f %.2f c' % c for c in (
                    (cx + r, cy + k, cx + k, cy + r, cx, cy + r),
                    (cx - k, cy + r, cx - r, cy + k, cx - r, cy),
                    (cx - r, cy - k, cx - k, cy - r, cx, cy - r),
                    (cx + k, cy - r, cx + r, cy - k, cx + r, cy))) + ' h')
        elif tag == 'text':
            label = (el.text or '')
            size = num(el.get('font-size'), 12)
            shift = {'middle': 0.5, 'end': 1.0}.get(el.get('text-anchor', 'start'), 0.0) * text_width(label, size)
            safe = label.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            transform = el.get('transform')
            if transform:  # translate(x y) rotate(-90): reads upward
                tx, ty = (float(v) for v in re.match(r'translate\(([-\d.]+) ([-\d.]+)\)', transform).groups())
                matrix = '0 -1 -1 0 %.2f %.2f' % (tx, ty + shift)
            else:
                matrix = '1 0 0 -1 %.2f %.2f' % (num(el.get('x')) - shift, num(el.get('y')))
            out.append('q %.3f %.3f %.3f rg BT /F1 %s Tf %s Tm (%s) Tj ET Q'
                       % (rgb(el.get('fill', '#000000')) + (el.get('font-size', '12'), matrix, safe)))

    stream = zlib.compress('\n'.join(out).encode('latin-1', 'replace'), 9)
    states = ' '.join('/%s << /ca %s >>' % (name, value) for value, name in alphas.items())
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R /Resources '
         '<< /Font << /F1 5 0 R >> /ExtGState << %s >> >> >>' % (width, height, states)).encode('latin-1'),
        b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
    ]
    pdf = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    start = len(pdf)
    pdf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    pdf += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    pdf += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, start)
    with open(pdf_path, 'wb') as handle:
        handle.write(pdf)


def main():
    names = sorted(name for name in os.listdir(FIGURES) if name.endswith('.svg'))
    for name in names:
        convert(os.path.join(FIGURES, name), os.path.join(FIGURES, name[:-4] + '.pdf'))
    print('%d figures converted to PDF in %s' % (len(names), FIGURES))


if __name__ == '__main__':
    main()
