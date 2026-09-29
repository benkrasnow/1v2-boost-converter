"""
Emits 1v2boost.kicad_sch, 1v2boost.kicad_pro, sym-lib-table and fp-lib-table.

Layout style
------------
Components are placed in functional blocks; every pin gets a short wire stub
ending in either a net label or a power symbol. That is a deliberate choice:
it makes the netlist provably identical to netlist.py regardless of routing
geometry, which matters because KiCad is not available here to check the
result visually. Rats-nest style wiring would look prettier but could hide a
connection error.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import netlist                     # noqa: E402
import symbols                     # noqa: E402

PROJECT = "1v2boost"
LIB = PROJECT
STUB = 2.54                        # wire stub length, mm
FONT = "(effects (font (size 1.27 1.27)))"

# Nets rendered with a power symbol instead of a text label.
POWER_SYMS = {"VCELL": "VCELL", "VOUT": "VOUT", "V12": "V12", "V5C": "V5C",
              "PGND": "PGND", "AGND": "AGND"}


def uid(*parts):
    h = hashlib.sha1(("sch|" + "|".join(str(p) for p in parts)).encode()).hexdigest()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def n(v):
    s = "%.4f" % float(v)
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

BLOCKS = [
    ("Cell input, bulk and damping",
     ["J1", "C1", "C2", "C3", "C4", "C5", "C6",
      "C7", "C8", "C9", "R1"], 11),
    ("Power stage",
     ["L1", "Q1", "Q2", "R2", "D1", "R3", "C12"], 7),
    ("Output filter and terminals",
     ["C13", "C14", "C15", "C16", "C17", "C18", "C19", "C20", "J2"], 9),
    ("Gate driver",
     ["U2", "R4", "R5", "R6", "C22", "C23"], 6),
    ("Controller TPS40210",
     ["U1", "R7", "C24", "C25", "C26", "R8", "R9", "R10", "C27", "C28",
      "R11", "C29"], 12),
    ("Auxiliary 12 V bias input",
     ["J3", "D3", "C30", "C31"], 4),
    ("Cell undervoltage lockout",
     ["R12", "D4", "C32", "U3", "R13", "R14", "R15", "R16", "R17",
      "C33", "C34", "JP1"], 12),
    ("Test points",
     ["TP%d" % i for i in range(1, 13)], 12),
    ("Mechanical and ERC power flags",
     ["H1", "H2", "H3", "H4", "FID1", "FID2"] +
     ["#FLG%d" % i for i in range(1, 8)], 13),
]

COL_PITCH = 33.02        # 1.3 in
ROW_PITCH = 44.45        # 1.75 in
WIDE = {"U1": 3, "U2": 2, "U3": 2, "D1": 2, "R2": 2, "Q1": 2, "Q2": 2,
        "J1": 2, "J2": 2, "J3": 1, "L1": 1}
ORIGIN_X = 38.1
ORIGIN_Y = 38.1


def place():
    by_ref = {c.ref: c for c in netlist.C}
    y = ORIGIN_Y
    headers = []
    for title, refs, _ in BLOCKS:
        headers.append((ORIGIN_X - 12.7, y - 17.78, title))
        col = 0
        for ref in refs:
            comp = by_ref[ref]
            span = WIDE.get(ref, 1)
            if col + span > 12:
                col = 0
                y += ROW_PITCH
                headers.append((ORIGIN_X - 12.7, y - 17.78, title + " (cont.)"))
            comp.at = (ORIGIN_X + col * COL_PITCH, y)
            col += span
        y += ROW_PITCH
    return headers, y


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------

def pin_geometry(comp, pin):
    """Absolute schematic position of a pin, plus its outward unit vector.

    KiCad mirrors Y between library and schematic space, hence the sign flip.
    """
    px, py, angle = symbols.PIN_MAP[comp.symbol][pin]
    x = comp.at[0] + px
    y = comp.at[1] - py
    out = {0: (-1.0, 0.0),      # pin body extends +x, so outward is -x
           180: (1.0, 0.0),
           90: (0.0, 1.0),      # library -y == schematic +y
           270: (0.0, -1.0)}[angle]
    return x, y, out


def emit_wire(x1, y1, x2, y2, tag):
    return ('  (wire\n'
            '    (pts (xy %s %s) (xy %s %s))\n'
            '    (stroke (width 0) (type default))\n'
            '    (uuid "%s")\n'
            '  )' % (n(x1), n(y1), n(x2), n(y2), uid("w", tag)))


def emit_label(text, x, y, out, tag):
    rot = 0 if out[0] >= 0 else 180
    if out[1] > 0:
        rot = 270
    elif out[1] < 0:
        rot = 90
    just = "left" if rot in (0, 90) else "right"
    return ('  (label "%s"\n'
            '    (at %s %s %d)\n'
            '    (effects (font (size 1.27 1.27)) (justify %s bottom))\n'
            '    (uuid "%s")\n'
            '  )' % (text, n(x), n(y), rot, just, uid("l", tag)))


def power_ref(tag):
    """Reference for a power symbol: #PWR01, #PWR02, ... in a stable order.

    These used to be "#PWR%d" % (abs(hash(tag)) % 100000), which was wrong
    twice over. Python salts str.__hash__ per process, so every regeneration
    produced different references and the schematic was not reproducible; and
    ~100 symbols drawn from 100000 slots collide often enough that two of them
    shared #PWR54091, which is what made KiCad's "Update PCB from Schematic"
    stop with "Duplicate items". Sequential numbering is what KiCad's own
    annotator does and cannot collide.
    """
    if tag not in _POWER_REFS:
        _POWER_REFS[tag] = "#PWR%02d" % (len(_POWER_REFS) + 1)
    return _POWER_REFS[tag]


_POWER_REFS = {}


def emit_power(net, x, y, tag):
    ref = power_ref(tag)
    return ('  (symbol\n'
            '    (lib_id "%s:%s")\n'
            '    (at %s %s 0)\n'
            '    (unit 1)\n'
            '    (exclude_from_sim no)\n'
            '    (in_bom no)\n'
            '    (on_board yes)\n'
            '    (dnp no)\n'
            '    (uuid "%s")\n'
            '    (property "Reference" "%s"\n'
            '      (at %s %s 0)\n'
            '      (effects (font (size 1.27 1.27)) (hide yes))\n'
            '    )\n'
            '    (property "Value" "%s"\n'
            '      (at %s %s 0)\n'
            '      (effects (font (size 1.27 1.27)))\n'
            '    )\n'
            '    (pin "1"\n'
            '      (uuid "%s")\n'
            '    )\n'
            '    (instances\n'
            '      (project "%s"\n'
            '        (path "/%s"\n'
            '          (reference "%s") (unit 1)\n'
            '        )\n'
            '      )\n'
            '    )\n'
            '  )' % (LIB, POWER_SYMS[net], n(x), n(y), uid("ps", tag), ref,
                     n(x), n(y - 3.81), net, n(x), n(y + 3.81),
                     uid("psp", tag), PROJECT, ROOT_UUID, ref))


def emit_component(comp):
    props = [
        ("Reference", comp.ref, comp.at[0] + 6.35, comp.at[1] - 8.89, False),
        ("Value", comp.value, comp.at[0] + 6.35, comp.at[1] - 6.35, False),
        ("Footprint", "%s:%s" % (LIB, comp.footprint) if comp.footprint else "",
         comp.at[0], comp.at[1], True),
        ("Datasheet", "~", comp.at[0], comp.at[1], True),
        ("Description", comp.descr, comp.at[0], comp.at[1], True),
    ]
    if comp.mpn:
        props.append(("MPN", comp.mpn, comp.at[0], comp.at[1], True))
    if comp.mfr:
        props.append(("Manufacturer", comp.mfr, comp.at[0], comp.at[1], True))
    # Distributor ordering number. Named "Digi-Key P/N" rather than something
    # generic because it is specific to one distributor and specific to the
    # Cut Tape packaging - it is not a second manufacturer part number.
    if comp.dk:
        props.append(("Digi-Key P/N", comp.dk, comp.at[0], comp.at[1], True))

    lines = ['  (symbol',
             '    (lib_id "%s:%s")' % (LIB, comp.symbol),
             '    (at %s %s %d)' % (n(comp.at[0]), n(comp.at[1]), comp.rot),
             '    (unit 1)',
             '    (exclude_from_sim no)',
             '    (in_bom %s)' % ("yes" if comp.in_bom else "no"),
             '    (on_board yes)',
             '    (dnp %s)' % ("yes" if comp.dnp else "no"),
             '    (uuid "%s")' % uid("sym", comp.ref)]
    for name, val, px, py, hide in props:
        lines.append('    (property "%s" "%s"' % (name, val))
        lines.append('      (at %s %s 0)' % (n(px), n(py)))
        lines.append('      (effects (font (size 1.27 1.27))%s)'
                     % (" (hide yes)" if hide else ""))
        lines.append('    )')
    for pin in symbols.PIN_MAP.get(comp.symbol, {}):
        lines.append('    (pin "%s"' % pin)
        lines.append('      (uuid "%s")' % uid("p", comp.ref, pin))
        lines.append('    )')
    lines.append('    (instances')
    lines.append('      (project "%s"' % PROJECT)
    lines.append('        (path "/%s"' % ROOT_UUID)
    lines.append('          (reference "%s") (unit 1)' % comp.ref)
    lines.append('        )')
    lines.append('      )')
    lines.append('    )')
    lines.append('  )')
    return "\n".join(lines)


def emit_text(text, x, y, size=2.0, tag=""):
    return ('  (text "%s"\n'
            '    (at %s %s 0)\n'
            '    (effects (font (size %s %s) (bold yes)) (justify left bottom))\n'
            '    (uuid "%s")\n'
            '  )' % (text, n(x), n(y), n(size), n(size), uid("t", tag, text)))


ROOT_UUID = uid("root", PROJECT)


def build_schematic():
    _POWER_REFS.clear()
    headers, max_y = place()
    body = []

    for x, y, title in headers:
        body.append(emit_text(title, x, y, 2.5, "hdr"))

    for comp in netlist.C:
        body.append(emit_component(comp))
        for pin, net in comp.nets.items():
            x, y, out = pin_geometry(comp, pin)
            ex, ey = x + out[0] * STUB, y + out[1] * STUB
            body.append(emit_wire(x, y, ex, ey, (comp.ref, pin)))
            if net in POWER_SYMS:
                body.append(emit_power(net, ex, ey, (comp.ref, pin)))
            else:
                body.append(emit_label(net, ex, ey, out, (comp.ref, pin)))

    head = [
        '(kicad_sch',
        '  (version 20250114)',
        '  (generator "1v2boost_gen")',
        '  (generator_version "9.0")',
        '  (uuid "%s")' % ROOT_UUID,
        '  (paper "A1")',
        '  (title_block',
        '    (title "1.2 V to 12 V / 20 W Boost Converter")',
        '    (date "2026-07-24")',
        '    (rev "A")',
        '    (comment 1 "Source: electromechanical cell, 0.9-1.6 V, up to 28 A")',
        '    (comment 2 "TPS40210 boost controller + UCC27511A gate driver")',
        '    (comment 3 "Bias from separate low-current 12 V auxiliary rail")',
        '    (comment 4 "All symbols and footprints are project-local")',
        '  )',
        '  (lib_symbols',
        symbols.lib_symbols_block(LIB, "    "),
        '  )',
    ]
    tail = [
        '  (sheet_instances',
        '    (path "/"',
        '      (page "1")',
        '    )',
        '  )',
        '  (embedded_fonts no)',
        ')',
    ]
    return "\n".join(head + body + tail) + "\n"


# --------------------------------------------------------------------------
# Project and library tables
# --------------------------------------------------------------------------

# NOTE: KICAD_PRO is raw JSON, not a Python literal - it cannot carry "#"
# comments. Explanations for these values belong here, above the string.
#
# Power netclass clearance is 0.3 mm rather than the 0.4 mm first chosen.
# PGND is a Power net and it also lands on U2's SOT-23-6, whose 0.95 mm pad
# pitch leaves 0.30 mm between pins 5 and 6. That is fixed package geometry no
# layout can change, so 0.4 mm is unsatisfiable rather than strict. 0.3 mm is
# still 3x the IPC-2221 external-uncoated minimum at 12 V (0.1 mm) and above
# the 0.2 mm typical 2 oz fab minimum; the real high-current copper is
# separated by millimetres, not by this limit.

KICAD_PRO = """{
  "board": {
    "design_settings": {
      "defaults": {
        "board_outline_line_width": 0.1,
        "copper_line_width": 0.5,
        "copper_text_size_h": 1.5,
        "copper_text_size_v": 1.5,
        "copper_text_thickness": 0.3,
        "silk_line_width": 0.12,
        "silk_text_size_h": 1.0,
        "silk_text_size_v": 1.0,
        "silk_text_thickness": 0.15
      },
      "rules": {
        "min_clearance": 0.15,
        "min_copper_edge_clearance": 0.3,
        "min_hole_clearance": 0.25,
        "min_through_hole_diameter": 0.3,
        "min_track_width": 0.2,
        "min_via_annular_width": 0.13,
        "min_via_diameter": 0.6
      },
      "track_widths": [0.0, 0.3, 0.5, 1.0, 2.0, 4.0, 6.0],
      "via_dimensions": [
        {"diameter": 0.0, "drill": 0.0},
        {"diameter": 0.8, "drill": 0.4},
        {"diameter": 1.2, "drill": 0.6}
      ]
    }
  },
  "meta": {
    "filename": "1v2boost.kicad_pro",
    "version": 1
  },
  "net_settings": {
    "classes": [
      {
        "bus_width": 12.0,
        "clearance": 0.2,
        "diff_pair_gap": 0.25,
        "diff_pair_width": 0.2,
        "line_style": 0,
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
        "name": "Default",
        "pcb_color": "rgba(0, 0, 0, 0.000)",
        "schematic_color": "rgba(0, 0, 0, 0.000)",
        "track_width": 0.25,
        "via_diameter": 0.8,
        "via_drill": 0.4,
        "wire_width": 6.0
      },
      {
        "bus_width": 12.0,
        "clearance": 0.3,
        "diff_pair_gap": 0.25,
        "diff_pair_width": 0.2,
        "line_style": 0,
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
        "name": "Power",
        "pcb_color": "rgba(200, 52, 52, 0.400)",
        "schematic_color": "rgba(0, 0, 0, 0.000)",
        "track_width": 4.0,
        "via_diameter": 1.2,
        "via_drill": 0.6,
        "wire_width": 6.0
      }
    ],
    "meta": {"version": 4},
    "net_colors": null,
    "netclass_assignments": null,
    "netclass_patterns": [
      {"netclass": "Power", "pattern": "VCELL"},
      {"netclass": "Power", "pattern": "PGND"},
      {"netclass": "Power", "pattern": "SW"},
      {"netclass": "Power", "pattern": "SRC"},
      {"netclass": "Power", "pattern": "VOUT"}
    ]
  },
  "pcbnew": {
    "last_paths": {
      "gencad": "",
      "idf": "",
      "netlist": "",
      "specctra_dsn": "",
      "step": "",
      "vrml": ""
    },
    "page_layout_descr_file": ""
  },
  "schematic": {
    "legacy_lib_dir": "",
    "legacy_lib_list": []
  },
  "sheets": [["%s", "Root"]],
  "text_variables": {}
}
""" % ROOT_UUID

SYM_LIB_TABLE = """(sym_lib_table
  (version 7)
  (lib (name "1v2boost")(type "KiCad")(uri "${KIPRJMOD}/lib/1v2boost.kicad_sym")(options "")(descr "Project-local symbols for the 1.2V boost converter"))
)
"""

FP_LIB_TABLE = """(fp_lib_table
  (version 7)
  (lib (name "1v2boost")(type "KiCad")(uri "${KIPRJMOD}/lib/1v2boost.pretty")(options "")(descr "Project-local footprints for the 1.2V boost converter"))
)
"""


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    symbols.symbol_defs()

    problems = netlist.check()
    if problems:
        print("NETLIST PROBLEMS - aborting:")
        for p in problems:
            print("  - " + p)
        return 1

    files = {
        "%s.kicad_sch" % PROJECT: build_schematic(),
        "%s.kicad_pro" % PROJECT: KICAD_PRO,
        "sym-lib-table": SYM_LIB_TABLE,
        "fp-lib-table": FP_LIB_TABLE,
    }
    for name, text in files.items():
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %-24s %7d bytes" % (name, len(text)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
