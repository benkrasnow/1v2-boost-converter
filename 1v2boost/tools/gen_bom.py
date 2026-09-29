"""
Emits bom/1v2boost-bom.csv, grouped by identical part.

Generated straight from netlist.py, so the BOM cannot drift away from the
schematic. Test points, mounting holes, fiducials and ERC power flags are
excluded (in_bom = False).
"""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import netlist                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Notes that matter when ordering or substituting.
NOTES = {
    "L1": "Land pattern per Pulse datasheet P716.D: pads 1/2 4.00x5.00 mm on "
          "14.50 mm centres, pad 3 4.00x4.00 mm at 17.50 mm. Pad 3 is a "
          "mounting tab - leave it unconnected. The core is conductive, so no "
          "exposed via may sit under the body (P716 note 10). DCR 1.4 mohm "
          "max, Isat 52 A, Irms 40 A. Dominant single loss in the design.",
    "R2": "TWO-terminal part - the 4 pads are 2 terminals, tied in pairs "
          "(1+3, 2+4) per Bourns' own land pattern, where a 0.40 mm sense "
          "finger leaves each solder pad. Take ISNS_K and AGND at the finger "
          "tips, not the solder pads. Value is capped at 1.73 mohm by the "
          "slope compensation requirement (TPS40210 eq 19), which rules out "
          "every stocked value above 1 mohm. Raises the OC trip to 150 A.",
    "Q1": "Two in parallel. Logic level, Rds(on) 1.49 mohm typ at Vgs=10 V, "
          "Qgd 6.7 nC. Bolt both to a common heatsink - tabs are the SW node, "
          "so isolate from D1.",
    "Q2": "See Q1.",
    "D1": "Both halves paralleled. Tab is the cathode (VOUT), so it needs an "
          "insulating pad if it shares a heatsink with Q1/Q2.",
    "U1": "Controller. Chosen over LM5155 because it has no hard duty-cycle "
          "limit, only a 200 ns minimum off-time.",
    "U2": "Mandatory, not optional: U1's own gate driver is only 400 mA and "
          "would cost ~2.2 W in switching loss driving 260 nC of gate charge.",
    "U3": "Cell undervoltage lockout - nothing in U1 watches the cell. Trips "
          "at 0.837 V, releases at 1.045 V. Max supply 5.5 V, hence the local "
          "zener rail from R12/D4.",
    "D4": "Creates the 5.1 V rail for U3 from the 12 V bias.",
    "R15": "UVLO hysteresis, and it sets BOTH thresholds - do not change it "
           "without re-running design.py uvlo_thresholds(). 207 mV must stay "
           "above the cell's I*R recovery step (~117 mV at 4 mohm).",
    "R7": "Sets 100 kHz with C24 = 330 pF. R*C*f ~= 18.0.",
    "R3": "Snubber. Tune on the bench - TO-220 lead inductance makes this "
          "necessary rather than optional.",
    "C12": "Snubber. Tune with R3.",
    "R1": "Damps the input filter against a low-impedance cell on long leads.",
    "JP1": "Do not fit. Fit only to bypass the cell UVLO and force enable.",
}

# The old hand-maintained DISTRIBUTOR_HINT table is gone. It listed seven
# Digi-Key numbers, four of which were wrong and three of which pointed at
# parts that do not exist. Distributor numbers now come from netlist.py's `dk`
# field, so there is one place to check them and it is the same place the
# schematic reads.

HEADER = ["Item", "Qty", "References", "Value", "Footprint", "MPN",
          "Manufacturer", "Description", "Digi-Key P/N", "Fit", "Notes"]


def build_rows():
    groups = {}
    order = []
    for c in netlist.C:
        if not c.in_bom or not c.footprint:
            continue
        key = (c.value, c.footprint, c.mpn, c.mfr, c.dk, c.dnp)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    rows = []
    for i, key in enumerate(order, start=1):
        comps = groups[key]
        value, footprint, mpn, mfr, dk, dnp = key
        refs = sorted(comps, key=lambda c: (c.ref[0], int("".join(
            ch for ch in c.ref if ch.isdigit()) or 0)))
        note = ""
        for c in comps:
            if c.ref in NOTES:
                note = NOTES[c.ref]
                break
        rows.append([
            i,
            len(comps),
            " ".join(c.ref for c in refs),
            value,
            footprint,
            mpn,
            mfr,
            comps[0].descr,
            dk,
            "DNP" if dnp else "Fit",
            note,
        ])
    return rows


def main():
    rows = build_rows()
    outdir = os.path.join(ROOT, "bom")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "1v2boost-bom.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)

    fitted = sum(r[1] for r in rows if r[9] == "Fit")
    lines = sum(1 for r in rows if r[9] == "Fit")
    print("wrote %s" % os.path.relpath(path, ROOT))
    print("  %d line items, %d fitted parts (+%d DNP)"
          % (len(rows), fitted, sum(r[1] for r in rows if r[9] == "DNP")))
    print("  %d distinct fitted line items" % lines)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
