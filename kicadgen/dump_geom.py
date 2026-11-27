"""Dump board geometry to JSON so the plain-python side can draw it.

KiCad's bundled interpreter has pcbnew but no matplotlib; the system one has
matplotlib but no pcbnew. So: dump here, plot there.

Run from a board folder with KiCad's python, then plot with plot_zoom.py:
    <kicad-python> ../kicadgen/dump_geom.py [board.kicad_pcb [out.json]]
"""
import json
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kicadgen import project  # noqa: E402

b = pcbnew.LoadBoard(sys.argv[1] if len(sys.argv) > 1 else project.board())
out = {'tracks': [], 'vias': [], 'pads': [], 'edges': []}

for t in b.GetTracks():
    s, e = t.GetStart(), t.GetEnd()
    if t.GetClass() == 'PCB_VIA':
        out['vias'].append({
            'x': s.x / 1e6, 'y': s.y / 1e6, 'net': t.GetNetname(),
            'drill': t.GetDrill() / 1e6,
            'pad': t.GetWidth(pcbnew.F_Cu) / 1e6,
            'locked': t.IsLocked()})
    else:
        out['tracks'].append({
            'x1': s.x / 1e6, 'y1': s.y / 1e6, 'x2': e.x / 1e6, 'y2': e.y / 1e6,
            'w': t.GetWidth() / 1e6, 'net': t.GetNetname(),
            'layer': b.GetLayerName(t.GetLayer()), 'locked': t.IsLocked()})

for fp in b.GetFootprints():
    for p in fp.Pads():
        c = p.GetCenter()
        sz = p.GetSize()
        out['pads'].append({
            'x': c.x / 1e6, 'y': c.y / 1e6,
            'w': sz.x / 1e6, 'h': sz.y / 1e6,
            'shape': int(p.GetShape()), 'angle': p.GetOrientationDegrees(),
            'ref': fp.GetReference(), 'name': p.GetPadName(),
            'net': p.GetNetname(),
            'npth': p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH})

for d in b.GetDrawings():
    if d.GetLayer() == pcbnew.Edge_Cuts:
        s, e = d.GetStart(), d.GetEnd()
        out['edges'].append({'x1': s.x / 1e6, 'y1': s.y / 1e6,
                             'x2': e.x / 1e6, 'y2': e.y / 1e6})

json.dump(out, open(sys.argv[2] if len(sys.argv) > 2 else 'build/geom.json', 'w'))
print("tracks %d vias %d pads %d" % (len(out['tracks']), len(out['vias']), len(out['pads'])))
