# building-pcbs-as-code reference

## Environment (macOS)

```sh
KIPY=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3
export PATH="/Applications/KiCad/KiCad.app/Contents/MacOS:$PATH"   # kicad-cli
# Freerouting: $FREEROUTING_JAR, default ~/freerouting.jar (a 2026-09 build that honours -mp).
#   Not 2.1.0: it ignores the pass limit headless. v2.5.0 (2026-10-05, freerouting-2.5.0.jar on
#   Java 25) stops on a stalled score; on the companion board it routed far worse than the 2026-09 jar.
#   2.5.0 needs profile.id (a UUID) in its freerouting.json or it stops with a NullPointerException;
#   set profile.allow_telemetry false and usage_and_diagnostic_data.disable_analytics true (both default on).
#   Nested --router.autorouter.* keys are preferred; flat keys like --router.max_passes are deprecated.
#   Always wrap it: timeout 900 java -jar "$FREEROUTING_JAR" ...
# SVG -> PNG for logos: /opt/homebrew/bin/rsvg-convert
```

Symbols and footprints come from `/Applications/KiCad/KiCad.app/Contents/SharedSupport/{symbols,footprints}`, plus a per-project `.pretty` and `.kicad_sym` registered in `fp-lib-table` and `sym-lib-table`.

## kicad-cli gates

```sh
kicad-cli sch erc --output build/erc.rpt --format report --units mm \
  --severity-all --exit-code-violations $P.kicad_sch
kicad-cli sch export netlist --format kicadxml --output build/netlist.xml $P.kicad_sch
kicad-cli pcb drc --output build/drc.rpt --format report --units mm \
  --severity-all --refill-zones --schematic-parity --exit-code-violations $P.kicad_pcb
```

## Fab outputs (export.sh)

```sh
L_FAB="F.Cu,In1.Cu,In2.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
kicad-cli pcb export gerbers --output build/gerbers --layers "$L_FAB" \
  --subtract-soldermask --precision 5 $P.kicad_pcb
kicad-cli pcb export drill --output build/gerbers --format excellon \
  --drill-origin absolute --excellon-units mm --excellon-zeros-format decimal $P.kicad_pcb
kicad-cli pcb export pos --output build/positions.csv --format csv --units mm \
  --side both --exclude-dnp $P.kicad_pcb
python3 ../kicadgen/jlc.py build/positions.csv build/ jlc_parts.csv   # jlc_bom.csv + jlc_cpl.csv
for side in top bottom; do
  kicad-cli pcb render --output build/render_$side.png --width 1400 --height 1400 \
    --quality high --use-board-stackup-colors --side $side $P.kicad_pcb
done
```

For `--use-board-stackup-colors` to show the ordered colours, the board needs a stackup. `pcbutil.set_white_stackup(path, layers)` rewrites it after saving.

## jlc_parts.csv

```csv
Value,Comment,LCSC
470nH 2%,LQW2UASR47G00L,C6980553
120pF C0G 100V,YAGEO CC0603FRNPO0BN121,C576768
WS2812B,"WS2812B-B/T (C2761795) fitted by hand: Standard PCBA only",skip
```

The table matches on the schematic Value. The MPN overrides the Comment, and `skip` drops the part from both files. JLC's tool matches the remaining parts by value and footprint. Stock lookups: `https://jlcpcb.com/partdetail/C<n>`.

## Freezing and completing routes

```text
rm routes_signals.py && $KIPY gen_pcb.py     # bare board
$KIPY route_signals.py                       # Freerouting -> complete_nets -> routes_signals.py
$KIPY gen_pcb.py                             # replays SEGMENTS / VIAS
# after a small move: hand-delete stale SEGMENTS/VIAS for those nets, then
$KIPY gen_pcb.py && $KIPY route_signals.py --complete && $KIPY gen_pcb.py
```

`routes_signals.py` holds two plain lists:
- `SEGMENTS = [(net, layer, x1, y1, x2, y2, width), ...]`
- `VIAS = [(x, y, net, pad, drill), ...]`

Back it up before editing it by hand.

## Placement loop (dense boards)

The commands, from the companion board:

```sh
python3 place.py --only 95x85 --seeds 16 --right-only --tag _h     # floorplan search + anneal per seed
python3 place.py --use build/place_best_95x85_h.json && python3 assign_pins.py --spec
$KIPY gen_sch.py && kicad-cli sch export netlist --format kicadxml --output build/netlist.xml companion-board.kicad_sch
$KIPY route_signals.py --pairs && $KIPY route_signals.py && ./build_all.sh
```

Cost model:
- **Hard (reject the move):** overlap with a KLC clearance halo, board outline, edge-bound connectors, keep-outs (antenna, buck switch node), board side, allowed rotations, and the served-pin maximum distances.
- **Soft (squared penalties, each reported on its own):** MST length, crossings, congestion overflow `sum(max(0, use - 1)**2)`, block cohesion.

Anneal budget: VPR's rule is about `0.5 * N**(4/3)` moves per temperature, about 520 for 184 parts. Use adaptive cooling with a range window. The crossing test needs a spatial grid, or it limits the move count.

Choosing the winner: route the 3–5 Pareto-best candidates (length, crossings, overflow) in parallel and keep the one with the best completion, then via count, then length. Log proxy against outcome, so the weights can be fitted later.

Evidence base: Cypress (NVIDIA/Cornell, ISPD 2025, github.com/NVlabs/Cypress, Apache-2.0) is analytical placement with crossing, orientation and halo terms, judged by Freerouting on 2 layers. It routed all 10 benchmarks at 41–476 parts. Independent studies found tuned SA beating RL placers, and LLM agents are poor placers. Use LLMs to review structured DRC and unrouted reports, never to certify a board.

## Pin labels on the back silk that stay on their pins

```python
for pad in placed['J1'].Pads():
    t = pcbnew.PCB_TEXT(board)
    t.SetText(LABELS[int(pad.GetNumber()) - 1])
    t.SetLayer(pcbnew.B_SilkS)
    t.SetMirrored(True)
    t.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
    t.SetTextThickness(mm(0.15))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)   # anchor = text centre
    t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
    t.SetTextAngleDegrees(90)
    pp = pad.GetPosition()
    t.SetPosition(pp)
    length = t.GetBoundingBox().GetHeight()
    # start clear of the header outline, or tidy_silk shoves the text aside
    t.SetPosition(pcbnew.VECTOR2I(pp.x, pp.y + mm(2.1) + length // 2))
    board.Add(t)
```

Afterwards, read every label's final position back and assert it equals its pin x.

## Mutation test skeleton (test_verify.py)

```python
CASES = [('J1 moved 1 mm', move('J1', 1.0, 0), 'J1.1 at'),
         ('GND track under the coil', gnd_in_keepout, 'inside the coil keep-out')]
# per case: subprocess "--case i out.kicad_pcb" applies the mutation and saves the copy,
# then run verify.py with BOARD=<copy> and expect returncode != 0 plus a FAIL line
# containing the expected text. Copy .kicad_pro, *-table and *.pretty next to the copies.
```

Run each mutation in its own process. `LoadBoard` caches by path and can hand back a board that an earlier case already mutated.

## NFC match model (from the reader)

The design model for the CLRC663 reader is `nfc_match_model.py` in this directory (numpy). Its geometry block at the top describes that reader's 3-turn 94 × 78 mm coil, so replace it for a new coil. It computes the coil's L (Neumann integral), its AC R, the effect of nearby copper sheets, and the match:
- `z_tx(Lc, Rc, C1, C2, Rq, parasitic=True, detail=True)` returns the drive impedance Zd and the coil current per volt.
- `drive(Zd, I, tvdd)` returns the TVDD current.

Targets were:
- |Z| 17–21 Ω with |X| ≤ 3 Ω
- 98–135 mA at 3.3 V
- 146–202 mA at 5 V

Stocked parts that land in the window: C1 47 pF, C2 120 pF and Rq 3.00 Ω (2512, 1 W). That gives 20.1 + j2.1 Ω.
