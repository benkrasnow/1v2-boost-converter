"""
Emits 1v2boost.kicad_pcb - a 2-layer, 2 oz board, 150 x 100 mm.

Floorplan intent
----------------
Current flows left to right along the top: cell terminal -> input bulk ->
L1 -> switch leg -> rectifier -> output bulk -> output terminal. Small-signal
parts sit along the bottom, well away from the switch node.

The two things that actually matter at 28 A are copper area and loop area,
so the board is built around zones rather than tracks:

  F.Cu   VCELL / SW / VOUT islands, plus a quiet AGND island under U1
  B.Cu   solid PGND pour across the whole board, stitched with vias

Every PGND pad on the top side gets its own via straight down into that pour
rather than routing along the top layer.

The commutation loop that must stay small is
  Q drain (SW) -> D1 -> C13..C18 -> PGND pour -> R2 -> Q source
so D1, the output ceramics, the shunt and the FETs are clustered together.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import footprints                  # noqa: E402
import kicad_netlist               # noqa: E402
import netlist                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOARD_W, BOARD_H = 150.0, 100.0
EDGE = 0.0                          # outline starts at origin

# Track widths by net class
W_SIG = 0.25
W_PWR = 3.0
VIA_D, VIA_DRILL = 1.2, 0.6
VIA_SIG_D, VIA_SIG_DRILL = 0.8, 0.4


def uid(*parts):
    h = hashlib.sha1(("pcb|" + "|".join(str(p) for p in parts)).encode()).hexdigest()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def n(v):
    s = "%.4f" % float(v)
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


# --------------------------------------------------------------------------
# Placement:  ref -> (x, y, rotation)
# --------------------------------------------------------------------------

PLACE = {
    # --- cell input, bulk, damping (left) ---------------------------------
    "J1": (16.0, 30.0, 0),          # + at (16,20), - at (16,40)
    # Main input bank, two rows marching toward L1's input pad.
    "C1": (46.0, 12.0, 0), "C2": (52.0, 12.0, 0), "C3": (58.0, 12.0, 0),
    "C4": (46.0, 20.0, 0), "C5": (52.0, 20.0, 0), "C6": (58.0, 20.0, 0),
    # Damping leg, grouped with R1 so the R-C loop stays short and the leg is
    # visibly a unit rather than three caps scattered through the bank.
    "C7": (34.0, 16.0, 0),
    "C8": (34.0, 30.0, 0),
    "C9": (34.0, 44.0, 0),
    "R1": (44.0, 44.0, 0),

    # --- inductor ---------------------------------------------------------
    # PG1083: body x 75.15..96.85, y 7.75..29.25; winding pads at y = 27,
    # VCELL on the left at x = 78.75 and SW on the right at x = 93.25.
    #
    # Pushed up and left of where the (larger) Coilcraft part sat, for three
    # reasons. The SW pad has to clear the VCELL zone's x = 90 edge or the
    # filler carves the pour back under the switch node. The body has to stay
    # above Q1/Q2's courtyard, which starts at y = 46.2. And the y = 4 row of
    # stitching vias has to stay outside the courtyard (top edge y = 6.9),
    # because P716 note 10 forbids an exposed via under the conductive core.
    "L1": (86.0, 18.5, 0),

    # --- switch leg -------------------------------------------------------
    "Q1": (78.0, 52.0, 0),          # pads y=52, body above
    "Q2": (91.0, 52.0, 0),
    "R2": (85.0, 60.0, 0),          # shunt: SRC left, PGND right

    # --- snubber, kept right at the switch node ---------------------------
    "R3": (106.0, 37.0, 0),
    "C12": (113.0, 37.0, 0),

    # --- rectifier and output --------------------------------------------
    "D1": (112.0, 52.0, 0),
    "C13": (106.0, 62.0, 0), "C14": (112.0, 62.0, 0), "C15": (118.0, 62.0, 0),
    "C16": (106.0, 68.0, 0), "C17": (112.0, 68.0, 0), "C18": (118.0, 68.0, 0),
    "C19": (106.0, 74.0, 0), "C20": (112.0, 74.0, 0),
    "J2": (138.0, 60.0, 0),         # + at (138,54.5), - at (138,65.5)

    # --- gate driver ------------------------------------------------------
    "U2": (72.0, 70.0, 0),
    "R4": (78.0, 66.0, 0),
    "R5": (78.0, 70.0, 0),
    "R6": (78.0, 74.0, 0),
    "C22": (66.0, 74.0, 0),
    "C23": (66.0, 66.0, 0),

    # --- controller -------------------------------------------------------
    "U1": (40.0, 74.0, 0),
    "R7": (30.0, 64.0, 0),
    "C24": (30.0, 68.0, 0),
    "C25": (30.0, 72.0, 0),
    "C26": (52.0, 68.0, 0),
    "R8": (46.0, 86.0, 0),
    "R9": (52.0, 86.0, 0),
    "R10": (34.0, 86.0, 0),
    "C27": (40.0, 86.0, 0),
    "C28": (40.0, 90.0, 0),
    "R11": (58.0, 78.0, 0),
    "C29": (58.0, 82.0, 0),
    "C31": (52.0, 64.0, 0),

    # --- bias input -------------------------------------------------------
    "J3": (10.0, 62.0, 0),
    "D3": (18.0, 62.0, 0),
    "C30": (25.0, 62.0, 0),

    # --- cell undervoltage lockout ---------------------------------------
    "R12": (12.0, 72.0, 0),
    "D4": (12.0, 78.0, 0),
    "C32": (12.0, 84.0, 0),
    "U3": (22.0, 88.0, 0),
    "R13": (12.0, 90.0, 0),
    "R14": (12.0, 94.0, 0),
    "R15": (30.0, 94.0, 0),
    "R16": (22.0, 94.0, 0),
    "R17": (18.0, 84.0, 0),
    "C33": (24.0, 84.0, 0),
    "C34": (18.0, 94.0, 0),
    "JP1": (30.0, 90.0, 0),

    # --- test points ------------------------------------------------------
    "TP1": (100.0, 44.0, 0),        # SW
    "TP2": (62.0, 82.0, 0),         # ISNS
    "TP3": (34.0, 90.0, 0),         # COMP
    "TP4": (56.0, 90.0, 0),         # FB
    "TP5": (84.0, 70.0, 0),         # GATE
    "TP6": (26.0, 8.0, 0),          # VCELL
    "TP7": (128.0, 50.0, 0),        # VOUT
    "TP8": (100.0, 76.0, 0),        # PGND
    "TP9": (36.0, 94.0, 0),         # EN
    "TP10": (56.0, 64.0, 0),        # BP
    "TP11": (62.0, 86.0, 0),        # AGND
    "TP12": (28.0, 58.0, 0),        # V12

    # --- mechanical -------------------------------------------------------
    "H1": (5.0, 5.0, 0), "H2": (145.0, 5.0, 0),
    "H3": (5.0, 95.0, 0), "H4": (145.0, 95.0, 0),
    "FID1": (5.0, 50.0, 0), "FID2": (145.0, 50.0, 0),
}

# F.Cu zones. KiCad automatically keeps clearance around pads and tracks of
# other nets, so these can be simple shapes; only the outline matters.
#
# There is deliberately NO zone for SW. A large switch-node pour is an
# efficient radiator at 100 kHz with 30 A edges, so SW is routed as compact
# wide track instead (see power_routes).
ZONES_F = {
    "VCELL": [(5, 5), (90, 5), (90, 34), (56, 34), (56, 48), (5, 48)],
    "VOUT": [(118, 46), (146, 46), (146, 84), (102, 84), (102, 58), (118, 58)],
    "AGND": [(7, 60), (64, 60), (64, 98), (7, 98)],
}
ZONE_B_PGND = [(3, 3), (147, 3), (147, 97), (3, 97)]


# --------------------------------------------------------------------------
# Footprint geometry, read back from the generated library
# --------------------------------------------------------------------------

PAD_RE = re.compile(
    r'\(pad "([^"]*)" (\w+) (\w+)\s*\n\s*\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)'
    r'\s*\n\s*\(size ([-\d.]+) ([-\d.]+)\)', re.M)
CRTYD_RE = re.compile(
    r'\(fp_line \(start ([-\d.]+) ([-\d.]+)\) \(end ([-\d.]+) ([-\d.]+)\)'
    r'\s*\n\s*\(stroke \(width [\d.]+\) \(type solid\)\)\s*\n\s*\(layer "F\.CrtYd"\)')


def load_footprints():
    out = {}
    d = os.path.join(ROOT, "lib", "1v2boost.pretty")
    for fn in os.listdir(d):
        if not fn.endswith(".kicad_mod"):
            continue
        name = fn[:-len(".kicad_mod")]
        text = open(os.path.join(d, fn), encoding="utf-8").read()
        pads = []
        for m in PAD_RE.finditer(text):
            pads.append(dict(num=m.group(1), ptype=m.group(2),
                             shape=m.group(3),
                             x=float(m.group(4)), y=float(m.group(5)),
                             sx=float(m.group(7)), sy=float(m.group(8))))
        xs, ys = [], []
        for m in CRTYD_RE.finditer(text):
            xs += [float(m.group(1)), float(m.group(3))]
            ys += [float(m.group(2)), float(m.group(4))]
        crtyd = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)
        out[name] = dict(text=text, pads=pads, crtyd=crtyd)
    return out


FP = load_footprints()


def xform(px, py, rot):
    """Footprint-local to board coordinates for a placement rotation."""
    r = math.radians(rot)
    return (px * math.cos(r) - py * math.sin(r),
            px * math.sin(r) + py * math.cos(r))


def pad_pos(ref, pad_num, expect_net=None):
    """Board position of a pad.

    `expect_net` guards against the easiest mistake in a hand-written routing
    table: naming the right component but the wrong pin, which silently drags
    a track across a neighbouring pad and shorts it.

    Where a footprint builds one terminal out of several same-numbered
    rectangles, the FIRST one in the library file wins. That is not arbitrary:
    R2's sense fingers are emitted tip-first precisely so a route attaches at
    the tip, out past the body, rather than at the neck buried under it.
    """
    comp = COMPS[ref]
    x, y, rot = PLACE[ref]
    if expect_net is not None:
        actual = comp.nets.get(pad_num)
        if actual != expect_net:
            raise AssertionError(
                "route claims %s pad %s is on net %r, but netlist.py says %r"
                % (ref, pad_num, expect_net, actual))
    for p in FP[comp.footprint]["pads"]:
        if p["num"] == pad_num:
            dx, dy = xform(p["x"], p["y"], rot)
            return (x + dx, y + dy)
    raise KeyError("%s pad %s" % (ref, pad_num))


COMPS = {c.ref: c for c in netlist.C}


# --------------------------------------------------------------------------
# Net numbering
# --------------------------------------------------------------------------

NETS = ["", ] + sorted(netlist.all_nets())
NET_ID = {name: i for i, name in enumerate(NETS)}

# KiCad's own view of the schematic, exported by kicad-cli. This supplies the
# net names and the component metadata that DRC's schematic-parity check
# compares, neither of which can be derived from netlist.py alone: KiCad
# sheet-qualifies local labels ("DAMP" -> "/DAMP") but leaves power and global
# labels bare, and it expects footprints to carry the symbol's fields and
# dnp / exclude_from_bom flags.
KI = kicad_netlist.load()
KI_NET = KI.bare_to_kicad()
KI_NET[""] = ""


def net_str(bare):
    """Our internal net name rendered the way KiCad names it."""
    return KI_NET[bare]


# --------------------------------------------------------------------------
# Emitters
# --------------------------------------------------------------------------

def emit_footprint(comp):
    x, y, rot = PLACE[comp.ref]
    src = FP[comp.footprint]["text"]
    body = src.split("\n", 1)[1]
    body = body.rsplit(")", 1)[0]

    # Strip the library header lines we replace. Anchor to exactly two spaces
    # of indent so this only ever hits top-level header tokens, and use \b so
    # "layer" cannot match a pad's "(layers ...)". Without both guards this
    # silently deletes the layer assignment from every pad and graphic in the
    # board, leaving KiCad to default them onto F.Cu.
    body = re.sub(r'^  \((version|generator|generator_version|layer|descr|tags|attr)\b[^\n]*\n',
                  "", body, flags=re.M)
    # placement-relative property positions
    body = body.replace('(property "Reference" "REF**"',
                        '(property "Reference" "%s"' % comp.ref)
    body = re.sub(r'\(property "Value" "[^"]*"',
                  '(property "Value" "%s"' % comp.value, body, count=1)
    body = re.sub(r'\(property "Footprint" ""',
                  '(property "Footprint" "1v2boost:%s"' % comp.footprint,
                  body, count=1)

    # attach nets to pads
    def add_net(m):
        pad_num = m.group(1)
        net = comp.nets.get(pad_num)
        if net is None:
            return m.group(0)
        return '%s\n    (net %d "%s")' % (m.group(0), NET_ID[net],
                                          net_str(net))

    # The body is copied verbatim from the library, so its uuids would repeat
    # on every instance of the same footprint. Re-key them per reference.
    counter = [0]

    def fresh(_m):
        counter[0] += 1
        return '(uuid "%s")' % uid("fpitem", comp.ref, counter[0])

    body = re.sub(r'\(uuid "[0-9a-f\-]+"\)', fresh, body)

    # insert the net just before the closing paren of each pad block
    out_lines = []
    cur_pad = None
    for line in body.split("\n"):
        m = re.match(r'\s*\(pad "([^"]*)" ', line)
        if m:
            cur_pad = m.group(1)
        if cur_pad is not None and re.match(r'\s*\)\s*$', line):
            net = comp.nets.get(cur_pad)
            if net:
                out_lines.append('    (net %d "%s")'
                                 % (NET_ID[net], net_str(net)))
            cur_pad = None
        out_lines.append(line)
    body = "\n".join(out_lines)
    # re-indent one level
    body = "\n".join(("  " + l) if l.strip() else l for l in body.split("\n"))

    # Carry the symbol's fields onto the footprint. DRC's schematic-parity
    # check compares these, so anything KiCad exported for this reference has
    # to land here or it reports a field mismatch per component.
    ki = KI.comps.get(comp.ref, {})
    for fname, fval in sorted(ki.get("fields", {}).items()):
        if fname in ("Reference", "Value", "Footprint"):
            continue        # already emitted from the library body
        body += ('\n    (property "%s" "%s"\n'
                 '      (at 0 0 %d)\n'
                 '      (unlocked yes)\n'
                 '      (layer "F.Fab")\n'
                 '      (hide yes)\n'
                 '      (uuid "%s")\n'
                 '      (effects (font (size 1 1) (thickness 0.15)))\n'
                 '    )'
                 % (fname, fval.replace('"', "'"), rot,
                    uid("fprop", comp.ref, fname)))

    attr = FP[comp.footprint]["text"]
    m = re.search(r'^\s*\(attr ([^\)]*)\)', attr, re.M)
    flags = m.group(1).split() if m else []
    # dnp / exclude_from_bom live on the symbol, not the footprint library, and
    # DRC compares them between the two.
    if comp.dnp and "dnp" not in flags:
        flags.append("dnp")
    if not comp.in_bom and "exclude_from_bom" not in flags:
        flags.append("exclude_from_bom")
    attr_line = "  (attr %s)\n" % " ".join(flags) if flags else ""

    # (path) is how KiCad matches this footprint to its schematic symbol.
    # Without it "Update PCB from Schematic" sees an orphan and proposes to
    # delete and re-place the part, losing its position. Taken from KiCad's
    # own netlist export so it always matches what the updater looks for.
    link = ""
    if ki.get("path"):
        link = '    (path "%s")\n' % ki["path"]
        if ki.get("sheetfile"):
            link += '    (sheetfile "%s")\n' % ki["sheetfile"]

    head = ('  (footprint "1v2boost:%s"\n'
            '    (layer "F.Cu")\n'
            '    (uuid "%s")\n'
            '    (at %s %s %d)\n'
            '    (descr "%s")\n'
            '%s%s'
            % (comp.footprint, uid("fp", comp.ref), n(x), n(y), rot,
               comp.descr.replace('"', "'"), attr_line, link))
    return head + body + "\n  )"


def emit_segment(x1, y1, x2, y2, width, layer, net, tag):
    return ('  (segment\n'
            '    (start %s %s)\n'
            '    (end %s %s)\n'
            '    (width %s)\n'
            '    (layer "%s")\n'
            '    (net %d)\n'
            '    (uuid "%s")\n'
            '  )' % (n(x1), n(y1), n(x2), n(y2), n(width), layer,
                     NET_ID[net], uid("seg", tag, x1, y1, x2, y2)))


def emit_via(x, y, net, tag, big=True):
    d, dr = (VIA_D, VIA_DRILL) if big else (VIA_SIG_D, VIA_SIG_DRILL)
    return ('  (via\n'
            '    (at %s %s)\n'
            '    (size %s)\n'
            '    (drill %s)\n'
            '    (layers "F.Cu" "B.Cu")\n'
            '    (net %d)\n'
            '    (uuid "%s")\n'
            '  )' % (n(x), n(y), n(d), n(dr), NET_ID[net],
                     uid("via", tag, x, y)))


def emit_zone(net, pts, layer, priority=0, tag=""):
    poly = "".join("(xy %s %s) " % (n(x), n(y)) for x, y in pts)
    return ('  (zone\n'
            '    (net %d)\n'
            '    (net_name "%s")\n'
            '    (layer "%s")\n'
            '    (uuid "%s")\n'
            '    (name "%s")\n'
            '    (hatch edge 0.5)\n'
            '    (priority %d)\n'
            '    (connect_pads\n'
            '      (clearance 0.3)\n'
            '    )\n'
            '    (min_thickness 0.3)\n'
            '    (filled_areas_thickness no)\n'
            '    (fill\n'
            '      (thermal_gap 0.3)\n'
            '      (thermal_bridge_width 0.8)\n'
            '    )\n'
            '    (polygon\n'
            '      (pts %s)\n'
            '    )\n'
            '  )' % (NET_ID[net], net_str(net), layer,
                     uid("zone", net, layer, tag),
                     net + "_" + layer.replace(".", ""), priority, poly))


def emit_outline():
    pts = [(0, 0), (BOARD_W, 0), (BOARD_W, BOARD_H), (0, BOARD_H), (0, 0)]
    out = []
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        out.append('  (gr_line\n'
                   '    (start %s %s)\n'
                   '    (end %s %s)\n'
                   '    (stroke (width 0.1) (type solid))\n'
                   '    (layer "Edge.Cuts")\n'
                   '    (uuid "%s")\n'
                   '  )' % (n(x1), n(y1), n(x2), n(y2), uid("edge", i)))
    return out


def emit_text(text, x, y, size=1.5, layer="F.SilkS", tag=""):
    return ('  (gr_text "%s"\n'
            '    (at %s %s 0)\n'
            '    (layer "%s")\n'
            '    (uuid "%s")\n'
            '    (effects (font (size %s %s) (thickness %s)) '
            '(justify left bottom))\n'
            '  )' % (text, n(x), n(y), layer, uid("txt", tag, text),
                     n(size), n(size), n(size / 7.0)))


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

def pgnd_via_stubs():
    """Drop every top-side PGND pad straight into the B.Cu pour.

    At 28 A the return path must be the plane, not a top-layer track, so each
    pad gets its own via rather than being daisy chained. Big terminal pads
    get an array, because a single 0.6 mm via is worth only a couple of amps
    and J1's return pad carries the full input current.
    """
    items = []
    for comp in netlist.C:
        for pad, net in comp.nets.items():
            if net != "PGND" or comp.ref not in PLACE:
                continue
            x, y = pad_pos(comp.ref, pad)
            fpp = [p for p in FP[comp.footprint]["pads"] if p["num"] == pad][0]
            if fpp["ptype"] == "thru_hole":
                continue                      # already spans both layers
            area = fpp["sx"] * fpp["sy"]
            if area > 25.0:
                # via array inside the pad, 2.5 mm pitch, 1 mm margin
                nx = max(1, int((fpp["sx"] - 2.0) // 2.5) + 1)
                ny = max(1, int((fpp["sy"] - 2.0) // 2.5) + 1)
                x0 = x - (nx - 1) * 1.25
                y0 = y - (ny - 1) * 1.25
                for i in range(nx):
                    for j in range(ny):
                        items.append(emit_via(x0 + i * 2.5, y0 + j * 2.5, net,
                                              ("arr", comp.ref, pad, i, j)))
            else:
                # Try each side in turn and take the first that keeps
                # clearance; a pad with no clean escape is left to the pour.
                w = min(fpp["sx"], 1.2)
                placed = False
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    off = (fpp["sy"] / 2 if dy else fpp["sx"] / 2) + 1.2
                    vx, vy = x + dx * off, y + dy * off
                    if not (2 < vx < BOARD_W - 2 and 2 < vy < BOARD_H - 2):
                        continue
                    ok, _ = route_is_clean(net, w, [(x, y), (vx, vy)],
                                           "F.Cu", ACCEPTED)
                    if ok and via_is_clean(vx, vy, net):
                        ACCEPTED.append((x, y, vx, vy, "F.Cu", net, w))
                        VIAS.append((vx, vy, net))
                        items.append(emit_segment(x, y, vx, vy, w, "F.Cu",
                                                  net, ("pg", comp.ref, pad)))
                        items.append(emit_via(vx, vy, net,
                                              ("pg", comp.ref, pad)))
                        placed = True
                        break
                if not placed:
                    SKIPPED_ROUTES.append(("PGND", "no clean via escape at %s.%s"
                                           % (comp.ref, pad)))
    return items


def stitching_vias():
    """Perimeter stitching so the PGND regions behave as one plane.

    Each candidate is clearance checked; ones that would land on other copper
    are simply dropped, since stitching is redundant by nature.
    """
    items = []
    cands = ([(x, 96.0) for x in range(8, 146, 8)]
             + [(146.0, y) for y in range(66, 96, 6)]
             + [(x, 4.0) for x in range(8, 146, 8)]
             + [(4.0, y) for y in range(8, 46, 6)])
    for i, (x, y) in enumerate(cands):
        if via_is_clean(x, y, "PGND"):
            VIAS.append((x, y, "PGND"))
            items.append(emit_via(x, y, "PGND", ("st", i)))
    return items


def assert_no_vias_under_l1():
    """P716 note 10: the PG1083's core is conductive.

    A via under the body is a hole in the mask waiting to touch it, and the
    failure is a trickle current through the core rather than a dead short -
    extra loss and heating that no bench measurement would obviously explain.
    The two via generators above are both clearance-driven and neither knows
    about the core, so this is checked rather than assumed. It has to run
    after them and before the zones.
    """
    x, y, rot = PLACE["L1"]
    x0, y0, x1, y1 = FP[COMPS["L1"].footprint]["crtyd"]
    if rot % 180:
        x0, y0, x1, y1 = y0, x0, y1, x1
    box = (x + x0, y + y0, x + x1, y + y1)
    hits = [(vx, vy, net) for vx, vy, net in VIAS
            if box[0] - VIA_D / 2 <= vx <= box[2] + VIA_D / 2
            and box[1] - VIA_D / 2 <= vy <= box[3] + VIA_D / 2]
    if hits:
        raise AssertionError(
            "%d via(s) fall inside L1's courtyard %s: %s. The PG1083 core is "
            "conductive (P716 note 10) - move L1 or move the vias."
            % (len(hits), tuple(round(v, 2) for v in box), hits))


def signal_routes():
    """Small-signal routing. Components were placed so these stay short."""
    table = [
        ("GDRV", W_SIG, ["U1", (58.0, 74.0), (58.0, 72.54), "U2"]),
        ("OUTH", W_SIG, ["U2", "R4"]),
        ("OUTL", W_SIG, ["U2", "R5"]),
        ("GATE", W_SIG, ["R4", (80.5, 66.0), (80.5, 70.0), "R5"]),
        ("GATE", W_SIG, ["R5", (80.5, 70.0), (80.5, 74.0), "R6"]),
        ("GATE", W_SIG, ["R6", (76.5, 74.0), (76.5, 58.0), (75.46, 58.0),
                         "Q1"]),
        ("GATE", W_SIG, [(76.5, 58.0), (88.46, 58.0), "Q2"]),
        ("GATE", W_SIG, [(80.5, 72.0), (84.0, 72.0), "TP5"]),
        ("RC", W_SIG, ["U1", (26.0, 69.08), (26.0, 64.0), "R7"]),
        ("RC", W_SIG, ["R7", (26.5, 64.0), (26.5, 68.0), "C24"]),
        ("SS", W_SIG, ["U1", (27.0, 71.62), (27.0, 72.0), "C25"]),
        ("BP", W_SIG, ["U1", (55.0, 71.62), (55.0, 68.5), "C26"]),
        ("BP", W_SIG, ["C26", (54.0, 68.5), (54.0, 64.5), "TP10"]),
        ("ISNS", W_SIG, ["U1", (55.5, 76.62), (55.5, 78.5), "R11"]),
        ("ISNS", W_SIG, ["R11", (56.0, 78.5), (56.0, 82.5), "C29"]),
        ("ISNS", W_SIG, ["C29", (59.5, 82.5), (59.5, 82.0), "TP2"]),
        # Leaves R2's sense finger heading straight out at +Y, away from the
        # package, before turning. Any sideways jog off the tip runs down the
        # side of the opposite terminal's solder pad and fails clearance.
        ("ISNS_K", W_SIG, ["R2", (84.5, 64.5), (67.5, 64.5), (67.5, 78.5),
                           "R11"]),
        ("FB", W_SIG, ["U1", (44.0, 74.16), (44.0, 83.0), (48.0, 83.0),
                       "R8"]),
        ("FB", W_SIG, ["R8", "R9"]),
        ("FB", W_SIG, ["R9", (51.0, 91.0), "C28"]),
        ("FB", W_SIG, ["C27", (42.0, 86.5), (42.0, 91.0), "C28"]),
        ("FB", W_SIG, [(51.0, 91.0), (55.0, 91.0), "TP4"]),
        ("COMP", W_SIG, ["U1", (37.0, 79.24), (37.0, 85.0), "R10"]),
        ("COMP", W_SIG, ["R10", (37.5, 85.0), (37.5, 91.0), "C28"]),
        ("COMP", W_SIG, [(37.0, 85.0), (33.0, 85.0), (33.0, 89.5), "TP3"]),
        ("CZ", W_SIG, ["R10", "C27"]),
        ("EN", W_SIG, ["U1", (35.0, 76.7), (35.0, 92.0), "R15"]),
        ("EN", W_SIG, [(35.0, 92.0), (31.5, 92.0), "JP1"]),
        ("EN", W_SIG, ["U3", (26.0, 88.0), (26.0, 92.0), (35.0, 92.0)]),
        ("EN", W_SIG, [(26.0, 92.0), (22.95, 92.0), "R16"]),
        ("EN", W_SIG, [(31.5, 92.0), (31.5, 94.5), (35.0, 94.5), "TP9"]),
        ("CMP_TH", W_SIG, ["U3", (15.5, 86.75), (15.5, 90.5), "R13"]),
        ("CMP_TH", W_SIG, ["R13", (15.0, 90.5), (15.0, 94.5), "R14"]),
        ("CMP_TH", W_SIG, ["R14", (16.0, 94.5), (16.0, 96.0), (29.0, 96.0),
                           "R15"]),
        ("CMP_REF", W_SIG, ["U3", (20.0, 90.75), (17.5, 90.75), "R13"]),
        ("CMP_REF", W_SIG, ["R13", (16.5, 89.5), (16.5, 93.5), "C34"]),
        ("CELL_S", W_SIG, ["U3", (20.5, 86.0), (20.5, 84.5), "C33"]),
        ("CELL_S", W_SIG, ["R17", "C33"]),
        ("V5C", W_SIG, ["R12", (14.5, 73.0), (14.5, 77.5), "D4"]),
        ("V5C", W_SIG, ["D4", (14.5, 78.5), (14.5, 83.5), "C32"]),
        ("V5C", W_SIG, ["C32", (15.5, 84.5), (15.5, 88.5), (19.0, 88.5),
                        "U3"]),
        ("V5C", W_SIG, ["U3", (25.5, 90.75), (25.5, 95.5), (21.5, 95.5),
                        "R16"]),
        ("V12AUX", W_SIG, ["J3", "D3"]),
        ("SNUB", W_SIG, ["R3", "C12"]),
        # Damping leg: three caps down to R1. Carries real ripple current, so
        # it is not a signal-width trace.
        ("DAMP", 1.5, ["C7", (30.0, 16.0), (30.0, 30.0), "C8"]),
        ("DAMP", 1.5, ["C8", (30.0, 30.0), (30.0, 44.0), "C9"]),
        ("DAMP", 1.5, ["C9", "R1"]),
        # 12 V bias spine along y = 59.5, clear of the AGND island below it
        ("V12", W_SIG, ["D3", (15.85, 57.0), (71.05, 57.0)]),
        ("V12", W_SIG, ["C30", (23.5, 57.0)]),
        ("V12", W_SIG, ["TP12", (28.0, 57.0)]),
        ("V12", W_SIG, ["R7", (29.2125, 57.0)]),
        ("V12", W_SIG, ["R12", (6.5, 72.0), (6.5, 57.0), (15.85, 57.0)]),
        ("V12", W_SIG, ["C31", (51.2125, 57.0)]),
        ("V12", W_SIG, ["U1", (48.0, 73.0), (48.0, 57.0)]),
        ("V12", W_SIG, ["U2", (71.05, 57.0)]),
        ("V12", W_SIG, ["C22", (65.05, 71.25), (71.05, 71.25)]),
        ("V12", W_SIG, ["C23", (65.2125, 57.0)]),
    ]
    return build_routes(table)



def pin_of(ref, net, idx=0):
    """The pin of `ref` that sits on `net`.

    Routing tables name a component and a net, never a pin number, so a route
    cannot be attached to the wrong pad. `idx` picks between pins when a part
    has several on one net (D1 has both anodes on SW).
    """
    pins = [p for p, nn in sorted(COMPS[ref].nets.items()) if nn == net]
    if not pins:
        raise AssertionError("%s has no pin on net %r" % (ref, net))
    return pins[idx]


def resolve(item, net):
    """A route waypoint: either a literal (x, y) or a component reference."""
    if isinstance(item, str):
        ref, _, which = item.partition("#")
        return pad_pos(ref, pin_of(ref, net, int(which or 0)), expect_net=net)
    return item


ESCAPE_LEN = {"MSOP-10_3x3mm_P0.5mm": 3.0, "SOT-23-6": 2.5,
              "TO-220-3_Vertical": 4.0}


# --------------------------------------------------------------------------
# Clearance-aware route acceptance
#
# The generator refuses to emit copper that would violate clearance. Anything
# it cannot route safely is reported and left as ratsnest for KiCad, which is
# far more useful than a board carrying a hundred sub-clearance gaps.
# --------------------------------------------------------------------------

MIN_CLEARANCE = 0.2

# Must track the netclasses written into the .kicad_pro by gen_schematic.py.
DEFAULT_CLEARANCE = 0.2
POWER_CLEARANCE = 0.3
POWER_NETS = ("VCELL", "PGND", "SW", "SRC", "VOUT")
NETCLASS_CLEARANCE = {net: POWER_CLEARANCE for net in POWER_NETS}
PAD_ENTRY_WIDTH = 1.2      # taper into fine-pitch pads
SKIPPED_ROUTES = []
ACCEPTED = []
VIAS = []


def _seg_seg_dist(a, b, c, d):
    def pt_seg(p, q, r):
        qx, qy = r[0] - q[0], r[1] - q[1]
        L2 = qx * qx + qy * qy
        if L2 == 0:
            return math.hypot(p[0] - q[0], p[1] - q[1])
        tt = max(0.0, min(1.0, ((p[0] - q[0]) * qx + (p[1] - q[1]) * qy) / L2))
        return math.hypot(p[0] - (q[0] + tt * qx), p[1] - (q[1] + tt * qy))

    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d),
               pt_seg(c, a, b), pt_seg(d, a, b))


def _seg_rect_dist(a, b, cx, cy, hx, hy):
    x0, y0, x1, y1 = cx - hx, cy - hy, cx + hx, cy + hy
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1:
        return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return min(_seg_seg_dist(a, b, corners[i], corners[(i + 1) % 4])
               for i in range(4))


def _all_pad_rects():
    out = []
    for ref, (x, y, rot) in PLACE.items():
        comp = COMPS.get(ref)
        if comp is None or not comp.footprint:
            continue
        for p in FP[comp.footprint]["pads"]:
            net = comp.nets.get(p["num"])
            dx, dy = xform(p["x"], p["y"], rot)
            hx, hy = p["sx"] / 2.0, p["sy"] / 2.0
            if rot % 180:
                hx, hy = hy, hx
            out.append((x + dx, y + dy, hx, hy, net, ref, p["num"],
                        p["ptype"]))
    return out


PAD_RECTS = _all_pad_rects()


# A note on net ties, because the obvious shortcut here is wrong. R2's pads 1/3
# and 2/4 are declared as net-tie groups, and it is tempting to let this router
# ignore clearance between a track and a pad that is tied to the track's net.
# KiCad does not work that way: the tie exempts the tied *pads* from each other,
# and an ordinary track still owes full clearance to every pad of another net,
# tied or not. Adding that exemption here made the generator emit three pieces
# of copper that KiCad's DRC then rejected - a checker that disagrees with the
# real one is worse than no checker. Route around the pads instead.


def clearance_between(net_a, net_b):
    """Clearance the netclasses demand of this pair.

    KiCad applies the larger of the two netclass clearances, so the router has
    to as well. Routing everything at the Default 0.2 mm produced copper that
    passed this check and then failed KiCad's DRC on every Power net.
    """
    return max(NETCLASS_CLEARANCE.get(net_a, DEFAULT_CLEARANCE),
               NETCLASS_CLEARANCE.get(net_b, DEFAULT_CLEARANCE))


def route_is_clean(net, width, pts, layer, accepted):
    half = width / 2.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        for (px, py, hx, hy, pnet, pref, pnum, ptype) in PAD_RECTS:
            if pnet == net or pnet is None:
                continue
            if layer == "B.Cu" and ptype != "thru_hole":
                continue
            d = _seg_rect_dist(a, b, px, py, hx, hy) - half
            if d < clearance_between(net, pnet):
                return False, "pad %s.%s (%s) %.3f mm" % (pref, pnum, pnet, d)
        for (ax, ay, bx, by, alayer, anet, awidth) in accepted:
            if anet == net or alayer != layer:
                continue
            d = _seg_seg_dist(a, b, (ax, ay), (bx, by)) - half - awidth / 2.0
            if d < clearance_between(net, anet):
                return False, "track %s %.3f mm" % (anet, d)
    return True, ""


def via_is_clean(vx, vy, net, radius=0.6):
    for (px, py, hx, hy, pnet, pref, pnum, ptype) in PAD_RECTS:
        if pnet is not None and pnet == net:
            continue
        # A netless pad is a non-plated hole (mounting hole, fiducial). It can
        # never be "the same net" as the via, so it must be avoided rather
        # than skipped - a via dropped inside an M3 hole is a drill collision,
        # not a connection.
        gx = abs(vx - px) - radius - hx
        gy = abs(vy - py) - radius - hy
        if max(gx, gy) < clearance_between(net, pnet):
            return False
    for (ax, ay, bx, by, alayer, anet, awidth) in ACCEPTED:
        if anet == net:
            continue
        if (_seg_seg_dist((vx, vy), (vx, vy), (ax, ay), (bx, by))
                - radius - awidth / 2.0) < clearance_between(net, anet):
            return False
    for (ox, oy, onet) in VIAS:
        if onet == net:
            continue
        if (math.hypot(vx - ox, vy - oy) - 2 * radius
                < clearance_between(net, onet)):
            return False
    return True


def pad_escape(ref, pad_num, toward=None):
    """Direction a track should leave a pad, and how far, before turning.

    Any of the four axis directions is allowed provided the escape ray does
    not run into another pad of the same footprint. Among the survivors the
    one facing the incoming route wins, which is what stops a track from
    having to come back around the part and cross its other pin.
    """
    comp = COMPS[ref]
    fpname = comp.footprint
    length = ESCAPE_LEN.get(fpname, 1.8)
    rot = PLACE[ref][2]
    px, py = pad_pos(ref, pad_num)

    siblings = []
    for q in FP[fpname]["pads"]:
        if q["num"] == pad_num:
            continue
        dx, dy = xform(q["x"], q["y"], rot)
        hx, hy = q["sx"] / 2.0, q["sy"] / 2.0
        if rot % 180:
            hx, hy = hy, hx
        siblings.append((PLACE[ref][0] + dx, PLACE[ref][1] + dy, hx, hy))

    # Test the escape against a ray long enough to leave the package, not just
    # the escape stub. A 1.8 mm ray fired inward from an MSOP-10 pin stops
    # short of the opposite pin row, so "escape into the middle of the part"
    # scored as safe - and the track then turned and crossed every pin on the
    # far side. The direction is only usable if it still clears the siblings
    # once carried past the courtyard.
    x0, y0, x1, y1 = FP[fpname]["crtyd"]
    probe = max(length, (x1 - x0), (y1 - y0)) + 0.5

    safe = []
    for d in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
        tip = (px + d[0] * probe, py + d[1] * probe)
        if all(_seg_rect_dist((px, py), tip, sx, sy, shx, shy) > 0.35
               for (sx, sy, shx, shy) in siblings):
            safe.append(d)
    if not safe:
        safe = [(0.0, 1.0)]

    if toward is not None:
        vx, vy = toward[0] - px, toward[1] - py
        safe.sort(key=lambda d: -(d[0] * vx + d[1] * vy))
    else:
        # default: straight out of the package
        p = [q for q in FP[fpname]["pads"] if q["num"] == pad_num][0]
        pref = ((1.0 if p["x"] >= 0 else -1.0, 0.0)
                if abs(p["x"]) >= abs(p["y"])
                else (0.0, 1.0 if p["y"] >= 0 else -1.0))
        prefr = xform(pref[0], pref[1], rot)
        safe.sort(key=lambda d: -(d[0] * prefr[0] + d[1] * prefr[1]))
    d = safe[0]
    return d[0] * length, d[1] * length


def build_routes(table, layer="F.Cu"):
    """Resolve a routing table into orthogonal copper, skipping unsafe routes.

    Waypoints are either literal (x, y) or a component reference whose pin is
    looked up from the net, so a route can never attach to the wrong pad.
    """
    items = []
    for entry in table:
        net, width, pts = entry
        resolved = []
        pads = [isinstance(q, str) for q in pts]
        for i, item in enumerate(pts):
            pt = resolve(item, net)
            if pads[i]:
                ref, _, which = item.partition("#")
                nb = pts[i + 1] if i == 0 else pts[i - 1]
                toward = nb if not isinstance(nb, str) else None
                ex, ey = pad_escape(ref, pin_of(ref, net, int(which or 0)),
                                    toward)
                if i == 0:
                    resolved.append(pt)
                    resolved.append((pt[0] + ex, pt[1] + ey))
                else:
                    resolved.append((pt[0] + ex, pt[1] + ey))
                    resolved.append(pt)
            else:
                resolved.append(pt)

        # Mark which path points are pad escapes, and along which axis.
        esc_axis = {}
        for i, item in enumerate(pts):
            if not pads[i]:
                continue
            ref, _, which = item.partition("#")
            nb = pts[i + 1] if i == 0 else pts[i - 1]
            toward = nb if not isinstance(nb, str) else None
            ex, ey = pad_escape(ref, pin_of(ref, net, int(which or 0)), toward)
            idx = 1 if i == 0 else resolved.index(
                (resolve(item, net)[0] + ex, resolve(item, net)[1] + ey))
            esc_axis[idx] = "x" if abs(ex) >= abs(ey) else "y"

        # Corner placement rule: never let a segment run along a pad's own row
        # or column, which is what drags copper across the adjacent pin.
        ortho = [resolved[0]]
        alternate = True
        for i in range(1, len(resolved)):
            ax, ay = ortho[-1]
            bx, by = resolved[i]
            if abs(ax - bx) > 1e-6 and abs(ay - by) > 1e-6:
                if i in esc_axis:
                    h_first = esc_axis[i] == "x"
                elif (i - 1) in esc_axis:
                    h_first = esc_axis[i - 1] == "y"
                else:
                    h_first = alternate
                    alternate = not alternate
                ortho.append((bx, ay) if h_first else (ax, by))
            ortho.append((bx, by))
        ortho = [q for i, q in enumerate(ortho)
                 if i == 0 or abs(q[0] - ortho[i - 1][0]) > 1e-6
                 or abs(q[1] - ortho[i - 1][1]) > 1e-6]

        # A 4 mm track cannot land on a 2.54 mm pitch TO-220 pin, so the
        # segments that actually touch a pad are tapered down.
        widths = []
        for i in range(len(ortho) - 1):
            touches_pad = (i == 0 and pads[0]) or (i == len(ortho) - 2
                                                   and pads[-1])
            widths.append(min(width, PAD_ENTRY_WIDTH) if touches_pad
                          else width)

        ok = True
        why = ""
        for i in range(len(ortho) - 1):
            good, reason = route_is_clean(net, widths[i],
                                          [ortho[i], ortho[i + 1]], layer,
                                          ACCEPTED)
            if not good:
                ok, why = False, reason
                break
        if not ok:
            SKIPPED_ROUTES.append((net, why))
            continue
        for i in range(len(ortho) - 1):
            (x1, y1), (x2, y2) = ortho[i], ortho[i + 1]
            ACCEPTED.append((x1, y1, x2, y2, layer, net, widths[i]))
            items.append(emit_segment(x1, y1, x2, y2, widths[i], layer, net,
                                      (net, i, x1, y1, x2, y2)))
    return items




def power_routes():
    """The high-current paths, routed as explicit wide copper.

    SW deliberately is not a zone. Everything here is 3-4 mm wide, which at
    2 oz is roughly 0.5 mohm per 10 mm - the whole point of the exercise, since
    every extra milliohm in the 28 A path costs about a watt.
    """
    swx = pad_pos("L1", "2")[0]
    table = [
        # switch node: inductor -> both FETs -> both diode anodes
        ("SW", 4.0, ["L1", (swx, 44.0), (78.0, 44.0), "Q1"]),
        ("SW", 4.0, [(swx, 44.0), (91.0, 44.0), "Q2"]),
        ("SW", 4.0, [(91.0, 44.0), (109.46, 44.0), "D1#0"]),
        ("SW", 4.0, [(109.46, 44.0), (114.54, 44.0), "D1#1"]),
        # snubber tap off the switch node
        ("SW", 1.0, [(swx, 36.0), (100.0, 36.0), "R3"]),
        ("SW", 0.6, [(100.0, 44.0), "TP1"]),
        # FET sources down into the shunt
        ("SRC", 4.0, ["Q1", (80.54, 56.0), (76.0, 56.0), (76.0, 60.0), "R2"]),
        ("SRC", 4.0, ["Q2", (93.54, 56.0), (80.54, 56.0)]),
        # rectifier cathode into the output pour
        ("VOUT", 4.0, ["D1", (112.0, 59.0)]),
        # feedback sense back to the divider, and the cell tap for the UVLO
        ("VOUT", 0.4, ["R8", (46.0, 99.0), (140.0, 99.0), (140.0, 83.0)]),
        ("VCELL", 0.4, ["R17", (17.21, 46.0)]),
    ]
    return build_routes(table)


def agnd_link():
    """AGND island tied to the shunt's sense terminal and nowhere else.

    This single connection is the star point: the Kelvin low-side tap of R2
    defines the controller's ground reference. Any other AGND-PGND link would
    put switch current into the sense measurement.
    """
    return build_routes([("AGND", W_SIG, ["R2", (85.5, 92.0), (62.0, 92.0)])])


# --------------------------------------------------------------------------

def build():
    items = []
    items += emit_outline()

    for comp in netlist.C:
        if comp.ref in PLACE and comp.footprint:
            items.append(emit_footprint(comp))

    # Order matters: this is a greedy router, so the copper that must not be
    # compromised claims its space first. Power, then the Kelvin sense return,
    # then small signal, and finally the PGND vias, which have four fallback
    # directions each and can afford to go last.
    items += power_routes()
    items += agnd_link()
    items += signal_routes()
    items += pgnd_via_stubs()
    items += stitching_vias()
    assert_no_vias_under_l1()

    for net, pts in ZONES_F.items():
        items.append(emit_zone(net, pts, "F.Cu",
                               priority=2 if net == "AGND" else 1))
    items.append(emit_zone("PGND", ZONE_B_PGND, "B.Cu", priority=0))
    # F.Cu PGND flood at the lowest priority, so it takes only the copper the
    # VCELL / VOUT / AGND islands above it do not claim. Without it the
    # perimeter stitching vias have no front-side PGND to reach and are just
    # blind holes into the back-side plane; with it they genuinely tie the two
    # halves of the return path together. The filler keeps it clear of AGND,
    # so the single Kelvin star point at the shunt is preserved.
    items.append(emit_zone("PGND", ZONE_B_PGND, "F.Cu", priority=0,
                           tag="flood"))

    # gr_text is justified (left, bottom), so x,y is the lower-left corner.
    # The bottom band is shared with H3/H4 and the y = 96 stitching via row,
    # so the title block sits clear of both rather than centred.
    notes = [
        ("1.2V -> 12V / 20W BOOST  rev A", 62.0, 91.0, 2.0),
        ("2 layer, 2 oz copper. Cell 0.9-1.6 V up to 28 A.", 62.0, 94.5, 1.5),
        ("L1 = Pulse PG1083.152NL. Keep vias clear of the core.",
         62.0, 99.0, 1.5),
        ("CELL", 2.0, 12.0, 2.0),
        ("12V OUT", 134.0, 42.0, 2.0),
        ("12V BIAS", 4.0, 57.0, 1.5),
    ]
    for text, x, y, size in notes:
        items.append(emit_text(text, x, y, size, tag="note"))

    nets_block = "\n".join('  (net %d "%s")' % (i, net_str(name))
                           for i, name in enumerate(NETS))

    head = [
        "(kicad_pcb",
        # KiCad 10 renumbered the copper/technical layer IDs. The table below
        # is KiCad 10 numbering (verified against a board written by pcbnew
        # 10.0.5), so the file must declare the KiCad 10 format version. Tag it
        # 20241229 and KiCad 10 runs its v9->v10 layer remap on numbers that
        # are already v10, silently moving every pad and graphic to the wrong
        # layer.
        '  (version 20260206)',
        '  (generator "1v2boost_gen")',
        '  (generator_version "10.0")',
        "  (general",
        "    (thickness 1.6)",
        "    (legacy_teardrops no)",
        "  )",
        '  (paper "A3")',
        "  (layers",
        '    (0 "F.Cu" signal)',
        '    (2 "B.Cu" signal)',
        '    (9 "F.Adhes" user "F.Adhesive")',
        '    (11 "B.Adhes" user "B.Adhesive")',
        '    (13 "F.Paste" user)',
        '    (15 "B.Paste" user)',
        '    (5 "F.SilkS" user "F.Silkscreen")',
        '    (7 "B.SilkS" user "B.Silkscreen")',
        '    (1 "F.Mask" user)',
        '    (3 "B.Mask" user)',
        '    (17 "Dwgs.User" user "User.Drawings")',
        '    (19 "Cmts.User" user "User.Comments")',
        '    (21 "Eco1.User" user "User.Eco1")',
        '    (23 "Eco2.User" user "User.Eco2")',
        '    (25 "Edge.Cuts" user)',
        '    (27 "Margin" user)',
        '    (31 "F.CrtYd" user "F.Courtyard")',
        '    (29 "B.CrtYd" user "B.Courtyard")',
        '    (35 "F.Fab" user)',
        '    (33 "B.Fab" user)',
        "  )",
        "  (setup",
        "    (pad_to_mask_clearance 0)",
        "    (allow_soldermask_bridges_in_footprints no)",
        "  )",
        nets_block,
    ]
    return "\n".join(head + items) + "\n)\n"


def main():
    text = build()
    path = os.path.join(ROOT, "1v2boost.kicad_pcb")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if SKIPPED_ROUTES:
        print("%d route(s) left as ratsnest to keep copper DRC-clean:"
              % len(SKIPPED_ROUTES))
        for net, why in SKIPPED_ROUTES:
            print("   %-9s blocked by %s" % (net, why))
        print()
    print("wrote 1v2boost.kicad_pcb  %d bytes, %d footprints, %d nets"
          % (len(text), sum(1 for c in netlist.C
                            if c.ref in PLACE and c.footprint), len(NETS) - 1))
    missing = [c.ref for c in netlist.C
               if c.footprint and c.ref not in PLACE]
    if missing:
        print("NOT PLACED: %s" % missing)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
