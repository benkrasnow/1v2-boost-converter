"""Maze router. Finishes whatever gen_pcb.py's greedy pass could not.

Run with KiCad's bundled Python (needs pcbnew and numpy):

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/autoroute.py

tools/build.py does this for you.

gen_pcb.py routes from a hand-written table of waypoints and rejects anything
that violates clearance. That is right for the power stage, where the path is
a design decision. It cannot find its way *around* an obstacle though, so
about thirty small-signal connections were left as ratsnest simply because the
straight run clipped a neighbouring pin. This finds a path if one exists.

It works on the real board through pcbnew, so pad shapes, netclass widths and
clearances are KiCad's rather than a second implementation of them.

Zone fills are not obstacles - zones are refilled afterwards and carve
themselves back around new copper. They *are* valid destinations, tested
lazily with HitTestFilledArea on the cells the search actually reaches, which
is a few thousand rather than the whole board.
"""

import json
import os
import sys
from collections import deque

try:
    import pcbnew
    import numpy as np
except ImportError as exc:
    sys.exit("needs KiCad's bundled python (pcbnew + numpy): %s" % exc)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(ROOT, "1v2boost.kicad_pcb")
DRCJSON = os.path.join(ROOT, "build", "drc.json")

GRID = 0.2              # mm per cell
QUANT = 0.15            # mm of slack for grid quantisation
VIA_COST = 60           # in cells - layer changes should be worth it
POWER_CLEARANCE = 0.3   # mm, the widest netclass clearance on this board
REPAIR_WIDTH = 0.25     # mm, signal-weight copper for these repairs
VIA_DIA = 0.8           # mm, matches the vias gen_pcb.py places

FREE, BLOCKED = 0, -1
LAYERS = None           # filled in at run time


def mm(v):
    return v / 1e6


def nm(v):
    return int(round(v * 1e6))


class Grid:
    """Per-layer occupancy. cell value: 0 free, >0 that netcode, -1 unusable."""

    def __init__(self, board):
        box = board.GetBoardEdgesBoundingBox()
        self.x0 = mm(box.GetLeft()) - 1.0
        self.y0 = mm(box.GetTop()) - 1.0
        self.w = int((mm(box.GetWidth()) + 2.0) / GRID) + 1
        self.h = int((mm(box.GetHeight()) + 2.0) / GRID) + 1
        self.occ = [np.zeros((self.h, self.w), dtype=np.int32),
                    np.zeros((self.h, self.w), dtype=np.int32)]
        self.own = [np.zeros((self.h, self.w), dtype=np.int32),
                    np.zeros((self.h, self.w), dtype=np.int32)]

    def ij(self, x, y):
        return (int((y - self.y0) / GRID), int((x - self.x0) / GRID))

    def xy(self, i, j):
        return (self.x0 + (j + 0.5) * GRID, self.y0 + (i + 0.5) * GRID)

    def box(self, x1, y1, x2, y2, grow):
        i1 = max(0, int((y1 - grow - self.y0) / GRID))
        j1 = max(0, int((x1 - grow - self.x0) / GRID))
        i2 = min(self.h - 1, int((y2 + grow - self.y0) / GRID))
        j2 = min(self.w - 1, int((x2 + grow - self.x0) / GRID))
        if i2 < i1 or j2 < j1:
            return None
        return (slice(i1, i2 + 1), slice(j1, j2 + 1))

    def add(self, li, x1, y1, x2, y2, net, grow):
        sl = self.box(x1, y1, x2, y2, grow)
        if sl is not None:
            cur = self.occ[li][sl]
            self.occ[li][sl] = np.where(
                cur == FREE, net, np.where(cur == net, net, BLOCKED))
        if net > 0:
            sl = self.box(x1, y1, x2, y2, 0.0)
            if sl is not None:
                self.own[li][sl] = net


def item_box(obj):
    b = obj.GetBoundingBox()
    return mm(b.GetLeft()), mm(b.GetTop()), mm(b.GetRight()), mm(b.GetBottom())


def build_grid(board, clearance, halfwidth):
    g = Grid(board)
    grow = clearance + halfwidth + QUANT

    for fp in board.GetFootprints():
        for pad in fp.Pads():
            code = pad.GetNetCode()
            x1, y1, x2, y2 = item_box(pad)
            for li, layer in enumerate(LAYERS):
                if pad.IsOnLayer(layer):
                    g.add(li, x1, y1, x2, y2, code if code else BLOCKED, grow)

    for t in board.GetTracks():
        code = t.GetNetCode()
        x1, y1, x2, y2 = item_box(t)
        if t.Type() == pcbnew.PCB_VIA_T:
            for li in range(2):
                g.add(li, x1, y1, x2, y2, code, grow)
        else:
            for li, layer in enumerate(LAYERS):
                if t.IsOnLayer(layer):
                    g.add(li, x1, y1, x2, y2, code, grow)
    return g


def zone_hit(zones, li, x, y):
    p = pcbnew.VECTOR2I(nm(x), nm(y))
    for z in zones:
        if z.IsOnLayer(LAYERS[li]) and z.HitTestFilledArea(LAYERS[li], p, 0):
            return True
    return False


def dilate(mask, k):
    """Separable binary dilation by k cells."""
    out = mask.copy()
    for d in range(1, k + 1):
        out |= np.roll(mask, d, axis=0) | np.roll(mask, -d, axis=0)
    tmp = out.copy()
    for d in range(1, k + 1):
        out |= np.roll(tmp, d, axis=1) | np.roll(tmp, -d, axis=1)
    return out


def via_map(g, code):
    """Cells where a via may legally be dropped.

    A via is much bigger than the track that leads to it - 0.8 mm pad against
    0.25 mm of copper - so the clearance reserved for routing is not enough to
    place one. Reserving only track clearance is what produced shorts,
    hole-clearance and solder-mask-bridge violations on the first pass. Here
    the obstacle mask is grown by the via's own radius plus clearance, on both
    layers at once, since a via occupies both.
    """
    bad = np.zeros((g.h, g.w), dtype=bool)
    for li in range(2):
        occ = g.occ[li]
        bad |= (occ != FREE) & (occ != code)
    k = int((VIA_DIA / 2.0 + POWER_CLEARANCE + QUANT) / GRID) + 1
    return ~dilate(bad, k)


def search(g, board, code, start_cells, zones, vok):
    """BFS from a pad's cells to any other copper of the same net."""
    h, w = g.h, g.w
    dist = {}
    prev = {}
    q = deque()
    for (li, i, j) in start_cells:
        dist[(li, i, j)] = 0
        q.append((li, i, j))

    goal = None
    while q:
        li, i, j = q.popleft()
        d = dist[(li, i, j)]
        # Layer changes are allowed but expensive, and only where a full
        # via footprint fits. B.Cu is the solid PGND return plane for a 28 A
        # switching loop, so VIA_COST is set high enough that signals only
        # dive under when there is no way across on the front.
        for (nli, ni, nj, step) in (
                (li, i + 1, j, 1), (li, i - 1, j, 1),
                (li, i, j + 1, 1), (li, i, j - 1, 1),
                (1 - li, i, j, VIA_COST)):
            if not (0 <= ni < h and 0 <= nj < w):
                continue
            if nli != li and not vok[i, j]:
                continue
            key = (nli, ni, nj)
            if key in dist:
                continue
            occ = g.occ[nli][ni, nj]
            if occ != FREE and occ != code:
                continue
            dist[key] = d + step
            prev[key] = (li, i, j)
            if g.own[nli][ni, nj] == code:
                goal = key
                q.clear()
                break
            x, y = g.xy(ni, nj)
            if zones and zone_hit(zones, nli, x, y):
                goal = key
                q.clear()
                break
            q.append(key)
        if goal:
            break

    if goal is None:
        return None
    path = [goal]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def snap_target(board, code, x, y):
    """Exact point on same-net copper nearest the cell the search stopped on.

    The search works on a 0.2 mm grid and its goal cell only has to *overlap*
    the target's bounding box, so ending the track at the cell centre can
    leave it a fraction of a millimetre short of the actual pad - copper that
    looks connected but that KiCad correctly reports as a dangling end.
    Snapping to the real pad or track endpoint removes the whole class.
    """
    p = pcbnew.VECTOR2I(nm(x), nm(y))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() == code and pad.HitTest(p, 0):
                c = pad.GetPosition()
                return mm(c.x), mm(c.y)
    best, bestd = None, 1e9
    for t in board.GetTracks():
        if t.GetNetCode() != code:
            continue
        for e in (t.GetStart(), t.GetEnd()):
            d = (mm(e.x) - x) ** 2 + (mm(e.y) - y) ** 2
            if d < bestd:
                best, bestd = (mm(e.x), mm(e.y)), d
    if best and bestd < (3.0 ** 2):
        return best
    return None


def emit(board, g, code, path, width, head=None, tail=None):
    """Turn a cell path into merged track segments and vias."""
    runs = []
    cur = [path[0]]
    for a, b in zip(path, path[1:]):
        if a[0] != b[0]:
            runs.append(("seg", cur))
            runs.append(("via", b))
            cur = [b]
        else:
            cur.append(b)
    runs.append(("seg", cur))

    added = 0
    for kind, data in runs:
        if kind == "via":
            li, i, j = data
            x, y = g.xy(i, j)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(nm(x), nm(y)))
            v.SetWidth(nm(VIA_DIA))
            v.SetDrill(nm(0.4))
            v.SetNetCode(code)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            board.Add(v)
            added += 1
            continue

        cells = data
        if len(cells) < 2:
            continue
        # collapse collinear cells into single segments
        pts = [cells[0]]
        for k in range(1, len(cells) - 1):
            p, c, n = cells[k - 1], cells[k], cells[k + 1]
            if not ((p[1] == c[1] == n[1]) or (p[2] == c[2] == n[2])):
                pts.append(c)
        pts.append(cells[-1])
        coords = [g.xy(c[1], c[2]) for c in pts]
        if head is not None and kind == "seg" and data is runs[0][1]:
            coords[0] = head
        if tail is not None and kind == "seg" and data is runs[-1][1]:
            coords[-1] = tail
        for (xa, ya), (xb, yb) in zip(coords, coords[1:]):
            a = pts[0]
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(nm(xa), nm(ya)))
            t.SetEnd(pcbnew.VECTOR2I(nm(xb), nm(yb)))
            t.SetWidth(nm(width))
            t.SetLayer(LAYERS[a[0]])
            t.SetNetCode(code)
            board.Add(t)
            added += 1
    return added


def unconnected_pads(board):
    """Pads KiCad currently reports as unconnected, from the DRC json."""
    if not os.path.isfile(DRCJSON):
        sys.exit("run kicad-cli pcb drc --format json -o build/drc.json first")
    with open(DRCJSON, encoding="utf-8") as fh:
        data = json.load(fh)

    wanted = set()
    for v in data.get("unconnected_items", []):
        for it in v.get("items", []):
            p = it.get("pos") or {}
            wanted.add((round(p.get("x", 0), 3), round(p.get("y", 0), 3)))

    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if not pad.GetNetCode():
                continue
            p = pad.GetPosition()
            if (round(mm(p.x), 3), round(mm(p.y), 3)) in wanted:
                out.append((fp.GetReference(), pad, pad.GetNetCode()))
    return out


def main():
    global LAYERS
    board = pcbnew.LoadBoard(BOARD)
    LAYERS = [pcbnew.F_Cu, pcbnew.B_Cu]
    zones_by_net = {}
    for z in board.Zones():
        zones_by_net.setdefault(z.GetNetCode(), []).append(z)

    todo = unconnected_pads(board)
    print("%d pad(s) reported unconnected" % len(todo))

    routed = failed = 0
    for ref, pad, code in todo:
        net = board.FindNet(code)
        # NETCLASS is opaque through SWIG, so use the project's worst case
        # (the Power netclass) for every repair. Over-clearing is safe: it
        # only ever makes the router more conservative than KiCad's DRC.
        clear, width = POWER_CLEARANCE, REPAIR_WIDTH
        g = build_grid(board, clear, width / 2.0)

        x1, y1, x2, y2 = item_box(pad)
        start = []
        for li, layer in enumerate(LAYERS):
            if not pad.IsOnLayer(layer):
                continue
            sl = g.box(x1, y1, x2, y2, 0.0)
            if sl is None:
                continue
            for i in range(sl[0].start, sl[0].stop):
                for j in range(sl[1].start, sl[1].stop):
                    start.append((li, i, j))
        if not start:
            failed += 1
            continue

        path = search(g, board, code, start, zones_by_net.get(code, []),
                      via_map(g, code))
        if path is None:
            print("   FAILED  %-6s %-9s no path" % (ref, net.GetNetname()))
            failed += 1
            continue
        gx, gy = g.xy(path[-1][1], path[-1][2])
        p0 = pad.GetPosition()
        n = emit(board, g, code, path, width,
                 head=(mm(p0.x), mm(p0.y)),
                 tail=snap_target(board, code, gx, gy))
        print("   routed  %-6s %-9s %d item(s)" % (ref, net.GetNetname(), n))
        routed += 1

    board.Save(BOARD)
    print("routed %d, failed %d" % (routed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
