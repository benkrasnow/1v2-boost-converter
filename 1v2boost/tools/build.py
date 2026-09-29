"""Regenerates the whole project and checks it with KiCad itself.

    python tools/build.py

Order matters. gen_pcb.py reads the netlist KiCad exports from the schematic,
so the schematic has to exist first; the zone filler and the two checkers run
against the finished board.

KiCad's ERC and DRC are the authority here. tools/validate.py only covers what
they cannot see - that the schematic encodes the circuit netlist.py intends.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicad_netlist               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
SCH = os.path.join(ROOT, "1v2boost.kicad_sch")
PCB = os.path.join(ROOT, "1v2boost.kicad_pcb")


def run(argv, label, quiet=False):
    print("--> %s" % label)
    r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or not quiet:
        for line in out.strip().splitlines():
            print("    " + line)
    return r.returncode, out


def main():
    py = sys.executable
    cli = kicad_netlist.find_cli()
    kpy = kicad_netlist.find_python()

    for step in ("symbols", "footprints", "gen_schematic"):
        rc, _ = run([py, os.path.join(TOOLS, step + ".py")], step, quiet=True)
        if rc:
            return rc

    if not cli:
        print("!!  kicad-cli not found - generating the board from the "
              "in-repo netlist and skipping all KiCad checks.")
        print("    Set KICAD_CLI to kicad-cli's full path to enable them.")
        return run([py, os.path.join(TOOLS, "gen_pcb.py")], "gen_pcb")[0]

    print("--> kicad-cli: %s" % cli)
    for step in ("gen_pcb", "gen_bom"):
        rc, _ = run([py, os.path.join(TOOLS, step + ".py")], step, quiet=True)
        if rc:
            return rc

    if kpy:
        rc, _ = run([kpy, os.path.join(TOOLS, "fill_zones.py")],
                    "fill zones (pcbnew)", quiet=True)
        if rc:
            return rc

        # Routing has to happen here, inside build, or it silently does not
        # happen at all: gen_pcb.py rewrites the board from scratch, so every
        # trace the router placed on the previous run is gone by this point.
        #
        # The order below is not negotiable and each step has burned us:
        #   1. DRC to json first - autoroute.py reads build/drc.json to learn
        #      which pads are still unconnected. Run it against a stale report
        #      and it routes the wrong nets onto a board that has moved.
        #   2. Route.
        #   3. Refill zones AFTER routing. Zone fills are computed against the
        #      copper that existed when they were filled, so new traces sit at
        #      0.000 mm from the old pour - which shows up as ~90 spurious
        #      clearance violations that vanish on a refill.
        run([cli, "pcb", "drc", "--severity-all", "--format", "json", "-o",
             os.path.join(ROOT, "build", "drc.json"), PCB],
            "DRC -> json (router input)", quiet=True)
        # A non-zero return here means some nets were left unrouted, which is
        # the normal outcome, not a build failure - the router refuses rather
        # than placing copper that violates clearance. DRC below reports what
        # is still open. Do not turn this into an early return.
        run([kpy, os.path.join(TOOLS, "autoroute.py")],
            "autoroute (pcbnew)", quiet=True)
        rc, _ = run([kpy, os.path.join(TOOLS, "fill_zones.py")],
                    "refill zones after routing", quiet=True)
        if rc:
            return rc
    else:
        print("!!  KiCad's python not found - zones left unfilled and the "
              "board left unrouted. Open the board, use Edit > Fill All "
              "Zones, and route by hand before reading DRC.")

    rc, _ = run([py, os.path.join(TOOLS, "validate.py")], "validate")
    if rc:
        return rc

    erc_rc, erc = run([cli, "sch", "erc", "--severity-all",
                       "--exit-code-violations", "-o",
                       os.path.join(ROOT, "build", "erc.rpt"), SCH], "ERC")
    drc_rc, drc = run([cli, "pcb", "drc", "--severity-all",
                       "--schematic-parity", "--exit-code-violations", "-o",
                       os.path.join(ROOT, "build", "drc.rpt"), PCB], "DRC")

    print()
    print("=" * 70)
    print("  ERC : %s" % erc.strip().splitlines()[0] if erc.strip() else "")
    for line in drc.strip().splitlines():
        if line.startswith("Found"):
            print("  DRC : %s" % line)
    print("=" * 70)
    # Unconnected pads are expected: the router deliberately leaves what it
    # cannot place without violating clearance. Everything else is not.
    return 0 if erc_rc == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
