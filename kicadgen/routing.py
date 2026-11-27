"""Generic signal router for the generated boards. A board's own
route_signals.py drives it; see the skill's SKILL.md for the workflow.
"""
import math
import os
import sys

import pcbnew

from kicadgen import router
from kicadgen.router import GRID, LAYERS, LAYER_NAME, Space, astar, node, pos



def pad_layers(pad):
    return [lay for lay in LAYERS
            if pad.IsOnLayer(lay) or pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]


def pad_nodes(pad):
    """Grid nodes that genuinely sit inside the pad, per layer it appears on.

    Walk the LATTICE over the pad's bounding box and test the lattice point
    itself. Sampling from the raw bbox edge instead lets node() round a sample
    onto a lattice point just outside the pad, and a track ending there is
    dangling rather than connected.
    """
    out = set()
    bb = pad.GetBoundingBox()
    gx0 = int(math.floor(bb.GetLeft() / 1e6 / GRID))
    gx1 = int(math.ceil(bb.GetRight() / 1e6 / GRID))
    gy0 = int(math.floor(bb.GetTop() / 1e6 / GRID))
    gy1 = int(math.ceil(bb.GetBottom() / 1e6 / GRID))
    for lay in pad_layers(pad):
        shape = pad.GetEffectiveShape(lay)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                if shape.Collide(router.vec(gx * GRID, gy * GRID), 0):
                    out.add((gx, gy, lay))
    return out


def trim_dangling(board, segments):
    """Drop track ends that connect to nothing.

    A route ends on any lattice node of the copper already laid, but that
    copper was smoothed after it was routed, so the node it was aiming for
    can lie a fraction of a millimetre past the real track. The overshoot is
    a stub DRC flags as dangling. Remove such segments until none is left; a
    route that is genuinely unconnected loses its stubs and DRC then reports
    it as unconnected, which is the true state.
    """
    def key(t):
        a, b = t.GetStart(), t.GetEnd()
        return (t.GetNetname(), t.GetLayerName(), round(a.x / 1e6, 4),
                round(a.y / 1e6, 4), round(b.x / 1e6, 4), round(b.y / 1e6, 4))
    dropped = set()
    while True:
        board.BuildConnectivity()
        conn = board.GetConnectivity()
        dead = [t for t in board.GetTracks()
                if t.GetClass() == 'PCB_TRACK'
                and conn.TestTrackEndpointDangling(t, False)]
        if not dead:
            break
        for t in dead:
            print("  trimmed dangling %s stub on %s at (%.2f, %.2f)"
                  % (t.GetNetname(), t.GetLayerName(),
                     t.GetStart().x / 1e6, t.GetStart().y / 1e6))
            dropped.add(key(t))
            board.Remove(t)
    return [s for s in segments
            if (s[0], s[1], round(s[2], 4), round(s[3], 4),
                round(s[4], 4), round(s[5], 4)) not in dropped]


def route_board(board_path, out_path, poured_nets=('GND',), rails=None,
                signal_width=0.3, tie_width=0.5, first=(), failed_out=None,
                front_only=()):
    """Route every non-poured net of the board at board_path and write the
    frozen copper to out_path. Returns the number of nets or ties that failed.

    `first` names nets to route before all others, in that order (a caller
    retrying after a failure puts the failed nets here); `failed_out`, if a
    list, receives the names of the nets that failed. Nets in `front_only`
    get no vias, so they stay on F.Cu and leave B.Cu to the others."""
    rails = rails or {}
    first = list(first)
    board = pcbnew.LoadBoard(board_path)

    # Walk footprints and pads in a FIXED order. gen_pcb.py gives every
    # footprint a fresh random UUID, KiCad's order follows them, and that order
    # decides which pad a net starts from and which of two equal-span nets goes
    # first - so the same board routed differently on every run (rev B saw
    # 9 vias one run and 10 the next, one of them a DRC failure).
    nets = {}
    poured = {}
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        for p in sorted(fp.Pads(), key=lambda q: q.GetNumber()):
            n = p.GetNetname()
            if n in poured_nets:
                poured.setdefault(n, []).append(p)
            elif n and not n.startswith('unconnected'):
                nets.setdefault(n, []).append(p)

    # A poured net still needs tracks to any of its pads that sit in a
    # no-pour area (rev B: the square under the RC522's antenna, which holds
    # the RTC's GND pin and one of the D1's). Each such pad is routed to the
    # nearest pad of the same net OUTSIDE the area, which the pour reaches.
    def no_pour(pad):
        c = pad.GetCenter()
        return any(z.GetIsRuleArea() and z.GetDoNotAllowZoneFills()
                   and z.Outline().Contains(c) for z in board.Zones())
    ties = []          # (net, stranded pad, [pads the pour reaches])
    for n, pl in poured.items():
        inside = [p for p in pl if no_pour(p)]
        # Through-hole pads only. An SMD pad joins one face's pour, and that
        # piece can be a small island with nothing else on it: moving SW1
        # left Q1's emitter on one, the tie went there, and the RTC's GND pin
        # ended up connected to Q1 and nothing else. A through-hole pad joins
        # both faces' pours, and those are what stitch_islands() in gen_pcb.py
        # counts when it makes the pour one piece.
        outside = [p for p in pl if not no_pour(p)
                   and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH] or \
                  [p for p in pl if not no_pour(p)]
        for p in inside:
            ties.append((n, p, outside))
        if inside:
            print("%s: %d pad(s) in a no-pour area, tied back to the pour: %s"
                  % (n, len(inside), ", ".join(
                      "%s.%s" % (p.GetParentFootprint().GetReference(),
                                 p.GetNumber()) for p in inside)))

    # Already-routed nets (the locked hand routes) are left alone.
    routed = set()
    for t in board.GetTracks():
        if t.IsLocked() and t.GetNetname() in nets:
            routed.add(t.GetNetname())
    if routed:
        print("leaving hand-routed nets alone:", ", ".join(sorted(routed)))

    todo = [n for n in nets if n not in routed and len(nets[n]) > 1]
    # Short, tightly-boxed nets first: they have the least room to manoeuvre,
    # and the sprawling ones can still find their way round afterwards.
    def span(n):
        xs = [p.GetCenter().x for p in nets[n]]
        ys = [p.GetCenter().y for p in nets[n]]
        return (max(xs) - min(xs) + max(ys) - min(ys)) / 1e6
    # Rails first, then the sprawling signals while there is still room.
    todo.sort(key=lambda n: (first.index(n) if n in first else len(first),
                             n not in rails, -round(span(n), 3), n))

    segments, vias, failed = [], [], []

    # Ties: each stranded pad of a poured net to ANY of that net's pads the
    # pour reaches - whichever is cheapest - one pad at a time. Routed FIRST,
    # on the empty board: they are a few mm long, and routed last the RTC's
    # GND pin (J6.6) was already walled in by +3V3.
    #
    # Rev B's no-pour area covers most of the shield, so a stranded pad may
    # join the copper of an earlier tie as well as a pad the pour reaches:
    # the ties grow a GND tree outward from the pour, nearest pads first.
    def to_pour(item):
        net, pad, outside = item
        c = pad.GetCenter()
        return min(math.hypot((c.x - q.GetCenter().x) / 1e6, (c.y - q.GetCenter().y) / 1e6)
                   for q in outside) if outside else 0.0
    ties.sort(key=lambda t: (t[0], round(to_pour(t), 3),
                             t[1].GetParentFootprint().GetReference(), t[1].GetNumber()))
    grown = {}         # net -> (lattice nodes, sample points) of its ties so far
    for netname, pad, outside in ties:
        space = Space(board, netname, tie_width)
        goals, gpts = set(), []
        for q in outside:
            goals |= pad_nodes(q)
            gpts.append((q.GetCenter().x / 1e6, q.GetCenter().y / 1e6))
        g_nodes, g_pts = grown.get(netname, (set(), []))
        goals |= g_nodes
        gpts += g_pts
        path = astar(space, sorted(pad_nodes(pad)), goals, gpts)
        tag = "%s.%s" % (pad.GetParentFootprint().GetReference(), pad.GetNumber())
        if path is None:
            failed.append("%s tie %s" % (netname, tag))
            print("  FAILED %s tie %s" % (netname, tag))
            continue
        tie_segs, tie_vias = [], []
        runs, run = [], [path[0]]
        for prev, cur in zip(path, path[1:]):
            if cur[2] != prev[2]:
                if len(run) > 1:
                    runs.append((run, prev[2]))
                tie_vias.append(pos(prev))
                run = [cur]
            else:
                run.append(cur)
        if len(run) > 1:
            runs.append((run, run[0][2]))
        for i, (r, lay) in enumerate(runs):
            pts = [pos(n) for n in r]
            if i == 0:
                c = (pad.GetCenter().x / 1e6, pad.GetCenter().y / 1e6)
                if lay in pad_layers(pad) and c != pts[0]:
                    pts.insert(0, c)
            pts = router.diagonalise(
                space, router.smooth(space, router.simplify(pts), lay), lay)
            for a, b in zip(pts, pts[1:]):
                if a != b:
                    tie_segs.append((LAYER_NAME[lay], a, b))
        net_obj = board.FindNet(netname)
        for lay_name, a, b in tie_segs:
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(router.vec(*a))
            t.SetEnd(router.vec(*b))
            t.SetWidth(int(tie_width * 1e6))
            t.SetLayer(pcbnew.F_Cu if lay_name == 'F.Cu' else pcbnew.B_Cu)
            t.SetNet(net_obj)
            board.Add(t)
            segments.append((netname, lay_name, a[0], a[1], b[0], b[1],
                             tie_width))
        for x, y in tie_vias:
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(router.vec(x, y))
            v.SetDrill(int(router.VIA_DRILL * 1e6))
            v.SetWidth(int(router.VIA_PAD * 1e6))
            v.SetNet(net_obj)
            board.Add(v)
            vias.append((x, y, netname, router.VIA_PAD, router.VIA_DRILL))
        print("  %-16s tie %s: %d segments, %d via(s)"
              % (netname, tag, len(tie_segs), len(tie_vias)))
        g_nodes, g_pts = grown.setdefault(netname, (set(), []))
        g_nodes |= set(path) | pad_nodes(pad)
        g_pts.extend(pos(n) for n in path[::4])


    for netname in todo:
        pads = nets[netname]
        width = rails.get(netname, signal_width)
        space = Space(board, netname, width)
        if netname in front_only:
            space.blocked_via = lambda gx, gy: True
        net_obj = board.FindNet(netname)

        connected = pad_nodes(pads[0])
        conn_pts = [(pads[0].GetCenter().x / 1e6, pads[0].GetCenter().y / 1e6)]
        remaining = list(pads[1:])
        net_segs, net_vias = [], []
        ok = True

        while remaining and ok:
            # Prim: attach whichever pad is nearest to what is already joined.
            def dist(p):
                x, y = p.GetCenter().x / 1e6, p.GetCenter().y / 1e6
                return min(math.hypot(x - cx, y - cy) for cx, cy in conn_pts)
            remaining.sort(key=dist)
            pad = remaining.pop(0)
            starts = sorted(pad_nodes(pad))
            path = astar(space, starts, connected, conn_pts)
            if path is None:
                ok = False
                break

            # split the node path into per-layer runs, with a via at each change
            runs, run = [], [path[0]]
            for prev, cur in zip(path, path[1:]):
                if cur[2] != prev[2]:
                    if len(run) > 1:
                        runs.append((run, prev[2]))
                    net_vias.append(pos(prev))
                    run = [cur]
                else:
                    run.append(cur)
            if len(run) > 1:
                runs.append((run, run[0][2]))

            for i, (r, lay) in enumerate(runs):
                pts = [pos(n) for n in r]
                if i == 0:
                    # Tie the start back to the pad's true centre. The lattice
                    # point is inside the pad, so this stub is too, and it
                    # makes the connection independent of grid rounding.
                    c = (pad.GetCenter().x / 1e6, pad.GetCenter().y / 1e6)
                    if lay in pad_layers(pad) and c != pts[0]:
                        pts.insert(0, c)
                pts = router.diagonalise(
                    space, router.smooth(space, router.simplify(pts), lay), lay)
                for a, b in zip(pts, pts[1:]):
                    if a != b:
                        net_segs.append((LAYER_NAME[lay], a, b))

            connected |= set(path)
            conn_pts.append((pad.GetCenter().x / 1e6, pad.GetCenter().y / 1e6))

        if not ok:
            failed.append(netname)
            print("  FAILED %s" % netname)
            continue

        # Commit to the board so later nets route around this one.
        for lay_name, a, b in net_segs:
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(router.vec(*a))
            t.SetEnd(router.vec(*b))
            t.SetWidth(int(width * 1e6))
            t.SetLayer(pcbnew.F_Cu if lay_name == 'F.Cu' else pcbnew.B_Cu)
            t.SetNet(net_obj)
            board.Add(t)
            segments.append((netname, lay_name, a[0], a[1], b[0], b[1],
                             width))
        for x, y in net_vias:
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(router.vec(x, y))
            v.SetDrill(int(router.VIA_DRILL * 1e6))
            v.SetWidth(int(router.VIA_PAD * 1e6))
            v.SetNet(net_obj)
            board.Add(v)
            vias.append((x, y, netname, router.VIA_PAD, router.VIA_DRILL))
        print("  %-16s %2d segments, %d via(s)" % (netname, len(net_segs),
                                                   len(net_vias)))

    segments = trim_dangling(board, segments)

    with open(out_path, 'w') as f:
        f.write('"""Signal copper routed by route_signals.py.\n\n'
                'Generated - do not hand-edit. Vias are priced at %g mm of\n'
                'copper, so the router detours rather than changing layer.\n'
                'Re-run route_signals.py if the placement changes.\n"""\n\n'
                % router.VIA_MM)
        f.write('# (net, layer, x1, y1, x2, y2, width)\nSEGMENTS = [\n')
        for s in segments:
            f.write('    (%r, %r, %.4f, %.4f, %.4f, %.4f, %.2f),\n' % s)
        f.write(']\n\n# (x, y, net, pad, drill)\nVIAS = [\n')
        for v in vias:
            f.write('    (%.4f, %.4f, %r, %.2f, %.2f),\n' % v)
        f.write(']\n')

    print("\nrouted %d nets: %d segments, %d vias -> %s"
          % (len(todo) - len(failed), len(segments), len(vias), out_path))
    if failed:
        print("UNROUTED: %s" % ", ".join(failed))
    if failed_out is not None:
        failed_out.extend(failed)
    return len(failed)



def _item_nodes(item, net):
    """Lattice nodes inside a pad, track or via of `net`, per copper layer."""
    if isinstance(item, pcbnew.PAD):
        return pad_nodes(item)
    out = set()
    bb = item.GetBoundingBox()
    gx0, gx1 = int(math.floor(bb.GetLeft() / 1e6 / GRID)), int(math.ceil(bb.GetRight() / 1e6 / GRID))
    gy0, gy1 = int(math.floor(bb.GetTop() / 1e6 / GRID)), int(math.ceil(bb.GetBottom() / 1e6 / GRID))
    lays = LAYERS if item.GetClass() == 'PCB_VIA' else [item.GetLayer()]
    for lay in lays:
        shape = item.GetEffectiveShape(lay)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                if shape.Collide(router.vec(gx * GRID, gy * GRID), 0):
                    out.add((gx, gy, lay))
    return out


ALL_CU = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)


def _islands(board, net):
    """Connected groups of this net's pads, tracks and vias (by copper contact)."""
    items = [p for f in board.GetFootprints() for p in f.Pads() if p.GetNetname() == net]
    items += [t for t in board.GetTracks() if t.GetNetname() == net]
    parent = list(range(len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            for lay in ALL_CU:
                if a.IsOnLayer(lay) and b.IsOnLayer(lay) and \
                        a.GetEffectiveShape(lay).Collide(b.GetEffectiveShape(lay), 0):
                    parent[find(j)] = find(i)
                    break
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


def _seg_key(net, lay, x1, y1, x2, y2):
    a, b = (round(x1, 3), round(y1, 3)), (round(x2, 3), round(y2, 3))
    return (net, lay) + (a + b if a <= b else b + a)


def _join(board, netname, width):
    """Route this net's islands together on the board as it stands. Returns
    the islands it could not reach (empty when the net is whole)."""
    isl = _islands(board, netname)
    if len(isl) < 2:
        return []
    net_obj = board.FindNet(netname)
    isl.sort(key=len, reverse=True)
    connected = set()
    for it in isl[0]:
        connected |= _item_nodes(it, netname)
    left = []
    for group in isl[1:]:
        space = Space(board, netname, width)
        starts = set()
        for it in group:
            starts |= _item_nodes(it, netname)
        pts = [pos(n) for n in list(connected)[::7]] or [(0, 0)]
        path = astar(space, sorted(starts), connected, pts)
        if path is None:
            left.append(group)
            continue
        runs, run = [], [path[0]]
        for prev, cur in zip(path, path[1:]):
            if cur[2] != prev[2]:
                if len(run) > 1:
                    runs.append((run, prev[2]))
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(router.vec(*pos(prev)))
                v.SetDrill(int(router.VIA_DRILL * 1e6))
                v.SetWidth(int(router.VIA_PAD * 1e6))
                v.SetNet(net_obj)
                board.Add(v)
                run = [cur]
            else:
                run.append(cur)
        if len(run) > 1:
            runs.append((run, run[0][2]))
        for r, lay in runs:
            p = router.diagonalise(space, router.smooth(space, router.simplify([pos(n) for n in r]), lay), lay)
            for a, b in zip(p, p[1:]):
                if a == b:
                    continue
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(router.vec(*a))
                t.SetEnd(router.vec(*b))
                t.SetWidth(int(width * 1e6))
                t.SetLayer(lay)
                t.SetNet(net_obj)
                board.Add(t)
        connected |= set(path) | starts
    return left


def complete_nets(board_path, out_path, skip=('GND',), widths=None, default_width=0.2,
                  rounds=1, rip_margin=1.5):
    """Join every net's disconnected islands on an already-routed board, with
    rip-up: a net that cannot be joined has the OTHER nets' routed copper in
    its way (the box spanning its islands, plus rip_margin) removed, is routed
    first, and the ripped nets are re-joined after it. Only copper listed in
    out_path (the frozen routes) is ever ripped; copper the generator draws
    itself is locked and stays. Rewrites out_path from the final board.
    Returns the nets still open."""
    widths = widths or {}
    board = pcbnew.LoadBoard(board_path)
    # What the generator draws itself (coil bridge, escapes, ground and
    # thermal vias) is LOCKED; everything unlocked came from the routes file.
    # (Matching by coordinates instead dropped any route segment the
    # generator had nudged by a few microns, and broke six nets on rev B.)
    fixed = {t.m_Uuid.AsString() for t in board.GetTracks() if t.IsLocked()}

    nets = sorted({p.GetNetname() for f in board.GetFootprints() for p in f.Pads()
                   if p.GetNetname() and p.GetNetname() not in skip
                   and not p.GetNetname().startswith('unconnected')})
    todo = list(nets)
    failed = []
    dead = set()
    for rnd in range(rounds):
        failed = []
        for netname in todo:
            if _join(board, netname, widths.get(netname, default_width)):
                failed.append(netname)
        print('  completion round %d: %s' % (rnd + 1, ', '.join(failed) or 'all joined'))
        if not failed or rnd == rounds - 1:
            break      # never rip after the last round: nothing would put it back
        # rip the other nets' routed copper out of each failed net's box
        ripped = set()
        for netname in failed:
            # rip only around the gap: the closest pair of points between the
            # main island and each stray one (a box around the whole net tore
            # up the entire strip every round and never converged)
            isl = sorted(_islands(board, netname), key=len, reverse=True)
            main = [it.GetPosition() for it in isl[0]]
            boxes = []
            for g in isl[1:]:
                pa, pb = min(((a, b) for a in main for b in (it.GetPosition() for it in g)),
                             key=lambda ab: (ab[0].x - ab[1].x) ** 2 + (ab[0].y - ab[1].y) ** 2)
                boxes.append((min(pa.x, pb.x) / 1e6 - rip_margin, min(pa.y, pb.y) / 1e6 - rip_margin,
                              max(pa.x, pb.x) / 1e6 + rip_margin, max(pa.y, pb.y) / 1e6 + rip_margin))
            for t in list(board.GetTracks()):
                n = t.GetNetname()
                if not n or n == netname or n in skip or t.m_Uuid.AsString() in fixed:
                    continue
                bb = t.GetBoundingBox()
                if not any(bb.GetRight() / 1e6 >= x0 and bb.GetLeft() / 1e6 <= x1
                           and bb.GetBottom() / 1e6 >= y0 and bb.GetTop() / 1e6 <= y1
                           for x0, y0, x1, y1 in boxes):
                    continue
                ripped.add(n)
                # Not board.Remove(): the bindings hand back dead objects
                # after it ('SwigPyObject has no attribute Pads'). Strip the
                # net and park it off the board instead; it is left out below.
                dead.add(t.m_Uuid.AsString())
                t.SetNetCode(0)
                if t.GetClass() == 'PCB_VIA':
                    t.SetPosition(router.vec(-500, -500))
                else:
                    t.SetStart(router.vec(-500, -500))
                    t.SetEnd(router.vec(-500, -500.1))
        todo = failed + sorted(ripped - set(failed))
        print('    ripped up: %s' % (', '.join(sorted(ripped)) or 'nothing'))
    segs, vias = [], []
    # an unlocked copy of locked (generator) copper is not route copper
    locked_keys = set()
    for t in board.GetTracks():
        if t.m_Uuid.AsString() in fixed:
            if t.GetClass() == 'PCB_VIA':
                locked_keys.add(('v', t.GetNetname(), round(t.GetPosition().x / 1e6, 2),
                                 round(t.GetPosition().y / 1e6, 2)))
            else:
                a, b = t.GetStart(), t.GetEnd()
                locked_keys.add(_seg_key(t.GetNetname(), t.GetLayerName(), a.x / 1e6, a.y / 1e6,
                                         b.x / 1e6, b.y / 1e6))
    for t in board.GetTracks():
        if t.m_Uuid.AsString() in fixed or t.m_Uuid.AsString() in dead:
            continue
        if t.GetClass() == 'PCB_VIA':
            if ('v', t.GetNetname(), round(t.GetPosition().x / 1e6, 2),
                    round(t.GetPosition().y / 1e6, 2)) in locked_keys:
                continue
        else:
            a, b = t.GetStart(), t.GetEnd()
            if _seg_key(t.GetNetname(), t.GetLayerName(), a.x / 1e6, a.y / 1e6,
                        b.x / 1e6, b.y / 1e6) in locked_keys:
                continue
        if t.GetClass() == 'PCB_VIA':
            p = t.GetPosition()
            # GetDrillValue: the drill actually used (GetDrill is 0 when a via
            # takes the netclass default)
            vias.append((p.x / 1e6, p.y / 1e6, t.GetNetname(), t.GetWidth(pcbnew.F_Cu) / 1e6,
                         t.GetDrillValue() / 1e6))
        else:
            a, b = t.GetStart(), t.GetEnd()
            segs.append((t.GetNetname(), t.GetLayerName(), a.x / 1e6, a.y / 1e6, b.x / 1e6, b.y / 1e6,
                         t.GetWidth() / 1e6))
    segs.sort()
    vias.sort()
    with open(out_path, 'w') as f:
        f.write('"""Signal copper: routed, then completed with rip-up by kicadgen.routing.\n\n'
                'Generated - do not hand-edit. Re-run route_signals.py if the\nplacement changes.\n"""\n\n'
                '# (net, layer, x1, y1, x2, y2, width)\nSEGMENTS = [\n')
        for sg in segs:
            f.write('    (%r, %r, %.4f, %.4f, %.4f, %.4f, %.3f),\n' % sg)
        f.write(']\n\n# (x, y, net, pad, drill)\nVIAS = [\n')
        for v in vias:
            f.write('    (%.4f, %.4f, %r, %.2f, %.2f),\n' % v)
        f.write(']\n')
    print('completion: %d segments, %d vias; failed: %s'
          % (len(segs), len(vias), ', '.join(failed) or 'none'))
    return failed
