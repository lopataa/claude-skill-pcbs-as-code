---
name: building-pcbs-as-code
description: Use when designing, revising or debugging a PCB generated from Python scripts with KiCad's pcbnew API and kicad-cli (no GUI editing), when ordering such a board from JLCPCB with assembly, or when a generated board has silk text in the wrong place, broken or non-reproducible routing, DRC or parity failures, an NFC/13.56 MHz antenna, or an ESP32 next to an RF reader. Also when placing 100+ parts automatically, choosing MCU pins for a layout, a board that won't route or won't shrink, or Freerouting that hangs.
---

# Building PCBs as code

## Overview

The `.kicad_sch` and `.kicad_pcb` are **build outputs**. The source is the generator scripts plus small data files: the parts table, the frozen routes and the mating-board geometry. Every change goes into a script and the board is rebuilt with `build_all.sh`. Never fix a board by hand in the GUI.

The shared library ships with this skill in `kicadgen/` (sch, pcbutil, router/routing, tidy_silk, jlc, plus the command-line tools check_place, dump_geom, plot_zoom and gen_logo). Copy it next to the board folders; board scripts put `..` on `sys.path` and `from kicadgen import ...`. Edit your copy, not the skill's.

## Pipeline (`build_all.sh`, `set -e`)

| Step | Tool | Gate |
|---|---|---|
| symbols, special footprints (coil, logo) | `gen_symbols.py`, `gen_coil.py` | `test_coil.py` |
| schematic | `gen_sch.py` (kicadgen.sch, net-label stubs) | `kicad-cli sch erc --severity-all --exit-code-violations` |
| netlist | `kicad-cli sch export netlist --format kicadxml` | `test_netlist.py` |
| placement (dense boards) | `assign_pins.py` → `pinmap.json`; `place.py` → `placement.json` (see **Placement**) | crossings and length limits |
| board | `gen_pcb.py`: place fixed parts by hand (or from `placement.json`), pack the rest next to the pin they serve, apply frozen routes, pour, stitch, `tidy_silk.tidy()` | |
| checks | `kicad-cli pcb drc --refill-zones --schematic-parity --severity-all --exit-code-violations` | DRC 0, 0 unconnected |
| spec | `verify.py` asserts the spec numerically | all pass |
| check the checks | `test_verify.py` breaks the board N ways | N/N caught |
| outputs | `export.sh`: gerbers, drill, `pos --exclude-dnp`, `jlc.py` → BOM/CPL, PDFs, renders | |

**Python:** KiCad's bundled interpreter, `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3`. Put `…/Contents/MacOS` on PATH for `kicad-cli`. Plain `python3` is fine only for scripts that don't import pcbnew (`jlc.py`).

Commands, snippets and file formats: see `reference.md` in this directory.

## Hard-won rules

**Reproducibility**
- Footprints get fresh random UUIDs, and KiCad's iteration order follows them. Sort footprints and pads by reference and number before routing or packing, or the same board routes differently on every run.
- Freerouting's output changes with version and settings, so `build_all.sh` never routes. Run it once, freeze the tracks and vias into `routes_signals.py`, and let `gen_pcb.py` replay them.
- Use Freerouting 2.2 or later and always run it under a timeout. 2.1.0 ignores its pass limit when headless and loops for ever. v2.5.0 (Oct 2026) stops after about 18 passes once its score stalls, and skips its optimizer while anything is unrouted. On a dense 2-layer board it left 20–39 nets open on all 5 layouts, while a 2026-09 build routed every one. Route the finalists with both versions before switching. To retry, vary the settings or the net order; with the same settings a rerun proves nothing.
- Freerouting leaves some nets in pieces, and its own unrouted count undercounts them. Always follow it with an A* completion pass (`routing.complete_nets`).
- Call `SetLocked(True)` on all copper the generator draws itself: QFN escape stubs, ground vias, the coil bridge. `complete_nets` treats locked copper as fixed and unlocked copper as rippable router output. Don't tell the two apart by coordinates, because copper nudged a few microns then gets dropped.
- Dense QFN, for example 0.5 mm pitch:
  - `gen_pcb.py` draws a short locked stub straight out of each pad (`fanout()`).
  - Netclasses go into the `.kicad_pro` (`set_netclasses.py`). Freerouting reads widths and clearances from there: default 0.2/0.2 mm, vias 0.6/0.3 mm, power 0.25–0.5 mm.
  - The completion pass runs on a finer lattice (`router.GRID = 0.25`, `CLEAR = 0.22`).
  - Freerouting itself is driven by `pcbnew.ExportSpecctraDSN`, then `java -jar freerouting.jar -de x.dsn -do x.ses --gui.enabled=false`, then `ImportSpecctraSES`. Diff the board's tracks against the bare board to get only the new copper.

**Placement** (reference: `~/dev/bug-tracker/hardware/companion-board/`, `place.py`, `assign_pins.py`, `placement_metrics.py`)
- Key every placement rule by a Block/Role field set in `gen_sch.py` (`out1/rsen`), never by an auto-numbered ref. Refs renumber when the schematic changes, and a spot fixed for `D5` then gets a different part.
- With a GPIO matrix (ESP32), pins are a placement variable. Assign them after the ICs are placed: Hungarian on distance, then swaps that cut crossings. Respect input-only, strapping and flash pins, and write the map out for firmware. A map chosen first, ignoring geometry, cost one board 635 crossings.
- Place critical sub-circuits as rigid macros in vendor order before any search: buck (CIN, inductor, COUT, then small parts; no vias in the hot loop), crystal at its pins, and the module antenna at an edge or overhanging it.
- A part that serves one pin (decap, feedback divider, series R, crystal caps, CAN ESD or termination) gets its maximum distance as a **legality check**. A soft weight loses to the crossing term and parks parts 13–16 mm away. Static pull-ups need no distance rule.
- Leave poured nets (GND, plane nets) out of the length and crossing metrics. Treat rotation as a variable: sweep all four per part by cost, after construction and again after the anneal.
- Gate on signal-net MST crossings and length plus a congestion map, but these proxies only roughly predict routability. Route the top 3–5 candidates and pick by completion, then via count, then length.
- Size floor: total courtyard area ÷ 0.70 on 2 layers. Check it before searching a size. Below it, the levers are antenna overhang and 4 layers, not the placer.
- Two-sided placement only on 4 layers (on 2 it cuts the bottom ground) with JLC Standard PCBA (about $58 more per order, Oct 2026). Never the ESP32 module (single reflow) or the buck stage.
- Reserve label room beside each part that carries a silk name, sized from the measured text box plus the labeller's margin. Let the rotation pick the side. A guessed size failed twice.
- Signal integrity: on 2 layers the buck switch node plus the inductor footprint is a no-route zone on both layers for every other net. Check CAN pairs for coupling on one layer, not length: at 500 kbit/s, 10 mm of skew is about 70 ps against a 2 µs bit. Limit the mm of each line that runs apart, not the share: a share fails short pairs for their fixed fan-out. Order: connector, TVS, choke, transceiver. TJA1055 RTH/RTL resistors sit at the chip.

**After moving parts:** delete only the frozen segments and vias of the affected nets near the old positions, rebuild, then run the completion pass alone (`route_signals.py --complete`). Rebuild and check DRC for `track_dangling`, because a dead branch often survives.

**Silk**
- If a label lands off its target, look for your own post-pass first. `tidy_silk` moves any text whose box overlaps another footprint's bbox.
- Start labels clear of neighbouring outlines. Centre the text both ways, because mirrored and rotated text then anchors at its centre.
- Assert the final positions. An assumption that the text stayed where you put it isn't a check.

**Mating boards:** draw the second board in the first board's coordinates. Its outline sits at an offset, so a hole or connector pin is at the same numbers on both boards. Mirror a connector by placing it flipped on the other face with pin 1 at the partner's pin 1. `verify.py` then loads the partner board and compares the numbers directly.

**Verification:** a check that has never failed proves nothing, so mutation-test `verify.py`.
- Read-only loads in one process: give each copy a fresh path, because `LoadBoard` caches by path.
- Mutate, save, verify: put each case in its own subprocess. The test runs saw an earlier case's mutation leak into a later one even with distinct paths.

**JLCPCB**
- Economic PCBA rejects "Standard only" parts. All 5050 addressable LEDs (WS2812B, XL-5050…) and the ESP32-WROOM-32E module (Nov 2026) are Standard only. Either pay the Standard fee (≈ $25 a side plus edge rails) or fit them by hand. Economic also only takes green, purple, blue or black mask, not white, red or yellow.
- Mark hand-fit parts and THT headers with LCSC `skip` in `jlc_parts.csv` so they drop out of both BOM and CPL.
- Look up stock before freezing a value. E96 power resistors and 100 V C0G caps in odd values are often out of stock. Move to the nearest stocked value, **re-run the design calculation** with it, and leave DNP trim pads.
- Add **F.Paste** to the fab gerber set whenever JLC assembles the board, and B.Paste only if the bottom side is assembled too. JLC cuts the stencil from it, and a QFN's segmented centre-pad paste exists only there. A set exported for hand-built prototypes (no stencil) silently lacks it. Check with `unzip -l` for `*_Paste.gtp`.
- The CPL needs JLC's rotations, not KiCad's: `kicadgen/jlc.py` adds a fix per footprint (SOIC/TSSOP 270, SOT-23 180) and per part (`Rot` column in `jlc_parts.csv`), because JLC's models differ by part even in one package (two SOT-23-5 parts on one board needed 180 and 270). JLC centres its model on the pads, so a footprint whose origin isn't there (ESP32 module, USB-C) gets moved in the CPL. Check every part in the preview's 2D view: the magenta dot is the model's pin 1, compare it with the silk pin-1 mark.
- JLC re-matches the BOM on every re-upload and can swap your LCSC number for a near-empty listing with the same MPN (CH340C). Re-check each line's part number and stock after every upload.

**13.56 MHz antennas**
- Keep copper away from the coil on **every board in the stack**. A GND pour on the board behind the reader cost about 21 % of L.
- Copper art inside the coil must have every closed loop cut, or it becomes a shorted turn. Finish each cut end in a small via ring, narrower than the stroke.
- With no 13.56 MHz VNA, tune by model. Target windows were |Z| 17–21 Ω and |X| ≤ 3 Ω, then check TVDD current at 3.3 V and 5 V. Keep DNP trim pads so you can correct it on the bench.

**ESP32 next to the reader**
- Use an LDO (1 A class, low dropout), not a buck, because a switcher's harmonics land near 13.56 MHz. Avoid the AMS1117, whose dropout is too high from 5 V USB.
- Put the module antenna at a board edge with no copper under it on any layer, pointing away from the coil.
- WS2812 data from a 3.3 V GPIO only works on a 5 V rail by luck. Buffer it with a 74AHCT1G125.

## Common mistakes

| Mistake | Fix |
|---|---|
| Editing the `.kicad_pcb` in the GUI | Change the generator and rebuild |
| Re-running Freerouting in the build | Freeze it in `routes_signals.py` |
| Trusting Freerouting's "0 unrouted" | Run the completion pass, then DRC with `--schematic-parity` |
| Blaming KiCad for misplaced silk | Check `tidy_silk` and bbox overlaps first |
| Exiting 0 on violations | `--exit-code-violations` on ERC and DRC |
| BOM and CPL listing different parts | Generate both from `pos --exclude-dnp` via `jlc.py` |
| Logo or pour inside the antenna on the mating board | Keep it out of the coil on every layer and every board |
| Calling it done without looking | `kicad-cli pcb render --use-board-stackup-colors`, then look at both sides |
| Fixing placement by auto-numbered ref | Key it by Block/Role |
| Choosing MCU pins before placement | Solve them from geometry |
| A soft weight for a maximum-distance rule | Make it a legality check |
| Picking a placement by ratsnest score alone | Route the finalists |
| Running Freerouting 2.1.0 or without a timeout | 2.2+ under `timeout` |
| Length-matching slow CAN | Check coupling |
