"""Collision-aware silkscreen placement.

DRC's three silk complaints -- silk over a mask opening, silk over silk, and
silk hanging off the board edge -- are all placement problems. Rather than
hand-nudging designators until the report goes quiet (which then breaks the
next time a part moves), find each one a clear spot against the board's real
geometry. Called from gen_pcb.py, so the result is reproducible.
"""
import math

import pcbnew

MARGIN = 0.22       # mm, a little over the 0.15 silk clearance rule
EDGE_MARGIN = 0.35  # mm from the board outline
SAMPLE = 0.3        # mm, how finely a text box is sampled
SILK = (pcbnew.F_SilkS, pcbnew.B_SilkS)


def _bbox_points(bb, step=SAMPLE):
    """Sample a bounding box: its perimeter plus a coarse interior grid."""
    x0, y0 = bb.GetLeft() / 1e6, bb.GetTop() / 1e6
    x1, y1 = bb.GetRight() / 1e6, bb.GetBottom() / 1e6
    nx = max(1, int((x1 - x0) / step))
    ny = max(1, int((y1 - y0) / step))
    pts = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            pts.append((x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny))
    return pts


def _vec(x, y):
    return pcbnew.VECTOR2I(int(round(x * 1e6)), int(round(y * 1e6)))


class SilkSpace:
    """Everything a piece of silkscreen has to stay clear of."""

    def __init__(self, board):
        self.board = board
        self.edges = []
        for d in board.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts:
                self.edges.append(d.GetEffectiveShape())
        # The outline as a box, for texts that miss the edge line altogether.
        # Testing only "does it touch the edge" passed a label pushed wholly
        # off the board: it touched nothing, and the fab clipped it away.
        eb = board.GetBoardEdgesBoundingBox()
        em = int(round(EDGE_MARGIN * 1e6))
        self.inner = (eb.GetLeft() + em, eb.GetTop() + em,
                      eb.GetRight() - em, eb.GetBottom() - em)
        self.bodies = []        # (footprint ref, body bbox without text)
        for fp in board.GetFootprints():
            self.bodies.append((fp.GetReference(),
                                fp.GetBoundingBox(False, False)))
        self.pads = {lay: [] for lay in SILK}
        for fp in board.GetFootprints():
            for p in fp.Pads():
                pth = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH,
                                           pcbnew.PAD_ATTRIB_NPTH)
                for lay, cu in ((pcbnew.F_SilkS, pcbnew.F_Cu),
                                (pcbnew.B_SilkS, pcbnew.B_Cu)):
                    if pth or p.IsOnLayer(cu):
                        self.pads[lay].append((p.GetBoundingBox(),
                                               p.GetEffectiveShape(cu)))

    def silk_items(self, exclude):
        """Silk graphics and text, minus the item currently being placed.

        SWIG hands out a fresh proxy per call, so items are identified by
        UUID rather than by Python identity.
        """
        skip = exclude.m_Uuid.AsString()
        out = {lay: [] for lay in SILK}

        def add(it):
            if it.GetLayer() not in SILK:
                return
            if it.m_Uuid.AsString() == skip:
                return
            if not getattr(it, 'IsVisible', lambda: True)():
                return
            out[it.GetLayer()].append((it.GetBoundingBox(),
                                       it.GetEffectiveShape()))

        for d in self.board.GetDrawings():
            add(d)
        for fp in self.board.GetFootprints():
            for it in list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]:
                add(it)
        return out

    def inside_other_part(self, bb, owner):
        """True if this text sits within some other footprint's body."""
        cx = (bb.GetLeft() + bb.GetRight()) // 2
        cy = (bb.GetTop() + bb.GetBottom()) // 2
        for ref, body in self.bodies:
            if ref == owner:
                continue
            if body.Contains(pcbnew.VECTOR2I(cx, cy)):
                return True
        return False

    def clear(self, bb, layer, others):
        """True if a text box at `bb` on `layer` hits nothing."""
        pts = _bbox_points(bb)
        m = int(round(MARGIN * 1e6))
        em = int(round(EDGE_MARGIN * 1e6))
        grown = pcbnew.BOX2I(bb.GetOrigin(), bb.GetSize())
        grown.Inflate(m)

        x0, y0, x1, y1 = self.inner
        if (bb.GetLeft() < x0 or bb.GetTop() < y0
                or bb.GetRight() > x1 or bb.GetBottom() > y1):
            return False
        for shape in self.edges:
            for x, y in pts:
                if shape.Collide(_vec(x, y), em):
                    return False
        for obb, shape in self.pads[layer]:
            if not grown.Intersects(obb):
                continue
            for x, y in pts:
                if shape.Collide(_vec(x, y), m):
                    return False
        for obb, shape in others[layer]:
            if not grown.Intersects(obb):
                continue
            for x, y in pts:
                if shape.Collide(_vec(x, y), m):
                    return False
        return True


def trim_at_pads(board, verbose=True):
    """Clip silkscreen lines where they run across a pad or a mounting hole.

    Stock connector footprints draw a closed body outline, and a module mount
    draws corner ticks; either can cross a hole that only exists once the part
    is placed on this board. Shortening the line beats deleting the outline
    or nudging a hole that has to sit where the real module's hole sits.
    """
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            for cu in (pcbnew.F_Cu, pcbnew.B_Cu):
                if (pad.IsOnLayer(cu)
                        or pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH,
                                                  pcbnew.PAD_ATTRIB_NPTH)):
                    pads.append((pad.GetBoundingBox(), pad.GetEffectiveShape(cu)))
                    break
    m = int(round(MARGIN * 1e6))
    trimmed = dropped = 0

    for fp in board.GetFootprints():
        for it in list(fp.GraphicalItems()):
            if it.GetLayer() not in SILK:
                continue
            # GraphicalItems() also yields text; only straight lines trim.
            if it.GetClass() != 'PCB_SHAPE':
                continue
            if it.GetShape() != pcbnew.SHAPE_T_SEGMENT:
                continue
            a, b = it.GetStart(), it.GetEnd()
            ax, ay, bx, by = a.x / 1e6, a.y / 1e6, b.x / 1e6, b.y / 1e6
            length = math.hypot(bx - ax, by - ay)
            if length < 1e-6:
                continue
            # Bind the box to a local first: GetBoundingBox() returns a
            # temporary, and GetOrigin()/GetSize() hand back references into
            # it, which go stale the moment it is collected.
            own = it.GetBoundingBox()
            grown = pcbnew.BOX2I(own.GetOrigin(), own.GetSize())
            grown.Inflate(m)
            near = [sh for bbx, sh in pads if grown.Intersects(bbx)]
            if not near:
                continue

            n = max(2, int(length / 0.1))
            free = []
            for i in range(n + 1):
                t = i / float(n)
                pt = _vec(ax + (bx - ax) * t, ay + (by - ay) * t)
                free.append(not any(sh.Collide(pt, m) for sh in near))
            if all(free):
                continue

            runs, start = [], None
            for i, ok in enumerate(free):
                if ok and start is None:
                    start = i
                elif not ok and start is not None:
                    runs.append((start, i - 1))
                    start = None
            if start is not None:
                runs.append((start, len(free) - 1))

            kept = []
            for i0, i1 in runs:
                t0, t1 = i0 / float(n), i1 / float(n)
                if (t1 - t0) * length < 0.6:
                    continue          # too short to read as a line
                kept.append((t0, t1))

            fp.Remove(it)
            if not kept:
                dropped += 1
            else:
                trimmed += 1
            if verbose:
                print("  trim %-5s (%.2f,%.2f)-(%.2f,%.2f) -> %s"
                      % (fp.GetReference(), ax, ay, bx, by,
                         ", ".join("%.0f%%-%.0f%%" % (t0 * 100, t1 * 100)
                                   for t0, t1 in kept) or "removed"))
            for t0, t1 in kept:
                seg = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
                seg.SetStart(_vec(ax + (bx - ax) * t0, ay + (by - ay) * t0))
                seg.SetEnd(_vec(ax + (bx - ax) * t1, ay + (by - ay) * t1))
                seg.SetLayer(it.GetLayer())
                seg.SetWidth(it.GetWidth())
                fp.Add(seg)
    if verbose:
        print("silk trim: %d lines shortened, %d removed outright"
              % (trimmed, dropped))
    return trimmed, dropped


def _candidates(origin, span):
    """Original spot first, then rings of offsets outward from the part."""
    yield (0.0, 0.0)
    for r in (span, span + 1.2, span + 2.4, span + 3.6, span + 5.0):
        for k in range(16):
            a = 2 * math.pi * k / 16.0
            yield (r * math.cos(a), r * math.sin(a))


def tidy(board, verbose=True):
    trim_at_pads(board, verbose)
    space = SilkSpace(board)
    moved = hidden = 0

    # Board-level texts first: they carry the real assembly instructions, so
    # they get first claim on the free space.
    targets = [(d, None) for d in board.GetDrawings()
               if d.GetLayer() in SILK and d.GetClass() == 'PCB_TEXT']
    targets += [(fp.Reference(), fp.GetReference())
                for fp in board.GetFootprints()
                if fp.Reference().GetLayer() in SILK
                and fp.Reference().IsVisible()]

    for item, owner in targets:
        others = space.silk_items(item)
        layer = item.GetLayer()
        home = item.GetPosition()
        bb = item.GetBoundingBox()
        span = max(bb.GetWidth(), bb.GetHeight()) / 1e6 / 2.0 + 1.0
        placed = False
        for dx, dy in _candidates(home, span):
            item.SetPosition(pcbnew.VECTOR2I(home.x + int(dx * 1e6),
                                             home.y + int(dy * 1e6)))
            box = item.GetBoundingBox()
            if owner is not None and space.inside_other_part(box, owner):
                continue
            if space.clear(box, layer, others):
                placed = True
                if dx or dy:
                    moved += 1
                    if verbose:
                        print("  moved %-22s by (%+.2f, %+.2f) mm"
                              % (item.GetShownText(True, 0)[:22], dx, dy))
                break
        if not placed:
            # Nowhere legible to put it: keep the information on the fab
            # layer instead of shipping silk the fab house will clip.
            item.SetPosition(home)
            item.SetLayer(pcbnew.F_Fab if layer == pcbnew.F_SilkS
                          else pcbnew.B_Fab)
            hidden += 1
            if verbose:
                print("  no clear spot for %-18s -> moved to fab layer"
                      % item.GetShownText(True, 0)[:18])
    if verbose:
        print("silk tidy: %d repositioned, %d pushed to fab" % (moved, hidden))
    return moved, hidden
