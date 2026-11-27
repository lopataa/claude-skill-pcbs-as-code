"""Plot a region of the board: plot_zoom.py x0 y0 x1 y1 out.png [title]

Plain python with matplotlib, from a board folder, after dump_geom.py has
written build/geom.json:
    python3 ../kicadgen/plot_zoom.py 120 60 160 90 build/zoom.png
"""
import json
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

g = json.load(open('build/geom.json'))
x0, y0, x1, y1 = (float(v) for v in sys.argv[1:5])
out = sys.argv[5]
title = sys.argv[6] if len(sys.argv) > 6 else ''

fig, ax = plt.subplots(figsize=(11, 11))
ax.set_facecolor('#14281e')
COL = {'F.Cu': '#d05a2a', 'B.Cu': '#2f6fd0'}

for t in g['tracks']:
    ax.plot([t['x1'], t['x2']], [t['y1'], t['y2']],
            color=COL.get(t['layer'], '#888'), linewidth=t['w'] * 6,
            solid_capstyle='round', zorder=2,
            alpha=0.95 if not t['locked'] else 0.75)

for p in g['pads']:
    col = '#777' if p['npth'] else '#e0c341'
    if p['w'] > 3 and p['h'] > 3 and p['npth']:
        col = '#999'
    ax.add_patch(Rectangle((p['x'] - p['w'] / 2, p['y'] - p['h'] / 2),
                           p['w'], p['h'], angle=-p['angle'],
                           rotation_point='center',
                           facecolor=col, edgecolor='none', zorder=3))

for v in g['vias']:
    r = v['pad'] / 2
    ax.add_patch(Circle((v['x'], v['y']), r, facecolor='#9bdc5a',
                        edgecolor='k', linewidth=0.4, zorder=5))
    ax.add_patch(Circle((v['x'], v['y']), v['drill'] / 2, facecolor='#14281e',
                        zorder=6))
    if x0 <= v['x'] <= x1 and y0 <= v['y'] <= y1:
        ax.annotate("%s" % v['net'].lstrip('/'), (v['x'], v['y']),
                    textcoords='offset points', xytext=(5, 5),
                    color='#9bdc5a', fontsize=7, zorder=8)

for e in g['edges']:
    ax.plot([e['x1'], e['x2']], [e['y1'], e['y2']], color='#f0f0b0',
            linewidth=1.6, zorder=7)

for p in g['pads']:
    if x0 <= p['x'] <= x1 and y0 <= p['y'] <= y1 and p['name']:
        ax.annotate("%s.%s" % (p['ref'], p['name']), (p['x'], p['y']),
                    textcoords='offset points', xytext=(3, -9),
                    color='#cfcfcf', fontsize=6, zorder=8)

ax.set_xlim(x0, x1)
ax.set_ylim(y1, y0)          # KiCad y grows downward
ax.set_aspect('equal')
ax.grid(color='#2a4438', linewidth=0.4)
ax.set_title("%s   x %g..%g  y %g..%g" % (title, x0, x1, y0, y1), fontsize=10)
fig.tight_layout()
fig.savefig(out, dpi=110)
print("wrote", out)
