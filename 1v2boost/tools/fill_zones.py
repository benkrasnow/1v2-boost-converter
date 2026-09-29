"""Fills the board's copper zones using KiCad's own zone filler.

Run with KiCad's bundled Python, which is the only interpreter that has the
pcbnew module:

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/fill_zones.py

tools/build.py does this for you.

The generator emits zone outlines but no filled polygons. An unfilled zone
connects nothing, so DRC reports every stitching via as dangling and counts
pads that the pour would have joined as unconnected. Filling is deliberately
left to KiCad rather than reimplemented here: the fill has to honour thermal
reliefs, clearances, island removal and min-thickness, and a hand-rolled
polygon fill that disagreed with KiCad's would be worse than none at all.
"""

import os
import sys

try:
    import pcbnew
except ImportError:
    sys.exit("no pcbnew module - run this with KiCad's bundled python.exe")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    path = os.path.join(ROOT, "1v2boost.kicad_pcb")
    board = pcbnew.LoadBoard(path)

    zones = list(board.Zones())
    if not zones:
        print("no zones on the board")
        return 1

    filler = pcbnew.ZONE_FILLER(board)
    ok = filler.Fill(board.Zones())

    filled = sum(1 for z in zones if z.IsFilled())
    print("zone fill %s: %d of %d zones filled"
          % ("ok" if ok else "reported a problem", filled, len(zones)))
    for z in zones:
        print("  %-8s %-5s %-9s area %8.1f mm2"
              % (z.GetNetname() or "<none>",
                 board.GetLayerName(z.GetLayer()),
                 "filled" if z.IsFilled() else "EMPTY",
                 z.GetFilledArea() / 1e12))

    board.Save(path)
    print("saved %s" % os.path.relpath(path, ROOT))
    return 0 if (ok and filled == len(zones)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
