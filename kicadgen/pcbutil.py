"""Board helpers shared by the shield and reader generators."""
import math

import pcbnew


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def stitch_islands(board, verbose=True, via_drill=0.6, via_pad=1.2):
    """Add the fewest GND vias that leave the pour in one piece.

    Eighteen plated through-hole GND pins already tie the two pours together,
    so blanket stitching only buys hand-soldered wire links. But "every island
    has a tie" is not the same as "the net is connected": each island joins
    whichever islands share one of its tie points, and those groups can still
    come out as two separate components -- which is exactly what happened
    here, one group hanging off each B.Cu island.

    So: fill, build the island graph, and if it has more than one component,
    drop a via where an island of one component overlaps an island of another.
    A point inside the filled polygon on both layers is already clear of every
    other net by the zone's own clearance.
    """
    def islands(board):
        out = []
        for z in board.Zones():
            if z.GetIsRuleArea():
                continue
            lay = z.GetLayer()
            poly = z.GetFilledPolysList(lay)
            for i in range(poly.OutlineCount()):
                out.append((lay, poly.Outline(i)))
        return out

    def ties(board):
        pts = []
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if (pad.GetNetname() == 'GND'
                        and pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH):
                    pts.append(pad.GetCenter())
        for t in board.GetTracks():
            if t.GetClass() == 'PCB_VIA' and t.GetNetname() == 'GND':
                pts.append(t.GetStart())
        return pts

    added = []
    # 24 rounds, not 8: rev B's no-pour square under the antenna splits each
    # face's pour into many more pieces, and 8 rounds left two GND pins
    # stranded on islands.
    for _ in range(24):
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.BuildConnectivity()

        isl = islands(board)
        pts = ties(board)
        parent = list(range(len(isl)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for pt in pts:
            touching = [i for i, (_l, o) in enumerate(isl) if o.PointInside(pt)]
            for other in touching[1:]:
                union(touching[0], other)

        groups = {}
        for i in range(len(isl)):
            groups.setdefault(find(i), []).append(i)
        if len(groups) <= 1:
            break

        # Bridge the two largest components: find a spot inside an island of
        # each, on opposite layers, with room for the via pad.
        order = sorted(groups.values(),
                       key=lambda g: -sum(abs(isl[i][1].Area()) for i in g))
        R, STEP = via_pad / 2 + 0.3, 0.25 if via_pad < 1.0 else 0.5
        # The pour clears copper, not drills: it runs right up to a bare
        # NPTH hole, so a spot inside it can still sit under DRC's hole rules
        # (0.254 copper-to-hole, 0.5 hole-to-hole). Keep the via's pad 0.3 mm
        # off every drilled hole's edge, which covers both.
        holes = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6,
                  max(p.GetDrillSize().x, p.GetDrillSize().y) / 2e6)
                 for fp in board.GetFootprints() for p in fp.Pads()
                 if p.GetDrillSize().x > 0]

        # and a through via exists on EVERY copper layer, not just the two
        # islands it joins: on 4 layers one bridged F.Cu to In1 straight
        # through a B.Cu track
        others = [t for t in board.GetTracks() if t.GetNetname() != 'GND']
        others += [p for fp in board.GetFootprints() for p in fp.Pads()
                   if p.GetNetname() != 'GND']
        cu = list(board.GetEnabledLayers().CuStack())

        # rule areas that forbid vias (screw hardware, say) are no-go too
        no_via = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]

        def clear_of_holes(x, y):
            if not all(math.hypot(x - hx, y - hy) >= via_pad / 2 + hr + 0.3
                       for hx, hy, hr in holes):
                return False
            pt, r = vec(x, y), mm(via_pad / 2 + 0.25)
            if any(z.Outline().Collide(pt, r) for z in no_via):
                return False
            return not any(it.IsOnLayer(lay) and it.GetEffectiveShape(lay).Collide(pt, r)
                           for it in others for lay in cu)
        spot = None
        # the largest piece with each other piece in turn, then any pair: a
        # thin piece may overlap the plane only where a track runs
        pairs = [(order[a], order[b]) for a in range(len(order))
                 for b in range(a + 1, len(order))]
        for home, away in pairs:
          if spot:
              break
          for i in home:
            if spot:
                break
            lay_a, oa = isl[i]
            for j in away:
                lay_b, ob = isl[j]
                if lay_a == lay_b:
                    continue
                ba, bb_ = oa.BBox(), ob.BBox()
                x0 = max(ba.GetLeft(), bb_.GetLeft()) / 1e6
                x1 = min(ba.GetRight(), bb_.GetRight()) / 1e6
                y0 = max(ba.GetTop(), bb_.GetTop()) / 1e6
                y1 = min(ba.GetBottom(), bb_.GetBottom()) / 1e6
                y = y0
                while y <= y1 and spot is None:
                    x = x0
                    while x <= x1:
                        ring = [(x, y)] + [
                            (x + R * math.cos(a * math.pi / 4),
                             y + R * math.sin(a * math.pi / 4)) for a in range(8)]
                        if (all(oa.PointInside(vec(*q)) for q in ring)
                                and all(ob.PointInside(vec(*q)) for q in ring)
                                and clear_of_holes(x, y)):
                            spot = (x, y)
                            break
                        x += STEP
                    y += STEP
                if spot:
                    break
        if spot is None:
            if verbose:
                print("  pour is in %d pieces and no spot bridges them"
                      % len(groups))
            break

        v = pcbnew.PCB_VIA(board)
        v.SetPosition(vec(*spot))
        v.SetDrill(mm(via_drill))
        v.SetWidth(mm(via_pad))
        v.SetNet(board.FindNet('GND'))
        v.SetLocked(True)              # generator copper, not routed
        board.Add(v)
        added.append(spot)
        if verbose:
            print("  stitched pour (%d pieces) at (%.2f, %.2f)"
                  % (len(groups), spot[0], spot[1]))
    if verbose:
        print("island stitching: %d via(s) added" % len(added))
    return added


# JLCPCB order: white solder mask, black silkscreen, ENIG, FR-4 1.6 mm, 1 oz.
# Written into the board file so the board itself says what is ordered, and so
# `kicad-cli pcb render --use-board-stackup-colors` shows the real colours.
WHITE_STACKUP = '''\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "Black"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "White") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "White") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "Black"))
\t\t\t(copper_finish "ENIG")
\t\t\t(dielectric_constraints no)
\t\t)
'''


# JLCPCB's standard 4-layer 1.6 mm (JLC04161H-7628), same colours and finish
WHITE_STACKUP_4 = '''\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "Black"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "White") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "White") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "Black"))
\t\t\t(copper_finish "ENIG")
\t\t\t(dielectric_constraints no)
\t\t)
'''


def set_white_stackup(path, layers=2):
    """Rewrite a saved .kicad_pcb so its setup carries the white stackup
    for its copper layer count (2 or 4)."""
    import re
    with open(path) as f:
        pcb = f.read()
    pcb = re.sub(r'\t\t\(stackup\n.*?\n\t\t\)\n', '', pcb, flags=re.S)
    if '\t(setup\n' not in pcb:
        raise SystemExit(f'{path}: no (setup) block to put the stackup in')
    pcb = pcb.replace('\t(setup\n', '\t(setup\n' + (WHITE_STACKUP_4 if layers == 4 else WHITE_STACKUP), 1)
    with open(path, 'w') as f:
        f.write(pcb)


def gnd_pad_vias(board, net='GND', via_drill=0.3, via_pad=0.6, track=0.3, clear=0.25,
                 skip=(), keep_y_min=None):
    """Give every SMD pad on `net` its own via to the inner plane: a short
    track from the pad to the first spot beside it that is clear on every
    copper layer. In a crowded strip the outer pours break into islands
    around ground pads that no stitching via fits in; this ties each pad to
    the plane directly (which is also the short ground path RF parts want)."""
    cu = list(board.GetEnabledLayers().CuStack())
    others = [t for t in board.GetTracks() if t.GetNetname() != net]
    others += [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() != net]
    all_pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    holes = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6,
              max(p.GetDrillSize().x, p.GetDrillSize().y) / 2e6)
             for fp in board.GetFootprints() for p in fp.Pads() if p.GetDrillSize().x > 0]
    graphics = [(g, lay) for fp in board.GetFootprints() for g in fp.GraphicalItems()
                for lay in cu if g.IsOnLayer(lay)]
    eb = board.GetBoardEdgesBoundingBox()
    placed = []
    n_obj = board.FindNet(net)

    def clear_at(x, y):
        if not (eb.GetLeft() / 1e6 + 0.8 < x < eb.GetRight() / 1e6 - 0.8
                and eb.GetTop() / 1e6 + 0.8 < y < eb.GetBottom() / 1e6 - 0.8):
            return False
        if keep_y_min is not None and y - via_pad / 2 < keep_y_min:
            return False
        if any(math.hypot(x - hx, y - hy) < via_pad / 2 + hr + 0.3 for hx, hy, hr in holes):
            return False
        if any(math.hypot(x - px, y - py) < via_pad + 0.3 for px, py in placed):
            return False
        pt, r = vec(x, y), mm(via_pad / 2 + clear)
        if any(it.IsOnLayer(lay) and it.GetEffectiveShape(lay).Collide(pt, r)
               for it in others for lay in cu):
            return False
        # and off every pad, its own net's too: a via in a pad wicks solder
        if any(p.IsOnLayer(lay) and p.GetEffectiveShape(lay).Collide(pt, mm(via_pad / 2))
               for p in all_pads for lay in (pcbnew.F_Cu, pcbnew.B_Cu)):
            return False
        return not any(g.GetEffectiveShape(lay).Collide(pt, r) for g, lay in graphics)

    def track_clear(a, b):
        seg = pcbnew.SHAPE_SEGMENT(vec(*a), vec(*b), mm(track))
        lay = pcbnew.F_Cu
        return not any(it.IsOnLayer(lay) and it.GetEffectiveShape(lay).Collide(seg, mm(clear))
                       for it in others)

    added = 0
    for fp in board.GetFootprints():
        if fp.GetReference() in skip:
            continue
        for p in fp.Pads():
            if p.GetNetname() != net or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            # already tied by the router (a track of this net leaves the pad)
            shp = p.GetEffectiveShape(pcbnew.F_Cu)
            if any(t.GetNetname() == net and t.IsOnLayer(pcbnew.F_Cu)
                   and shp.Collide(t.GetEffectiveShape(pcbnew.F_Cu), 0)
                   for t in board.GetTracks()):
                continue
            c = p.GetPosition()
            cx, cy = c.x / 1e6, c.y / 1e6
            half = max(p.GetSize().x, p.GetSize().y) / 2e6
            done = False
            for d in [half + via_pad / 2 + e for e in (0.25, 0.6, 1.0, 1.4, 1.8, 2.3)]:
                for k in range(24):
                    a = k * math.pi / 12
                    x, y = cx + d * math.cos(a), cy + d * math.sin(a)
                    if clear_at(x, y) and track_clear((cx, cy), (x, y)):
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(c)
                        t.SetEnd(vec(x, y))
                        t.SetWidth(mm(track))
                        t.SetLayer(pcbnew.F_Cu)
                        t.SetNet(n_obj)
                        t.SetLocked(True)      # drawn by the generator, not routed
                        board.Add(t)
                        v = pcbnew.PCB_VIA(board)
                        v.SetPosition(vec(x, y))
                        v.SetDrill(mm(via_drill))
                        v.SetWidth(mm(via_pad))
                        v.SetNet(n_obj)
                        v.SetLocked(True)
                        board.Add(v)
                        placed.append((x, y))
                        added += 1
                        done = True
                        break
                if done:
                    break
            if not done:
                print('  no ground via fits beside %s.%s' % (fp.GetReference(), p.GetNumber()))
    print('ground pad vias: %d' % added)
    return added
