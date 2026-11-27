"""Flag pads that foul fixed board features.

The power rails are hand-routed and locked, and the module mounting slots sit
where the real modules' holes sit; neither moves to suit a part. After any
re-placement this says which pads landed on top of them, so the conflicts come
out in one list instead of one DRC run at a time.

Run from a board folder with KiCad's python:
    <kicad-python> ../kicadgen/check_place.py [board.kicad_pcb]
"""
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kicadgen import project  # noqa: E402

CLEAR = 0.35        # mm, pad copper to foreign copper


def main():
    board = pcbnew.LoadBoard(sys.argv[1] if len(sys.argv) > 1 else project.board())
    locked = [t for t in board.GetTracks()
              if t.GetClass() == 'PCB_TRACK' and t.IsLocked()]
    slots = [(fp, p) for fp in board.GetFootprints() for p in fp.Pads()
             if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]

    hits = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                continue
            name = "%s.%s" % (fp.GetReference(), pad.GetPadName())
            net = pad.GetNetname()
            for lay in (pcbnew.F_Cu, pcbnew.B_Cu):
                if not (pad.IsOnLayer(lay)
                        or pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH):
                    continue
                ps = pad.GetEffectiveShape(lay)
                for t in locked:
                    if t.GetNetname() == net or not t.IsOnLayer(lay):
                        continue
                    if ps.Collide(t.GetEffectiveShape(lay),
                                  int(CLEAR * 1e6)):
                        c = pad.GetCenter()
                        print("PAD-ON-RAIL   %-7s [%-12s] at (%.2f,%.2f) "
                              "sits on locked %s track"
                              % (name, net, c.x / 1e6, c.y / 1e6,
                                 t.GetNetname()))
                        hits += 1
                        break
            for mfp, slot in slots:
                d = max(0.6, (slot.GetLocalClearance() or 0) / 1e6)
                if pad.GetEffectiveShape(pcbnew.F_Cu).Collide(
                        slot.GetEffectiveShape(pcbnew.F_Cu), int(d * 1e6)):
                    c = pad.GetCenter()
                    print("PAD-ON-SLOT   %-7s at (%.2f,%.2f) fouls %s "
                          "mounting slot" % (name, c.x / 1e6, c.y / 1e6,
                                             mfp.GetReference()))
                    hits += 1
    print("%d placement conflict(s)" % hits)
    return 1 if hits else 0


if __name__ == '__main__':
    sys.exit(main())
