"""JLCPCB assembly files from a board's KiCad placement export.

    python3 ../kicadgen/jlc.py build/positions.csv build/ [jlc_parts.csv]

Writes <out>/jlc_bom.csv (Comment, Designator, Footprint, JLCPCB Part #)
and <out>/jlc_cpl.csv (Designator, Mid X, Mid Y, Layer, Rotation), in the
layout of JLC's own samples (Sample-BOM_JLCSMT / JLCSMT_Sample_CPL). Both
come from positions.csv, the parts that are actually placed
(kicad-cli pos --exclude-dnp), so the two files always list the same
designators. (KiCad's own BOM marks references like "C0A" as unannotated.)

A parts table (CSV: Value, Comment, LCSC) overrides the Comment with a
manufacturer part number for the parts that matter (the ones the design
calculation picked) and fills JLC part numbers where known; JLC's BOM tool
matches the rest by value and footprint. LCSC "skip" leaves a part out of both
files (fitted by hand, e.g. a header on the bottom face). Mid Y keeps KiCad's sign, the same
frame as the gerbers.
Rotations are KiCad's; JLC's preview shows any package whose zero
orientation differs (SOT-23, QFN, LEDs are the usual ones) and fixes it there.
"""
import csv
import os
import re
import sys

# KiCad footprint name -> the package name JLC's tool knows
PACKAGES = [
    (r'^R_0603_', 'R0603'), (r'^C_0603_', 'C0603'), (r'^R_0805_', 'R0805'), (r'^C_0805_', 'C0805'),
    (r'^R_2512_', 'R2512'), (r'^L_1008_', 'L1008'), (r'^SOT-23-6', 'SOT-23-6'), (r'^SOT-23', 'SOT-23-3'),
    (r'^HVQFN-32', 'HVQFN-32_L5.0-W5.0-P0.50'), (r'^Crystal_SMD_3225', 'SMD3225-4P'),
    (r'^LED_WS2812B', 'LED-SMD_4P-L5.0-W5.0'), (r'^D_SOD-123', 'SOD-123'), (r'^D_SMA', 'SMA'),
    (r'^CP_Elec_6.3x5.4', 'CAP-SMD_D6.3-L5.4'),
    (r'^PinHeader_1x(\d+)_P2.54mm_Vertical', lambda m: 'HDR-TH_%dP-P2.54-V' % int(m.group(1))),
]


def package(fp):
    for pat, name in PACKAGES:
        m = re.match(pat, fp)
        if m:
            return name(m) if callable(name) else name
    return fp


def main():
    pos, out = sys.argv[1], sys.argv[2]
    mpn = {}
    if len(sys.argv) > 3 and os.path.exists(sys.argv[3]):
        with open(sys.argv[3], newline='') as f:
            for row in csv.DictReader(f):
                mpn[row['Value']] = (row.get('Comment', ''), row.get('LCSC', ''))
    with open(pos, newline='') as f:
        rows = [r for r in csv.DictReader(f) if mpn.get(r['Val'], ('', ''))[1] != 'skip']
    groups = {}
    for r in rows:
        groups.setdefault((r['Val'], r['Package']), []).append(r['Ref'])
    with open(os.path.join(out, 'jlc_bom.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Comment', 'Designator', 'Footprint', 'JLCPCB Part #（optional）'])
        for (val, fp), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            comment, lcsc = mpn.get(val, ('', ''))
            w.writerow([comment or val, ','.join(sorted(refs)), package(fp), lcsc])
    with open(os.path.join(out, 'jlc_cpl.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        for r in rows:
            w.writerow([r['Ref'], '%.4fmm' % float(r['PosX']), '%.4fmm' % float(r['PosY']),
                        'Top' if r['Side'] == 'top' else 'Bottom', '%g' % float(r['Rot'])])
    print('jlc: %d parts in %d BOM lines -> %s' % (len(rows), len(groups), out))


if __name__ == '__main__':
    main()
