"""Turn a logo or wordmark SVG into a silkscreen footprint.

KiCad's CLI cannot import SVG into a footprint and bitmap2component only
takes rasters, which would give ragged edges on a 30 mm wordmark. So: parse
the Illustrator path data, flatten the beziers, XOR the subpaths together so
the counters inside letters come out as holes, then fracture the result into
plain outlines -- fp_poly has no notion of a hole.

    python3 ../kicadgen/gen_logo.py source.svg [width_mm] [--out ...]

Run from a board folder, it writes into that board's own .pretty library.
"""
import math
import os
import re
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kicadgen import project  # noqa: E402

BEZIER_STEPS = 14

NUM = re.compile(r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?')
CMD = re.compile(r'([MmLlHhVvCcSsQqTtAaZz])')


def tokenize(d):
    """Path data -> [(command, [numbers]), ...]"""
    out = []
    parts = CMD.split(d)
    i = 1
    while i < len(parts):
        cmd = parts[i]
        args = [float(n) for n in NUM.findall(parts[i + 1])] if i + 1 < len(parts) else []
        out.append((cmd, args))
        i += 2
    return out


def bezier3(p0, p1, p2, p3, steps=BEZIER_STEPS):
    pts = []
    for i in range(1, steps + 1):
        t = i / float(steps)
        u = 1 - t
        pts.append((u * u * u * p0[0] + 3 * u * u * t * p1[0]
                    + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1]
                    + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return pts


def bezier2(p0, p1, p2, steps=BEZIER_STEPS):
    pts = []
    for i in range(1, steps + 1):
        t = i / float(steps)
        u = 1 - t
        pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return pts


def subpaths(d):
    """Flatten one path's `d` attribute into closed point lists."""
    paths, cur = [], []
    x = y = 0.0
    start = (0.0, 0.0)
    prev_c2 = None          # reflection point for s/S
    prev_q1 = None          # reflection point for t/T

    def flush():
        if len(cur) > 2:
            paths.append(list(cur))
        del cur[:]

    for cmd, a in tokenize(d):
        rel = cmd.islower()
        c = cmd.upper()
        if c == 'M':
            flush()
            for k in range(0, len(a) - 1, 2):
                nx, ny = (x + a[k], y + a[k + 1]) if rel else (a[k], a[k + 1])
                x, y = nx, ny
                if k == 0:
                    start = (x, y)
                cur.append((x, y))
            prev_c2 = prev_q1 = None
        elif c == 'L':
            for k in range(0, len(a) - 1, 2):
                x, y = (x + a[k], y + a[k + 1]) if rel else (a[k], a[k + 1])
                cur.append((x, y))
            prev_c2 = prev_q1 = None
        elif c == 'H':
            for v in a:
                x = x + v if rel else v
                cur.append((x, y))
            prev_c2 = prev_q1 = None
        elif c == 'V':
            for v in a:
                y = y + v if rel else v
                cur.append((x, y))
            prev_c2 = prev_q1 = None
        elif c == 'C':
            for k in range(0, len(a) - 5, 6):
                if rel:
                    p1 = (x + a[k], y + a[k + 1])
                    p2 = (x + a[k + 2], y + a[k + 3])
                    p3 = (x + a[k + 4], y + a[k + 5])
                else:
                    p1, p2, p3 = (a[k], a[k + 1]), (a[k + 2], a[k + 3]), (a[k + 4], a[k + 5])
                cur.extend(bezier3((x, y), p1, p2, p3))
                prev_c2 = p2
                x, y = p3
            prev_q1 = None
        elif c == 'S':
            for k in range(0, len(a) - 3, 4):
                if rel:
                    p2 = (x + a[k], y + a[k + 1])
                    p3 = (x + a[k + 2], y + a[k + 3])
                else:
                    p2, p3 = (a[k], a[k + 1]), (a[k + 2], a[k + 3])
                p1 = (2 * x - prev_c2[0], 2 * y - prev_c2[1]) if prev_c2 else (x, y)
                cur.extend(bezier3((x, y), p1, p2, p3))
                prev_c2 = p2
                x, y = p3
            prev_q1 = None
        elif c == 'Q':
            for k in range(0, len(a) - 3, 4):
                if rel:
                    p1 = (x + a[k], y + a[k + 1])
                    p2 = (x + a[k + 2], y + a[k + 3])
                else:
                    p1, p2 = (a[k], a[k + 1]), (a[k + 2], a[k + 3])
                cur.extend(bezier2((x, y), p1, p2))
                prev_q1 = p1
                x, y = p2
            prev_c2 = None
        elif c == 'T':
            for k in range(0, len(a) - 1, 2):
                p2 = (x + a[k], y + a[k + 1]) if rel else (a[k], a[k + 1])
                p1 = (2 * x - prev_q1[0], 2 * y - prev_q1[1]) if prev_q1 else (x, y)
                cur.extend(bezier2((x, y), p1, p2))
                prev_q1 = p1
                x, y = p2
            prev_c2 = None
        elif c == 'A':
            sys.exit("elliptical arc in path data; not supported")
        elif c == 'Z':
            if cur:
                flush()
            x, y = start
            prev_c2 = prev_q1 = None
    flush()
    return paths


CUT = 0.2   # mm, width of the cut that opens each closed loop in copper mode


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('svg')
    # default width 20 mm
    ap.add_argument('width_mm', nargs='?', type=float, default=20.0)
    ap.add_argument('--copper', action='store_true',
                    help='exposed copper (F.Cu + mask opening), every closed loop cut open')
    ap.add_argument('--tented', action='store_true',
                    help='with --copper: copper under the solder mask (no mask opening), '
                         'so it shows as a raised shape instead of bare metal')
    ap.add_argument('--out', help='default: <project>.pretty/<name>.kicad_mod in the current folder')
    ap.add_argument('--name', default='Logo')
    a = ap.parse_args()
    src, width_mm = a.svg, a.width_mm
    out_path = a.out or os.path.join(project.pretty(), a.name + '.kicad_mod')
    svg = open(src, encoding='utf-8').read()

    vb = re.search(r'viewBox="([^"]+)"', svg)
    vx, vy, vw, vh = [float(v) for v in vb.group(1).split()]

    loops = []
    for d in re.findall(r'<path[^>]*\sd="([^"]+)"', svg):
        loops.extend(subpaths(d))
    if not loops:
        sys.exit("no path data found in " + src)

    scale = width_mm / vw
    height_mm = vh * scale

    # XOR every subpath so letter counters punch through, then fracture,
    # because a footprint polygon cannot carry holes.
    poly = pcbnew.SHAPE_POLY_SET()
    for loop in loops:
        one = pcbnew.SHAPE_POLY_SET()
        one.NewOutline()
        for px, py in loop:
            one.Append(int(round((px - vx) * scale * 1e6)),
                       int(round((py - vy) * scale * 1e6)))
        poly.BooleanXor(one)
    if a.copper:
        # A closed copper loop is a shorted turn for the reader coil. Cut
        # each counter open with a thin slot from its top-most point straight
        # up and out through the stroke above it.
        cuts = pcbnew.SHAPE_POLY_SET()
        half = int(CUT / 2 * 1e6)
        for i in range(poly.OutlineCount()):
            top = poly.Outline(i).BBox().GetTop()
            for h in range(poly.HoleCount(i)):
                hole = poly.Hole(i, h)
                p = min((hole.CPoint(j) for j in range(hole.PointCount())), key=lambda q: q.y)
                cuts.NewOutline()
                for x, y in ((p.x - half, p.y + half), (p.x + half, p.y + half),
                             (p.x + half, top - 1000000), (p.x - half, top - 1000000)):
                    cuts.Append(x, y)
        poly.BooleanSubtract(cuts)
    poly.Fracture()

    layers = (('F.Cu',) if a.tented else ('F.Cu', 'F.Mask')) if a.copper else ('F.SilkS',)
    out = ['(footprint "%s"' % a.name,
           '\t(version 20260206)',
           '\t(generator "gen_logo.py")',
           '\t(layer "%s")' % layers[0],
           ('\t(descr "Logo, ' + ('copper under the mask' if a.tented else 'exposed copper')
            + ' with every closed loop cut. Generated by gen_logo.py --copper from %s -- do not hand-edit.")'
            if a.copper else
            '\t(descr "Logo, silkscreen only. Generated by '
            'gen_logo.py from %s -- do not hand-edit.")') % os.path.basename(src),
           '\t(tags "logo silkscreen graphic")',
           '\t(attr board_only exclude_from_pos_files exclude_from_bom '
           'allow_missing_courtyard)',
           '\t(property "Reference" "LOGO**"\n\t\t(at 0 -2 0)\n\t\t(layer "F.Fab")\n'
           '\t\t(hide yes)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
           '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)',
           '\t(property "Value" "%s"\n\t\t(at 0 %.2f 0)\n\t\t(layer "F.Fab")\n'
           '\t\t(hide yes)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
           '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)' % (a.name, height_mm + 2)]

    # centre the mark on the footprint origin
    cx, cy = width_mm / 2.0, height_mm / 2.0
    count = 0
    for i in range(poly.OutlineCount()):
        o = poly.Outline(i)
        pts = []
        for j in range(o.PointCount()):
            p = o.CPoint(j)
            pts.append('(xy %.4f %.4f)' % (p.x / 1e6 - cx, p.y / 1e6 - cy))
        if len(pts) < 3:
            continue
        count += 1
        for lay in layers:
            out.append('\t(fp_poly\n\t\t(pts\n\t\t\t' + ' '.join(pts) + '\n\t\t)\n'
                       '\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type solid)\n\t\t)\n'
                       '\t\t(fill yes)\n\t\t(layer "%s")\n\t)' % lay)
    out.append(')')
    open(out_path, 'w').write("\n".join(out) + "\n")
    print("%s: %d subpaths -> %d filled outline(s), %.1f x %.1f mm%s"
          % (os.path.basename(out_path), len(loops), count, width_mm, height_mm,
             ', copper' if a.copper else ''))


if __name__ == '__main__':
    main()
