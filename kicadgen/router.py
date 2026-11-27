"""Grid A* over both copper layers, priced so vias are expensive.

Every via on this board is a plated hole we would rather not have: the fewer
layer changes, the simpler the board is to inspect and to hand-assemble. So
the search charges a via a fixed cost in millimetres of equivalent copper and
prefers a long detour to a hop.

Obstacles come from the board's own effective shapes, so the clearance being
honoured is the real geometry rather than an estimate.
"""
import heapq
import math

import pcbnew

GRID = 0.5           # mm, routing lattice
WIDTH = 0.3          # mm, signal track
# Clearance the router aims for. It has to sit meaningfully above the 0.2 mm
# DRC minimum, because clear_line() checks a span by sampling it: at 0.08 mm
# steps it can under-report the true distance by ~0.04 mm, and at the old
# 0.15 mm steps by ~0.075 mm, which was enough to let three tracks land
# between 0.11 and 0.19 mm from a pad.
CLEAR = 0.35
# 0.6 mm drill, not 0.4: fabs treat anything under 0.5 mm as a small-hole
# surcharge (and a panel check flagged "6 holes under 0.5 mm will not be
# drilled"), and on the hand-etched prototype every via is a wire link, so the
# hole has to pass a wire. 1.2 mm pad keeps a 0.3 mm annular ring.
VIA_PAD = 1.2
VIA_DRILL = 0.6
EDGE_KEEP = 0.6      # mm from the board outline
EDGE_VIA = 0.4       # mm from the board outline to a via's pad edge
# A via costs this much equivalent track length. Rev A priced it at 400 mm,
# because on the hand-etched prototype every via was a wire link to solder;
# 400 bought 5 vias for ~10% more copper. Rev B is fab-made with plated vias,
# so a via is just a hole: 40 mm still stops gratuitous layer hopping without
# sending tracks round the board to avoid one.
VIA_MM = 40.0
TURN_MM = 0.4        # discourage staircases
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
LAYER_NAME = {pcbnew.F_Cu: 'F.Cu', pcbnew.In1_Cu: 'In1.Cu', pcbnew.In2_Cu: 'In2.Cu', pcbnew.B_Cu: 'B.Cu'}


def vec(x, y):
    return pcbnew.VECTOR2I(int(round(x * 1e6)), int(round(y * 1e6)))


class Space:
    """Copper that a given net has to stay clear of, as an occupancy grid.

    Testing every obstacle shape at every A* node is far too slow in Python,
    so each obstacle is rasterised once into a set of blocked cells, walking
    only the cells inside that shape's own bounding box.
    """

    def __init__(self, board, netname, width=None):
        self.board = board
        self.net = netname
        self.width = WIDTH if width is None else width
        bb = board.GetBoardEdgesBoundingBox()
        self.x0 = bb.GetLeft() / 1e6 + EDGE_KEEP
        self.y0 = bb.GetTop() / 1e6 + EDGE_KEEP
        self.x1 = bb.GetRight() / 1e6 - EDGE_KEEP
        self.y1 = bb.GetBottom() / 1e6 - EDGE_KEEP

        # (shape, extra) pairs: `extra` is the obstacle's OWN clearance beyond
        # the default, in mm. The mounting slots ask for 0.6 mm, and ignoring
        # that puts tracks through a hole_clearance violation.
        shapes = {lay: [] for lay in LAYERS}
        # This net's own SMD pads: a track may run onto them, a via may not.
        # A via in a pad is an open 0.6 mm hole that wicks the joint's solder
        # through to the other face; the router put three on rev B (R6, R7,
        # C1) before this.
        own_smd = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if (pad.GetNetname() == netname
                        and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD):
                    own_smd.append((pad.GetEffectiveShape(pad.GetLayer()), 0.0))
        for lay in LAYERS:
            for t in board.GetTracks():
                if t.GetNetname() == netname:
                    continue
                if t.GetClass() == 'PCB_VIA' or t.IsOnLayer(lay):
                    shapes[lay].append((t.GetEffectiveShape(lay), 0.0))
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    if pad.GetNetname() == netname:
                        continue
                    if (pad.IsOnLayer(lay)
                            or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH,
                                                      pcbnew.PAD_ATTRIB_NPTH)):
                        try:
                            own = pad.GetLocalClearance() / 1e6
                        except Exception:
                            own = 0.0
                        shapes[lay].append((pad.GetEffectiveShape(lay),
                                            max(0.0, own - CLEAR)))
            # Copper drawn inside footprints (a copper logo, the reader's
            # coil) belongs to no net and is not a pad: every net keeps clear.
            for fp in board.GetFootprints():
                for g in fp.GraphicalItems():
                    if g.IsOnLayer(lay) and g.GetClass() in ('PCB_SHAPE', 'FP_SHAPE'):
                        shapes[lay].append((g.GetEffectiveShape(lay), 0.0))
            for z in board.Zones():
                # Only keepouts that forbid tracks are walls. The antenna's
                # no-pour area (rev B) allows tracks, and treating it as a
                # wall would close off the middle 40 x 40 mm of the board.
                if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
                    shapes[lay].append((z.Outline(), 0.0))

        self.track_r = self.width / 2.0 + CLEAR
        self.via_r = VIA_PAD / 2.0 + CLEAR
        self.bad = {lay: self._raster(shapes[lay], self.track_r)
                    for lay in LAYERS}
        via_bad = set()
        for lay in LAYERS:
            via_bad |= self._raster(shapes[lay], self.via_r)
        via_bad |= self._raster(own_smd, self.via_r)
        self.bad_via = via_bad
        self.shapes = shapes

    def _raster(self, shapes, radius):
        """Cells within `radius` of any shape, walked per shape bounding box."""
        bad = set()
        step = GRID
        for sh, extra in shapes:
            d = int(round((radius + extra) * 1e6))
            # Inflate the box by hand. SHAPE::BBox(clearance) ignores its
            # argument for some shape types - a rectangular SMD pad came back
            # at its bare outline - so the scan never reached the clearance
            # ring around it, and vias landed 0.02 mm from 0805 pads. Rev A
            # never saw it: every pad was a round through-hole pad.
            bb = sh.BBox(0)
            gx0 = int(math.floor((bb.GetLeft() - d) / 1e6 / step))
            gx1 = int(math.ceil((bb.GetRight() + d) / 1e6 / step))
            gy0 = int(math.floor((bb.GetTop() - d) / 1e6 / step))
            gy1 = int(math.ceil((bb.GetBottom() + d) / 1e6 / step))
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    if (gx, gy) in bad:
                        continue
                    if sh.Collide(vec(gx * step, gy * step), d):
                        bad.add((gx, gy))
        return bad

    def blocked_cell(self, gx, gy, lay):
        return (gx, gy) in self.bad[lay]

    def blocked_via(self, gx, gy):
        # EDGE_KEEP is sized for a track's centreline. A via's 1.2 mm pad
        # needs more: kept only to EDGE_KEEP, one sat 0.25 mm from the edge.
        x, y = gx * GRID, gy * GRID
        m = VIA_PAD / 2.0 + EDGE_VIA - EDGE_KEEP
        if not (self.x0 + m <= x <= self.x1 - m and self.y0 + m <= y <= self.y1 - m):
            return True
        return (gx, gy) in self.bad_via

    def blocked(self, x, y, lay, radius):
        """Exact test, used by the smoothing passes rather than by the search."""
        v = vec(x, y)
        for sh, extra in self.shapes[lay]:
            if sh.Collide(v, int(round((radius + extra) * 1e6))):
                return True
        return False

    def inside(self, x, y):
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def clear_line(self, a, b, lay, radius):
        dist = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(2, int(dist / 0.08) + 1)
        for i in range(n + 1):
            t = i / float(n)
            if self.blocked(a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t, lay, radius):
                return False
        return True


def node(x, y, lay):
    return (int(round(x / GRID)), int(round(y / GRID)), lay)


def pos(n):
    return (n[0] * GRID, n[1] * GRID)


def astar(space, starts, goals, goal_pts):
    """Cheapest path from any start node to any goal node.

    `goal_pts` are representative (x, y) points of the goal set, used only to
    steer the heuristic.
    """
    via_cost = VIA_MM / GRID
    turn_cost = TURN_MM / GRID
    steps = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
             if (dx, dy) != (0, 0)]

    def h(n):
        x, y = pos(n)
        return min(math.hypot(x - gx, y - gy) for gx, gy in goal_pts) / GRID

    open_set = [(h(s), 0.0, s, (0, 0), None, None) for s in starts]
    heapq.heapify(open_set)
    came, best = {}, {}
    hit = None
    while open_set:
        _, g, n, indir, parent, pdir = heapq.heappop(open_set)
        if (n, indir) in came:
            continue
        came[(n, indir)] = None if parent is None else (parent, pdir)
        if n in goals:
            hit = (n, indir)
            break
        x, y = pos(n)
        for dx, dy in steps:
            nx, ny = x + dx * GRID, y + dy * GRID
            m = (n[0] + dx, n[1] + dy, n[2])
            key = (m, (dx, dy))
            if key in came or not space.inside(nx, ny):
                continue
            cost = g + math.hypot(dx, dy)
            if indir != (0, 0) and (dx, dy) != indir:
                cost += turn_cost
            if best.get(key, 1e18) <= cost:
                continue
            if m not in goals and space.blocked_cell(m[0], m[1], n[2]):
                continue
            best[key] = cost
            heapq.heappush(open_set, (cost + h(m), cost, m, (dx, dy), n, indir))
        # a through via: from this layer to any other copper layer in use
        for other in LAYERS:
            if other == n[2]:
                continue
            m = (n[0], n[1], other)
            key = (m, indir)
            if key in came or best.get(key, 1e18) <= g + via_cost:
                continue
            # A via is checked even when it lands on a goal. Landing on this
            # net's existing copper exempts a 0.3 mm track, but a via is a
            # 1.2 mm pad, and the exemption put one 0.1 mm from a +3V3 track
            # once rev B made vias cheap enough to be common.
            if not space.blocked_via(n[0], n[1]):
                best[key] = g + via_cost
                heapq.heappush(open_set, (g + via_cost + h(m), g + via_cost,
                                          m, indir, n, indir))
    if hit is None:
        return None
    path = []
    key = hit
    while key is not None:
        path.append(key[0])
        key = came[key]
    path.reverse()
    return path


def simplify(run):
    out = [run[0]]
    for i in range(1, len(run) - 1):
        ax, ay = out[-1][0], out[-1][1]
        bx, by = run[i][0], run[i][1]
        cx, cy = run[i + 1][0], run[i + 1][1]
        if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) > 1e-9:
            out.append(run[i])
    out.append(run[-1])
    return out


def smooth(space, run, lay):
    """Greedy line of sight: keep the farthest node reachable in a straight line."""
    r = space.track_r
    out = [run[0]]
    i = 0
    while i < len(run) - 1:
        j = len(run) - 1
        while j > i + 1 and not space.clear_line(run[i], run[j], lay, r):
            j -= 1
        out.append(run[j])
        i = j
    return out


def diagonalise(space, run, lay):
    """Turn an arbitrary-angle hop into a 45/90 dogleg where one fits."""
    r = space.track_r
    out = [run[0]]
    for a, b in zip(run, run[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        adx, ady = abs(dx), abs(dy)
        if adx < 1e-9 or ady < 1e-9 or abs(adx - ady) < 1e-9:
            out.append(b)
            continue
        sx = 1 if dx > 0 else -1
        sy = 1 if dy > 0 else -1
        run45 = min(adx, ady)
        for k in ((a[0] + sx * run45, a[1] + sy * run45),
                  (b[0] - sx * run45, b[1] - sy * run45)):
            if (space.clear_line(a, k, lay, r)
                    and space.clear_line(k, b, lay, r)):
                out.append(k)
                break
        out.append(b)
    return out
