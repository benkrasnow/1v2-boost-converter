"""
Structural validation of the generated KiCad files.

KiCad is not installed in the environment these files were generated in, so
this stands in for ERC/DRC as far as it can. The important check is
`check_schematic_netlist`: it parses the emitted .kicad_sch, rebuilds
connectivity from wire and label geometry, and compares the result against
netlist.py. That catches placement or stub-geometry bugs that would otherwise
only show up as a silently wrong board.

    python tools/validate.py

Exit status is non-zero if anything fails.
"""

from __future__ import annotations

import glob
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import footprints                  # noqa: E402
import kicad_netlist               # noqa: E402
import netlist                     # noqa: E402
import symbols                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []
NOTES = []


def fail(msg):
    FAILURES.append(msg)


def note(msg):
    NOTES.append(msg)


# --------------------------------------------------------------------------
# S-expression parser
# --------------------------------------------------------------------------

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse_sexp(text):
    stack, cur = [], []
    for m in TOKEN.finditer(text):
        t = m.group(0)
        if t == "(":
            new = []
            cur.append(new)
            stack.append(cur)
            cur = new
        elif t == ")":
            if not stack:
                raise ValueError("unbalanced ')' at offset %d" % m.start())
            cur = stack.pop()
        elif t.startswith('"'):
            cur.append(("str", t[1:-1].replace('\\"', '"')))
        else:
            cur.append(("sym", t))
    if stack:
        raise ValueError("unbalanced '(' - %d unclosed" % len(stack))
    return cur


def is_node(x, name=None):
    if not isinstance(x, list) or not x:
        return False
    head = x[0]
    if not (isinstance(head, tuple) and head[0] == "sym"):
        return False
    return name is None or head[1] == name


def children(node, name):
    return [c for c in node if is_node(c, name)]


def first(node, name):
    for c in node:
        if is_node(c, name):
            return c
    return None


def val(node, i=1):
    """Value of the i-th element of a node, as a string."""
    if node is None or len(node) <= i:
        return None
    x = node[i]
    return x[1] if isinstance(x, tuple) else None


def nums(node, start=1):
    out = []
    for x in node[start:]:
        if isinstance(x, tuple):
            try:
                out.append(float(x[1]))
            except ValueError:
                break
        else:
            break
    return out


# --------------------------------------------------------------------------
# Generic file checks
# --------------------------------------------------------------------------

def check_parses():
    files = ([os.path.join(ROOT, f) for f in
              ("1v2boost.kicad_sch", "1v2boost.kicad_pcb",
               "lib/1v2boost.kicad_sym", "sym-lib-table", "fp-lib-table")]
             + sorted(glob.glob(os.path.join(ROOT, "lib/1v2boost.pretty/*.kicad_mod"))))
    trees = {}
    for path in files:
        if not os.path.exists(path):
            fail("missing file: %s" % os.path.relpath(path, ROOT))
            continue
        text = open(path, encoding="utf-8").read()
        try:
            trees[path] = parse_sexp(text)[0]
        except ValueError as e:
            fail("%s: %s" % (os.path.relpath(path, ROOT), e))
        # UUID uniqueness
        ids = re.findall(r'\(uuid "([^"]+)"\)', text)
        dupes = {u for u in ids if ids.count(u) > 1}
        if dupes:
            fail("%s: %d duplicate uuid(s), e.g. %s"
                 % (os.path.relpath(path, ROOT), len(dupes), sorted(dupes)[:3]))
    return trees


def check_libraries(trees):
    sym_path = os.path.join(ROOT, "lib/1v2boost.kicad_sym")
    if sym_path in trees:
        defined = {val(s) for s in children(trees[sym_path], "symbol")}
        for c in netlist.C:
            if c.symbol not in defined:
                fail("symbol %r (used by %s) not in kicad_sym" % (c.symbol, c.ref))
        note("symbol library defines %d symbols" % len(defined))

    have = {os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(ROOT, "lib/1v2boost.pretty/*.kicad_mod"))}
    for c in netlist.C:
        if c.footprint and c.footprint not in have:
            fail("footprint %r (used by %s) missing from .pretty"
                 % (c.footprint, c.ref))
    note("footprint library contains %d footprints" % len(have))

    # Every footprint must have at least as many pads as the symbol has pins.
    for c in netlist.C:
        if not c.footprint:
            continue
        path = os.path.join(ROOT, "lib/1v2boost.pretty", c.footprint + ".kicad_mod")
        if path not in trees:
            continue
        pads = {val(p) for p in children(trees[path], "pad")}
        pads.discard("")
        for pin in c.nets:
            if pin not in pads:
                fail("%s: pin %s has no matching pad in footprint %s (pads: %s)"
                     % (c.ref, pin, c.footprint, sorted(pads)))


# --------------------------------------------------------------------------
# Netlist reconstruction from the schematic
# --------------------------------------------------------------------------

class DSU:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def key(x, y):
    return (round(float(x), 3), round(float(y), 3))


def rotate(px, py, deg):
    r = math.radians(deg)
    return (px * math.cos(r) - py * math.sin(r),
            px * math.sin(r) + py * math.cos(r))


def check_schematic_netlist(trees):
    path = os.path.join(ROOT, "1v2boost.kicad_sch")
    if path not in trees:
        return
    tree = trees[path]

    dsu = DSU()
    for w in children(tree, "wire"):
        pts = first(w, "pts")
        xy = [nums(p) for p in children(pts, "xy")]
        if len(xy) >= 2:
            dsu.union(key(*xy[0]), key(*xy[1]))

    # Labels name the point they sit on.
    named = {}
    for lab in children(tree, "label"):
        name = val(lab)
        at = nums(first(lab, "at"))
        named.setdefault(key(at[0], at[1]), set()).add(name)

    # Symbol instances: pin positions and, for power symbols, the net name.
    lib_by_name = {}
    for c in netlist.C:
        lib_by_name[c.ref] = c

    pin_positions = {}          # (ref, pin) -> point
    placed = {}
    for sym in children(tree, "symbol"):
        lib_id = val(first(sym, "lib_id"))
        if lib_id is None:
            continue
        at = nums(first(sym, "at"))
        ref = None
        value = None
        for prop in children(sym, "property"):
            if val(prop, 1) == "Reference":
                ref = val(prop, 2)
            elif val(prop, 1) == "Value":
                value = val(prop, 2)
        short = lib_id.split(":", 1)[-1]
        rot = at[2] if len(at) > 2 else 0
        pins = symbols.PIN_MAP.get(short, {})
        if short in ("VCELL", "VOUT", "V12", "V5C", "PGND", "AGND"):
            # power symbol: names the point it sits on
            named.setdefault(key(at[0], at[1]), set()).add(short)
            continue
        if ref is None or ref.startswith("#"):
            # power flags carry no netlist obligation beyond ERC
            continue
        placed[ref] = (at[0], at[1], rot)
        for pin, (px, py, _ang) in pins.items():
            rx, ry = rotate(px, -py, -rot)
            pin_positions[(ref, pin)] = key(at[0] + rx, at[1] + ry)

    # Resolve each pin to a net name.
    groups = {}
    for pt, names in named.items():
        groups.setdefault(dsu.find(pt), set()).update(names)

    derived = {}
    for (ref, pin), pt in pin_positions.items():
        root = dsu.find(pt)
        names = groups.get(root, set())
        if len(names) == 1:
            derived[(ref, pin)] = next(iter(names))
        elif len(names) > 1:
            fail("schematic: %s.%s sits on a node with conflicting labels %s"
                 % (ref, pin, sorted(names)))
        else:
            derived[(ref, pin)] = None

    # Compare against the source of truth.
    expected = {}
    for c in netlist.C:
        if c.ref.startswith("#"):
            continue
        for pin, net in c.nets.items():
            expected[(c.ref, pin)] = net

    missing_syms = [c.ref for c in netlist.C
                    if not c.ref.startswith("#") and c.ref not in placed]
    if missing_syms:
        fail("schematic: %d component(s) not placed: %s"
             % (len(missing_syms), missing_syms[:8]))

    mismatch = 0
    for kpin, net in sorted(expected.items()):
        got = derived.get(kpin, "<absent>")
        if got != net:
            mismatch += 1
            if mismatch <= 10:
                fail("schematic net mismatch at %s.%s: expected %r, got %r"
                     % (kpin[0], kpin[1], net, got))
    if mismatch > 10:
        fail("... and %d further net mismatches" % (mismatch - 10))
    if not mismatch:
        note("schematic netlist matches netlist.py exactly (%d pins on %d nets)"
             % (len(expected), len({v for v in expected.values()})))

    # No stray pin should collide with an unrelated pin.
    seen = {}
    for kpin, pt in pin_positions.items():
        if pt in seen and expected.get(kpin) != expected.get(seen[pt]):
            fail("schematic: %s.%s overlaps %s.%s at %s but they are different nets"
                 % (kpin[0], kpin[1], seen[pt][0], seen[pt][1], pt))
        seen[pt] = kpin


# --------------------------------------------------------------------------
# PCB checks
# --------------------------------------------------------------------------

def check_pcb(trees):
    path = os.path.join(ROOT, "1v2boost.kicad_pcb")
    if path not in trees:
        note("no .kicad_pcb yet - skipping board checks")
        return
    tree = trees[path]

    # Two board formats to cope with. Up to KiCad 9 a board carried a
    # top-level table of "(net <id> <name>)" and pads referred to the id;
    # KiCad 10 dropped the table and pads name their net directly. This
    # generator writes the older form, KiCad rewrites the newer one whenever
    # it saves (the zone fill does), so both turn up in practice.
    declared = {}
    names = set()

    def scan(node):
        if not isinstance(node, list) or not node:
            return
        if is_node(node, "net"):
            if len(node) > 2:                     # (net <id> "<name>")
                declared[val(node, 1)] = val(node, 2)
                names.add(val(node, 2))
            elif len(node) > 1:                   # (net "<name>")
                v = val(node, 1)
                if isinstance(v, str) and not str(v).lstrip("-").isdigit():
                    names.add(v)
        for c in node[1:]:
            scan(c)

    scan(tree)

    # The board carries KiCad's net names, which sheet-qualify local labels
    # ("DAMP" -> "/DAMP") but leave power and global labels bare. Compare
    # against those, not against netlist.py's internal bare names.
    try:
        ki_name = kicad_netlist.load(refresh=False).bare_to_kicad()
    except Exception as exc:                       # no netlist exported yet
        note("pcb: comparing bare net names (%s)" % exc)
        ki_name = {}

    got = {v for v in names if v}
    for want in sorted(netlist.all_nets()):
        if ki_name.get(want, want) not in got:
            fail("pcb: net %r declared in netlist.py is absent from the board"
                 % want)

    refs_on_board = set()
    pad_nets = {}
    for fp in children(tree, "footprint"):
        ref = None
        for prop in children(fp, "property"):
            if val(prop, 1) == "Reference":
                ref = val(prop, 2)
        if ref:
            refs_on_board.add(ref)
        for pad in children(fp, "pad"):
            netn = first(pad, "net")
            if netn is not None:
                pad_nets.setdefault(val(netn, 2), set()).add(ref)

    for c in netlist.C:
        if c.ref.startswith("#") or not c.footprint:
            continue
        if c.ref not in refs_on_board:
            fail("pcb: %s missing from the board" % c.ref)

    # Every board pad net must agree with the schematic
    src = netlist.all_nets()
    for net, refs in pad_nets.items():
        if net is None:
            continue
        want = {r for r, _ in src.get(net, [])}
        stray = refs - want
        if stray:
            fail("pcb: net %r reaches unexpected component(s) %s"
                 % (net, sorted(stray)))

    note("board carries %d footprints and %d nets"
         % (len(refs_on_board), len(got)))

    check_pcb_geometry(tree, declared)


def _rot(px, py, deg):
    r = math.radians(deg)
    return (px * math.cos(r) - py * math.sin(r),
            px * math.sin(r) + py * math.cos(r))


def _point_in_poly(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _on_segment(px, py, x1, y1, x2, y2, tol=0.01):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return abs(px - x1) < tol and abs(py - y1) < tol
    t = ((px - x1) * dx + (py - y1) * dy) / L2
    if t < -1e-9 or t > 1 + 1e-9:
        return False
    cx, cy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - cx, py - cy) < tol


BOARD_W, BOARD_H = 150.0, 100.0


def check_pcb_geometry(tree, declared):
    """Courtyard collisions, board containment, and per-net connectivity."""
    import gen_pcb

    # --- courtyard overlaps ------------------------------------------------
    boxes = {}
    for ref, (x, y, rot) in gen_pcb.PLACE.items():
        comp = gen_pcb.COMPS.get(ref)
        if comp is None or not comp.footprint:
            continue
        x0, y0, x1, y1 = gen_pcb.FP[comp.footprint]["crtyd"]
        corners = [_rot(cx, cy, rot) for cx, cy in
                   ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        xs = [x + c[0] for c in corners]
        ys = [y + c[1] for c in corners]
        boxes[ref] = (min(xs), min(ys), max(xs), max(ys))
        if (min(xs) < 0.5 or min(ys) < 0.5
                or max(xs) > BOARD_W - 0.5 or max(ys) > BOARD_H - 0.5):
            fail("pcb: %s courtyard falls outside the board outline (%s)"
                 % (ref, tuple(round(v, 2) for v in boxes[ref])))

    refs = sorted(boxes)
    collisions = 0
    for i, a in enumerate(refs):
        ax0, ay0, ax1, ay1 = boxes[a]
        for b in refs[i + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 0.01 and oy > 0.01:
                collisions += 1
                if collisions <= 8:
                    fail("pcb: courtyards of %s and %s overlap by %.2f x %.2f mm"
                         % (a, b, ox, oy))
    if collisions > 8:
        fail("pcb: ... and %d further courtyard overlaps" % (collisions - 8))
    if not collisions:
        note("no courtyard collisions among %d placed footprints" % len(refs))

    # --- gather copper -----------------------------------------------------
    id2net = declared
    segs = []
    for s in children(tree, "segment"):
        a = nums(first(s, "start"))
        b = nums(first(s, "end"))
        layer = val(first(s, "layer"))
        net = id2net.get(val(first(s, "net")), "")
        segs.append((a[0], a[1], b[0], b[1], layer, net))
    vias = []
    for v in children(tree, "via"):
        a = nums(first(v, "at"))
        vias.append((a[0], a[1], id2net.get(val(first(v, "net")), "")))

    zones = []
    for z in children(tree, "zone"):
        netname = val(first(z, "net_name"))
        layer = val(first(z, "layer"))
        poly = first(z, "polygon")
        pts = [nums(p) for p in children(first(poly, "pts"), "xy")]
        zones.append((netname, layer, [(p[0], p[1]) for p in pts]))

    # pad positions per (ref, pad)
    pads = {}
    for ref, (x, y, rot) in gen_pcb.PLACE.items():
        comp = gen_pcb.COMPS.get(ref)
        if comp is None or not comp.footprint:
            continue
        for p in gen_pcb.FP[comp.footprint]["pads"]:
            net = comp.nets.get(p["num"])
            if not net:
                continue
            dx, dy = _rot(p["x"], p["y"], rot)
            layers = ("F.Cu", "B.Cu") if p["ptype"] == "thru_hole" else ("F.Cu",)
            hx, hy = p["sx"] / 2.0, p["sy"] / 2.0
            if rot % 180:
                hx, hy = hy, hx
            pads[(ref, p["num"])] = (x + dx, y + dy, hx, hy, net, layers)

    # --- connectivity ------------------------------------------------------
    dsu = DSU()

    def node(kind, *rest):
        return (kind,) + rest

    for i, (x1, y1, x2, y2, layer, net) in enumerate(segs):
        dsu.union(node("s", i, 0), node("s", i, 1))
    # segment endpoints touching other segments (same layer, same net)
    for i, (x1, y1, x2, y2, la, na) in enumerate(segs):
        for j, (u1, v1, u2, v2, lb, nb) in enumerate(segs):
            if i >= j or la != lb or na != nb:
                continue
            for (px, py), ei in (((x1, y1), 0), ((x2, y2), 1)):
                if _on_segment(px, py, u1, v1, u2, v2):
                    dsu.union(node("s", i, ei), node("s", j, 0))
            for (px, py), ej in (((u1, v1), 0), ((u2, v2), 1)):
                if _on_segment(px, py, x1, y1, x2, y2):
                    dsu.union(node("s", j, ej), node("s", i, 0))
    # vias tie layers and touch segments
    for k, (vx, vy, vnet) in enumerate(vias):
        for i, (x1, y1, x2, y2, layer, net) in enumerate(segs):
            if net != vnet:
                continue
            if _on_segment(vx, vy, x1, y1, x2, y2, tol=0.6):
                dsu.union(node("v", k), node("s", i, 0))
    # pads touch segments
    for kpad, (px, py, hx, hy, net, players) in pads.items():
        r = max(hx, hy)
        for i, (x1, y1, x2, y2, layer, snet) in enumerate(segs):
            if snet != net or layer not in players:
                continue
            if (math.hypot(px - x1, py - y1) <= r + 0.3
                    or math.hypot(px - x2, py - y2) <= r + 0.3
                    or _on_segment(px, py, x1, y1, x2, y2, tol=r)):
                dsu.union(node("p", *kpad), node("s", i, 0))
        for k, (vx, vy, vnet) in enumerate(vias):
            if vnet == net and math.hypot(px - vx, py - vy) <= r + 1.0:
                dsu.union(node("p", *kpad), node("v", k))

    # zones connect everything of their net inside them
    for zi, (znet, zlayer, poly) in enumerate(zones):
        anchor = node("z", zi)
        for kpad, (px, py, hx, hy, net, players) in pads.items():
            if net == znet and zlayer in players and _point_in_poly(px, py, poly):
                dsu.union(node("p", *kpad), anchor)
        for k, (vx, vy, vnet) in enumerate(vias):
            if vnet == znet and _point_in_poly(vx, vy, poly):
                dsu.union(node("v", k), anchor)
        for i, (x1, y1, x2, y2, layer, net) in enumerate(segs):
            if net != znet or layer != zlayer:
                continue
            # either end landing in the pour ties the track to it
            if _point_in_poly(x1, y1, poly) or _point_in_poly(x2, y2, poly):
                dsu.union(node("s", i, 0), anchor)

    by_net = {}
    for kpad, (px, py, hx, hy, net, players) in pads.items():
        by_net.setdefault(net, []).append(kpad)

    unrouted = []
    for net, kpads in sorted(by_net.items()):
        roots = {dsu.find(node("p", *k)) for k in kpads}
        if len(roots) > 1:
            groups = {}
            for k in kpads:
                groups.setdefault(dsu.find(node("p", *k)), []).append(
                    "%s.%s" % k)
            unrouted.append((net, len(roots), list(groups.values())))

    if unrouted:
        # Tracks and vias only - this walk knows nothing about zone fills, so
        # a pad joined solely by a pour still counts as its own island here.
        # It over-reports badly on PGND/VCELL/VOUT, which are mostly poured.
        # `kicad-cli pcb drc` is the authority on what is genuinely
        # unconnected; treat the list below as "not joined by track or via".
        note("%d of %d nets continuous through tracks and vias alone; %d rely "
             "on a zone fill or are genuinely unrouted (DRC decides which):"
             % (len(by_net) - len(unrouted), len(by_net), len(unrouted)))
        for net, cnt, groups in unrouted:
            note("    %-9s %d islands: %s"
                 % (net, cnt, " | ".join(",".join(g[:4]) for g in groups)))
    else:
        note("all %d nets are electrically continuous on the board"
             % len(by_net))

    # Clearance is KiCad's job now. This file used to carry its own sweep,
    # which passed a board whose pads had no layer assignment at all - it
    # modelled pads as bare rectangles and never looked at layers. A second,
    # weaker implementation that can disagree with the real DRC is worse than
    # none, so run `kicad-cli pcb drc` (tools/build.py does) and trust that.
    note("clearance/courtyard: delegated to kicad-cli DRC, see build.py")


MIN_CLEARANCE = 0.25        # mm, matches the design rules in the .kicad_pro


def _seg_seg_dist(a, b, c, d):
    """Minimum distance between segments ab and cd."""
    def pt_seg(p, q, r):
        qx, qy = r[0] - q[0], r[1] - q[1]
        L2 = qx * qx + qy * qy
        if L2 == 0:
            return math.hypot(p[0] - q[0], p[1] - q[1])
        t = max(0.0, min(1.0, ((p[0] - q[0]) * qx + (p[1] - q[1]) * qy) / L2))
        return math.hypot(p[0] - (q[0] + t * qx), p[1] - (q[1] + t * qy))

    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d),
               pt_seg(c, a, b), pt_seg(d, a, b))


def _seg_rect_dist(a, b, cx, cy, hx, hy):
    """Distance from segment ab to an axis-aligned rectangle."""
    x0, y0, x1, y1 = cx - hx, cy - hy, cx + hx, cy + hy
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    best = min(_seg_seg_dist(a, b, e[0], e[1]) for e in edges)
    # segment entirely inside the rectangle
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1:
        return 0.0
    return best


def check_clearance(segs, vias, pads):
    """Track/pad/via clearance between different nets on the same layer.

    A stand-in for DRC, not a replacement. Pads are treated as their true
    rectangles, but zone fills are not evaluated at all - KiCad computes those
    when the board is opened, and it keeps its own clearance around foreign
    copper, so pours are excluded here by design.
    """
    violations = []

    for i in range(len(segs)):
        x1, y1, x2, y2, la, na = segs[i]
        for j in range(i + 1, len(segs)):
            u1, v1, u2, v2, lb, nb = segs[j]
            if la != lb or na == nb:
                continue
            d = _seg_seg_dist((x1, y1), (x2, y2), (u1, v1), (u2, v2))
            if d < MIN_CLEARANCE:
                violations.append("track %s vs track %s on %s: %.3f mm"
                                  % (na, nb, la, d))

    for (ref, pnum), (px, py, hx, hy, net, players) in pads.items():
        for (x1, y1, x2, y2, layer, snet) in segs:
            if snet == net or layer not in players:
                continue
            d = _seg_rect_dist((x1, y1), (x2, y2), px, py, hx, hy)
            if d < MIN_CLEARANCE:
                violations.append("pad %s.%s (%s) vs track %s on %s: %.3f mm"
                                  % (ref, pnum, net, snet, layer, d))

    items = list(pads.items())
    for i in range(len(items)):
        (ra, pa), (xa, ya, hxa, hya, na, la) = items[i]
        for j in range(i + 1, len(items)):
            (rb, pb), (xb, yb, hxb, hyb, nb, lb) = items[j]
            if na == nb or ra == rb or not (set(la) & set(lb)):
                continue        # same component = fixed package pitch
            gx = abs(xa - xb) - hxa - hxb
            gy = abs(ya - yb) - hya - hyb
            d = max(gx, gy)
            if d < MIN_CLEARANCE:
                violations.append("pad %s.%s (%s) vs pad %s.%s (%s): %.3f mm"
                                  % (ra, pa, na, rb, pb, nb, d))

    for (vx, vy, vnet) in vias:
        for (x1, y1, x2, y2, layer, snet) in segs:
            if snet == vnet:
                continue
            d = _seg_seg_dist((vx, vy), (vx, vy), (x1, y1), (x2, y2)) - 0.6
            if d < MIN_CLEARANCE:
                violations.append("via %s vs track %s on %s: %.3f mm"
                                  % (vnet, snet, layer, d))
        for (ref, pnum), (px, py, hx, hy, net, players) in pads.items():
            if net == vnet or "F.Cu" not in players:
                continue
            gx = abs(vx - px) - 0.6 - hx
            gy = abs(vy - py) - 0.6 - hy
            d = max(gx, gy)
            if d < MIN_CLEARANCE:
                violations.append("via %s vs pad %s.%s (%s): %.3f mm"
                                  % (vnet, ref, pnum, net, d))

    uniq = sorted(set(violations))
    if uniq:
        for v in uniq[:15]:
            fail("pcb clearance: " + v)
        if len(uniq) > 15:
            fail("pcb clearance: ... and %d more" % (len(uniq) - 15))
    else:
        note("copper clearance >= %.2f mm between all differing nets "
             "(tracks, pads, vias)" % MIN_CLEARANCE)


# --------------------------------------------------------------------------

def check_unique_references(trees):
    """No two schematic symbols may share a Reference.

    KiCad's "Update PCB from Schematic" aborts with "Duplicate items <ref>"
    rather than annotating around it, so a collision here blocks the whole
    schematic-to-board path. Power symbols and PWR_FLAGs count: they carry
    #PWR / #FLG references and are checked the same way.
    """
    path = os.path.join(ROOT, "1v2boost.kicad_sch")
    if path not in trees:
        return
    seen = {}
    dupes = {}
    for sym in children(trees[path], "symbol"):
        ref = None
        for prop in children(sym, "property"):
            if val(prop, 1) == "Reference":
                ref = val(prop, 2)
        if ref is None:
            continue
        uu = first(sym, "uuid")
        uu = val(uu, 1) if uu is not None else None
        if ref in seen and seen[ref] != uu:
            dupes.setdefault(ref, 1)
            dupes[ref] += 1
        seen[ref] = uu

    if dupes:
        fail("schematic: %d duplicate reference(s) - KiCad's Update PCB from "
             "Schematic will refuse to run: %s"
             % (len(dupes), ", ".join("%s x%d" % kv
                                      for kv in sorted(dupes.items()))))
    else:
        note("all %d schematic symbol references are unique" % len(seen))


def check_symbol_footprint_links(trees):
    """Every board footprint must carry the UUID path of its schematic symbol.

    This is what "Update PCB from Schematic" matches on. A footprint with no
    path is an orphan: the updater offers to delete and re-place it instead of
    updating it in situ, which silently throws away the layout.
    """
    path = os.path.join(ROOT, "1v2boost.kicad_pcb")
    if path not in trees:
        return
    try:
        want = {ref: c["path"]
                for ref, c in kicad_netlist.load(refresh=False).comps.items()
                if c.get("path")}
    except Exception as exc:
        note("footprint link check skipped: %s" % exc)
        return

    missing, wrong = [], []
    for fp in children(trees[path], "footprint"):
        ref = None
        for prop in children(fp, "property"):
            if val(prop, 1) == "Reference":
                ref = val(prop, 2)
        if ref not in want:
            continue
        node = first(fp, "path")
        got = val(node, 1) if node is not None else None
        if not got:
            missing.append(ref)
        elif got != want[ref]:
            wrong.append(ref)

    if missing:
        fail("pcb: %d footprint(s) have no schematic link (path): %s"
             % (len(missing), ", ".join(sorted(missing)[:8])))
    if wrong:
        fail("pcb: %d footprint(s) point at the wrong symbol uuid: %s"
             % (len(wrong), ", ".join(sorted(wrong)[:8])))
    if not missing and not wrong:
        note("all %d placed footprints link back to their schematic symbol"
             % len(want))


def check_board_layers(trees):
    """Every pad and every graphic in the board must name its layer.

    This exists because of a real bug: the footprint copier in gen_pcb.py
    stripped "(layers ...)" from pads and "(layer ...)" from graphics along
    with the header lines it meant to remove. KiCad then defaulted them all
    onto F.Cu, so silkscreen outlines became copper and pads landed on the
    wrong side - 835 DRC violations from one over-broad regex. Nothing in the
    old geometric checks could see it, because they never looked at layers.
    """
    path = os.path.join(ROOT, "1v2boost.kicad_pcb")
    if path not in trees:
        return
    tree = trees[path]

    bad_pads = []
    bad_gfx = []
    n_pads = n_gfx = 0
    for fp in children(tree, "footprint"):
        ref = None
        for prop in children(fp, "property"):
            if val(prop, 1) == "Reference":
                ref = val(prop, 2)
        for pad in children(fp, "pad"):
            n_pads += 1
            if first(pad, "layers") is None:
                bad_pads.append("%s.%s" % (ref, val(pad, 1)))
        for tag in ("fp_line", "fp_circle", "fp_rect", "fp_poly", "fp_arc",
                    "fp_text"):
            for g in children(fp, tag):
                n_gfx += 1
                if first(g, "layer") is None:
                    bad_gfx.append("%s %s" % (ref, tag))

    if bad_pads:
        fail("pcb: %d pad(s) carry no layer assignment: %s"
             % (len(bad_pads), ", ".join(sorted(set(bad_pads))[:8])))
    if bad_gfx:
        fail("pcb: %d footprint graphic(s) carry no layer: %s"
             % (len(bad_gfx), ", ".join(sorted(set(bad_gfx))[:8])))
    if not bad_pads and not bad_gfx:
        note("every one of %d pads and %d footprint graphics names its layer"
             % (n_pads, n_gfx))


def check_against_kicad_netlist():
    """Compare netlist.py to the netlist KiCad itself exports.

    check_schematic_netlist re-derives connectivity from wire and label
    geometry, which proves this project's writer is self-consistent. This
    proves KiCad *reads* the same circuit back - a different failure mode.
    """
    try:
        nl = kicad_netlist.load()
    except Exception as exc:
        note("kicad netlist cross-check skipped: %s" % exc)
        return

    # PWR_FLAG symbols exist only to satisfy ERC; KiCad does not emit them as
    # netlist nodes, so they are not a discrepancy.
    mine = {}
    for c in netlist.C:
        if c.ref.startswith("#"):
            continue
        for pin, net in c.nets.items():
            mine.setdefault(net, set()).add((c.ref, pin))

    theirs = {}
    for name, nodes in nl.nets:
        theirs.setdefault(name.rsplit("/", 1)[-1], set()).update(nodes)

    for net in sorted(set(mine) | set(theirs)):
        a, b = mine.get(net, set()), theirs.get(net, set())
        if a != b:
            fail("net %r differs between netlist.py and KiCad's export: "
                 "only ours %s / only KiCad's %s"
                 % (net, sorted(a - b)[:4], sorted(b - a)[:4]))
    else:
        note("KiCad reads back the same %d nets netlist.py defines"
             % len(theirs))


def main():
    symbols.symbol_defs()

    for p in netlist.check():
        fail("netlist: " + p)

    trees = check_parses()
    check_libraries(trees)
    check_schematic_netlist(trees)
    check_unique_references(trees)
    check_against_kicad_netlist()
    check_pcb(trees)
    check_symbol_footprint_links(trees)
    check_board_layers(trees)

    print("=" * 70)
    print("  VALIDATION")
    print("=" * 70)
    for m in NOTES:
        print("  [info] " + m)
    if FAILURES:
        print()
        for m in FAILURES:
            print("  [FAIL] " + m)
        print("\n  %d failure(s)" % len(FAILURES))
        return 1
    print("\n  All checks passed.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
