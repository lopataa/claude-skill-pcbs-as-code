"""Shared schematic builder for the generated KiCad projects.

Every board's gen_sch.py builds on it.
Call init(project) before placing anything: the project name seeds every
UUID and names the symbol instances, so a rebuild gives the same UUIDs.
"""
import hashlib
import re
import sys
from pathlib import Path

KICAD = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")
SYMDIR = KICAD / "symbols"
PROJECT = None
ROOT_UUID = None
LIB = None
EXTRA_SYMDIRS = []


# ---------------------------------------------------------------- lib parsing


def sexp_block(text, start):
    """Return the balanced-paren block beginning at text[start] == '('."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("unbalanced")


class Lib:
    def __init__(self):
        self.cache = {}

    def get(self, lib_id):
        if lib_id in self.cache:
            return self.cache[lib_id]
        lib, name = lib_id.split(":")
        path = SYMDIR / f"{lib}.kicad_sym"
        for d in EXTRA_SYMDIRS:          # a project's own library wins
            if (d / f"{lib}.kicad_sym").exists():
                path = d / f"{lib}.kicad_sym"
        text = path.read_text()
        m = re.search(r'^\t\(symbol "%s"' % re.escape(name), text, re.M)
        if not m:
            raise SystemExit(f"symbol {lib_id} not found")
        block = sexp_block(text, m.start() + 1)
        if '(extends "' in block.split("(symbol", 2)[0] + block[:400]:
            ext = re.search(r'\(extends "([^"]+)"', block)
            if ext:
                raise SystemExit(f"{lib_id} extends {ext.group(1)}: use the base symbol")
        pins = {}
        for pm in re.finditer(r'\(pin \w+ \w+\s*\n\s*\(at ([-\d.]+) ([-\d.]+) (\d+)\)', block):
            sub = sexp_block(block, pm.start())
            num = re.search(r'\(number "([^"]+)"', sub)
            nam = re.search(r'\(name "([^"]*)"', sub)
            x, y = float(pm.group(1)), float(pm.group(2))
            if num:
                pins[num.group(1)] = (x, y)
            if nam and nam.group(1) not in ("~", ""):
                pins.setdefault(nam.group(1), (x, y))
        # the schematic embeds the definition under its full "Lib:Name" id
        body = re.sub(r'^\t\(symbol "%s"' % re.escape(name),
                      '\t\t(symbol "%s:%s"' % (lib, name), block, count=1, flags=re.M)
        body = "\n".join(("\t" + ln) if ln.startswith("\t\t") else ln
                         for ln in body.split("\n")[1:])
        self.cache[lib_id] = (pins, '\t\t(symbol "%s:%s"\n' % (lib, name) + body)
        return self.cache[lib_id]



# ------------------------------------------------------------------- builder

_n = [0]


def uid(tag=""):
    _n[0] += 1
    h = hashlib.md5(f"{PROJECT}/{tag}/{_n[0]}".encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def snap(v):
    return round(v, 4)


class Sym:
    """A placed symbol; .p(pin) gives the absolute sheet coords of a pin."""

    def __init__(self, lib_id, ref, value, x, y, rot=0, mirror=False,
                 footprint="", dnp=False, in_bom=True, hide_value=False,
                 hide_ref=False, ref_off=(0, -5.08), val_off=(0, 5.08)):
        self.lib_id, self.ref, self.value = lib_id, ref, value
        self.x, self.y, self.rot, self.mirror = x, y, rot, mirror
        self.footprint, self.dnp, self.in_bom = footprint, dnp, in_bom
        self.hide_value, self.hide_ref = hide_value, hide_ref
        self.ref_off, self.val_off = ref_off, val_off
        self.pins, self._def = LIB.get(lib_id)
        self.uuid = uid(ref)

    def p(self, pin):
        pin = str(pin)
        if pin not in self.pins:
            raise SystemExit(f"{self.ref} ({self.lib_id}) has no pin {pin!r}; "
                             f"have {sorted(self.pins)}")
        px, py = self.pins[pin]
        if self.mirror:
            px = -px
        r = self.rot % 360
        if r == 0:
            dx, dy = px, -py
        elif r == 90:
            dx, dy = -py, -px
        elif r == 180:
            dx, dy = -px, py
        elif r == 270:
            dx, dy = py, px
        else:
            raise SystemExit("rotation must be a multiple of 90")
        return (snap(self.x + dx), snap(self.y + dy))

    def render(self):
        props = [("Reference", self.ref, self.ref_off, self.hide_ref),
                 ("Value", self.value, self.val_off, self.hide_value),
                 ("Footprint", self.footprint, (0, 0), True),
                 ("Datasheet", "~", (0, 0), True),
                 ("Description", "", (0, 0), True)]
        out = ["\t(symbol",
               f'\t\t(lib_id "{self.lib_id}")',
               f"\t\t(at {self.x} {self.y} {self.rot})"]
        if self.mirror:
            out.append("\t\t(mirror y)")
        out += ["\t\t(unit 1)",
                "\t\t(exclude_from_sim no)",
                f"\t\t(in_bom {'yes' if self.in_bom else 'no'})",
                "\t\t(on_board yes)",
                f"\t\t(dnp {'yes' if self.dnp else 'no'})",
                f'\t\t(uuid "{self.uuid}")']
        for name, val, off, hide in props:
            if not hide and val:
                TEXTS.append((val, snap(self.x + off[0]), snap(self.y + off[1]),
                              1.27, "center", f"{self.ref}.{name}"))
            out += [f'\t\t(property "{name}" "{val}"',
                    f"\t\t\t(at {snap(self.x + off[0])} {snap(self.y + off[1])}"
                    f" {0 if self.rot in (0, 180) else (360 - self.rot) % 360})",
                    "\t\t\t(effects",
                    "\t\t\t\t(font",
                    "\t\t\t\t\t(size 1.27 1.27)",
                    "\t\t\t\t)"]
            if hide:
                out.append("\t\t\t\t(hide yes)")
            out += ["\t\t\t)", "\t\t)"]
        for pin in sorted({k for k in self.pins if k.isdigit()}, key=int):
            out += [f'\t\t(pin "{pin}"', f'\t\t\t(uuid "{uid(self.ref + pin)}")', "\t\t)"]
        out += ["\t\t(instances",
                f'\t\t\t(project "{PROJECT}"',
                f'\t\t\t\t(path "/{ROOT_UUID}"',
                f'\t\t\t\t\t(reference "{self.ref}")',
                "\t\t\t\t\t(unit 1)", "\t\t\t\t)", "\t\t\t)", "\t\t)", "\t)"]
        return "\n".join(out)


items, symbols, TEXTS, WIRES = [], [], [], []


def place(*a, **kw):
    s = Sym(*a, **kw)
    symbols.append(s)
    return s


def wire(a, b):
    a, b = (snap(a[0]), snap(a[1])), (snap(b[0]), snap(b[1]))
    if a == b:
        return
    if a[0] != b[0] and a[1] != b[1]:
        raise SystemExit(f"diagonal wire {a}->{b}")
    WIRES.append((a, b))
    items.append("\n".join([
        "\t(wire", "\t\t(pts", f"\t\t\t(xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})", "\t\t)",
        "\t\t(stroke", "\t\t\t(width 0)", "\t\t\t(type default)", "\t\t)",
        f'\t\t(uuid "{uid("w")}")', "\t)"]))


def route(a, b, first="h"):
    """L-shaped route between two points."""
    if a[0] == b[0] or a[1] == b[1]:
        wire(a, b)
        return
    mid = (b[0], a[1]) if first == "h" else (a[0], b[1])
    wire(a, mid)
    wire(mid, b)


def junction(p):
    items.append("\n".join([
        "\t(junction", f"\t\t(at {snap(p[0])} {snap(p[1])})", "\t\t(diameter 0)",
        "\t\t(color 0 0 0 0)", f'\t\t(uuid "{uid("j")}")', "\t)"]))


def label(text, p, rot=0, justify="left bottom"):
    just = "right" if "right" in justify else "left"
    if rot in (90, 270):        # vertical label: the audit must size it vertically
        # KiCad keeps vertical text readable, so the side it grows to is set
        # by the justification: "left" grows up from the anchor, "right" down
        just = "vup" if just == "left" else "vdown"
    TEXTS.append((text, snap(p[0]), snap(p[1]), 1.27, just, f"label:{text}"))
    items.append("\n".join([
        f'\t(label "{text}"', f"\t\t(at {snap(p[0])} {snap(p[1])} {rot})",
        "\t\t(fields_autoplaced yes)", "\t\t(effects", "\t\t\t(font",
        "\t\t\t\t(size 1.27 1.27)", "\t\t\t)", f"\t\t\t(justify {justify})",
        "\t\t)", f'\t\t(uuid "{uid("l")}")', "\t)"]))


def nc(p):
    items.append("\n".join([
        "\t(no_connect", f"\t\t(at {snap(p[0])} {snap(p[1])})",
        f'\t\t(uuid "{uid("nc")}")', "\t)"]))


def rect(a, b, color):
    r, g, bl = color
    items.append("\n".join([
        "\t(rectangle", f"\t\t(start {a[0]} {a[1]})", f"\t\t(end {b[0]} {b[1]})",
        "\t\t(stroke", "\t\t\t(width 0.3)", "\t\t\t(type dash)",
        f"\t\t\t(color {r} {g} {bl} 1)", "\t\t)",
        "\t\t(fill", "\t\t\t(type none)", "\t\t)",
        f'\t\t(uuid "{uid("r")}")', "\t)"]))


def esc(s):
    """KiCad quotes strings with escaped newlines, not literal line breaks."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def text(s, p, size=1.27, bold=False, color=None, justify="left"):
    for i, ln in enumerate(s.split("\n")):
        TEXTS.append((ln, p[0], p[1] + i * size * 1.7, size,
                      "right" if "right" in justify else "left",
                      "note:" + s.split("\n")[0][:18]))
    out = [f'\t(text "{esc(s)}"', "\t\t(exclude_from_sim no)",
           f"\t\t(at {snap(p[0])} {snap(p[1])} 0)", "\t\t(effects", "\t\t\t(font",
           f"\t\t\t\t(size {size} {size})"]
    if bold:
        out.append("\t\t\t\t(bold yes)")
    if color:
        out.append("\t\t\t\t(color %d %d %d 1)" % color)
    out += ["\t\t\t)", f"\t\t\t(justify {justify} top)", "\t\t)",
            f'\t\t(uuid "{uid("t")}")', "\t)"]
    items.append("\n".join(out))


_pwr = [0]


def gnd(p, rot=0, val_off=(0, 3.81)):
    """GND symbol whose pin sits exactly on p."""
    _pwr[0] += 1
    return place("power:GND", "#PWR%02d" % _pwr[0], "GND", p[0], p[1], rot=rot,
                 footprint="", in_bom=False, hide_ref=True,
                 ref_off=(0, 0), val_off=val_off)


def rail(name, p, rot=0, val_off=(0, -3.81)):
    _pwr[0] += 1
    return place(f"power:{name}", "#PWR%02d" % _pwr[0], name, p[0], p[1], rot=rot,
                 footprint="", in_bom=False, hide_ref=True,
                 ref_off=(0, 0), val_off=val_off)


def pwr_flag(p, rot=0):
    _pwr[0] += 1
    return place("power:PWR_FLAG", "#FLG%02d" % _pwr[0], "PWR_FLAG", p[0], p[1],
                 rot=rot, in_bom=False, hide_ref=True, hide_value=True,
                 ref_off=(0, 0), val_off=(0, 0))


def _bbox(t):
    txt, x, y, size, just, _ = t
    w = 0.85 * size * len(txt)
    h = size * 1.15
    if just == "vup":
        return (x - h * 0.8, y - w, x + h * 0.3, y)
    if just == "vdown":
        return (x - h * 0.8, y, x + h * 0.3, y + w)
    if just == "right":
        x0 = x - w
    elif just == "center":
        x0 = x - w / 2
    else:
        x0 = x
    return (x0, y - h * 0.8, x0 + w, y + h * 0.3)


def _ov(a, b, pad=0.3):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0]
                or a[3] + pad < b[1] or b[3] + pad < a[1])


def audit():
    """Report text that collides with other text or sits on a wire."""
    bad = 0
    boxes = [(_bbox(t), t) for t in TEXTS]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (ba, ta), (bb, tb) = boxes[i], boxes[j]
            if ta[5] == tb[5]:
                continue
            if _ov(ba, bb):
                print(f"  TEXT/TEXT  {ta[0]!r} @({ta[1]},{ta[2]}) [{ta[5]}]"
                      f"  <->  {tb[0]!r} @({tb[1]},{tb[2]}) [{tb[5]}]")
                bad += 1
    for box, t in boxes:
        anchor = (t[1], t[2])
        for (a, b) in WIRES:
            # a label sitting on its own wire is the connection, not a clash
            on_wire = ((a[1] == b[1] == anchor[1]
                        and min(a[0], b[0]) <= anchor[0] <= max(a[0], b[0]))
                       or (a[0] == b[0] == anchor[0]
                           and min(a[1], b[1]) <= anchor[1] <= max(a[1], b[1])))
            if t[5].startswith("label:") and on_wire:
                continue
            if a == anchor or b == anchor:
                continue
            if a[1] == b[1]:  # horizontal
                y = a[1]
                x0, x1 = sorted((a[0], b[0]))
                if box[1] < y < box[3] and not (box[2] < x0 or x1 < box[0]):
                    print(f"  TEXT/WIRE  {t[0]!r} @({t[1]},{t[2]}) [{t[5]}] "
                          f"over h-wire y={y} x={x0}..{x1}")
                    bad += 1
            else:  # vertical
                x = a[0]
                y0, y1 = sorted((a[1], b[1]))
                if box[0] < x < box[2] and not (box[3] < y0 or y1 < box[1]):
                    print(f"  TEXT/WIRE  {t[0]!r} @({t[1]},{t[2]}) [{t[5]}] "
                          f"over v-wire x={x} y={y0}..{y1}")
                    bad += 1
    print(f"audit: {bad} collision(s)")
    return bad


def init(project, extra_symdirs=()):
    """Start a schematic: set the project, the symbol search path and the
    root UUID (always the first UUID drawn, as in the original script)."""
    global PROJECT, ROOT_UUID, LIB
    PROJECT = project
    EXTRA_SYMDIRS[:] = [Path(d) for d in extra_symdirs]
    LIB = Lib()
    ROOT_UUID = uid("root")


def emit_schematic(out, title, date, rev, company, comments):
    """Write the .kicad_sch from the symbols and items placed so far."""
    lib_defs = []
    for sym in symbols:
        if sym._def not in lib_defs:
            lib_defs.append(sym._def)
    head = [
        "(kicad_sch",
        "\t(version 20251024)",
        '\t(generator "eeschema")',
        '\t(generator_version "10.0")',
        f'\t(uuid "{ROOT_UUID}")',
        '\t(paper "A3")',
        "\t(title_block",
        f'\t\t(title "{title}")',
        f'\t\t(date "{date}")',
        f'\t\t(rev "{rev}")',
        f'\t\t(company "{company}")',
    ] + [f'\t\t(comment {i} "{c}")' for i, c in enumerate(comments, 1)] + [
        "\t)",
        "\t(lib_symbols",
    ]
    body = ["\t)"] + items + [s.render() for s in symbols]
    tail = [
        "\t(sheet_instances",
        '\t\t(path "/"',
        '\t\t\t(page "1")',
        "\t\t)",
        "\t)",
        "\t(embedded_fonts no)",
        ")",
    ]
    Path(out).write_text("\n".join(head + lib_defs + body + tail) + "\n")
    print(f"wrote {out} ({len(symbols)} symbols, {len(items)} graphic/net items)")
    return audit()
