"""
Generates lib/1v2boost.pretty/*.kicad_mod - a fully self-contained footprint
library. Nothing here depends on the stock KiCad footprint libraries.

Land patterns follow IPC-7351 density level B (nominal) for chip parts.
Package-specific patterns are noted inline with their source.
"""

from __future__ import annotations

import hashlib
import os

SILK_W = 0.12
FAB_W = 0.10
CRTYD_W = 0.05


def uid(*parts) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def n(v) -> str:
    s = "%.4f" % float(v)
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


class Footprint:
    def __init__(self, name, descr, tags, attr="smd"):
        self.name = name
        self.descr = descr
        self.tags = tags
        self.attr = attr
        self.items = []
        self._c = 0
        self._pad_nums = []

    def _u(self):
        self._c += 1
        return uid(self.name, self._c)

    # -- graphics ---------------------------------------------------------
    def line(self, x1, y1, x2, y2, layer, width):
        self.items.append(
            '  (fp_line (start %s %s) (end %s %s)\n'
            '    (stroke (width %s) (type solid))\n'
            '    (layer "%s")\n'
            '    (uuid "%s")\n'
            '  )' % (n(x1), n(y1), n(x2), n(y2), n(width), layer, self._u()))

    def rect_outline(self, w, h, layer, width, cx=0.0, cy=0.0):
        x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        self.line(x1, y1, x2, y1, layer, width)
        self.line(x2, y1, x2, y2, layer, width)
        self.line(x2, y2, x1, y2, layer, width)
        self.line(x1, y2, x1, y1, layer, width)

    def circle(self, cx, cy, r, layer, width, fill=False):
        self.items.append(
            '  (fp_circle (center %s %s) (end %s %s)\n'
            '    (stroke (width %s) (type solid))\n'
            '    (fill %s)\n'
            '    (layer "%s")\n'
            '    (uuid "%s")\n'
            '  )' % (n(cx), n(cy), n(cx + r), n(cy), n(width),
                     "solid" if fill else "none", layer, self._u()))

    def text(self, kind, value, x, y, layer, size=1.0, thickness=0.15):
        self.items.append(
            '  (fp_text %s "%s"\n'
            '    (at %s %s 0)\n'
            '    (layer "%s")\n'
            '    (uuid "%s")\n'
            '    (effects (font (size %s %s) (thickness %s)))\n'
            '  )' % (kind, value, n(x), n(y), layer, self._u(),
                     n(size), n(size), n(thickness)))

    def courtyard(self, w, h, cx=0.0, cy=0.0):
        self.rect_outline(w, h, "F.CrtYd", CRTYD_W, cx, cy)

    # -- pads -------------------------------------------------------------
    def smd(self, num, x, y, sx, sy, rratio=0.25, layers=None, paste=True):
        lay = layers or (["F.Cu", "F.Paste", "F.Mask"] if paste
                         else ["F.Cu", "F.Mask"])
        self._pad_nums.append(num)
        # A roundrect with a zero corner ratio is a rect, and KiCad says so:
        # loading one from a *library* normalises it to PAD_SHAPE::RECT, while
        # the copy already placed on the board stays a roundrect. The two then
        # compare unequal forever and DRC reports the footprint as edited. Emit
        # the shape we actually mean.
        shape = "roundrect" if rratio else "rect"
        rr = ('    (roundrect_rratio %s)\n' % n(rratio)) if rratio else ""
        self.items.append(
            '  (pad "%s" smd %s\n'
            '    (at %s %s)\n'
            '    (size %s %s)\n'
            '    (layers %s)\n'
            '%s'
            '    (uuid "%s")\n'
            '  )' % (num, shape, n(x), n(y), n(sx), n(sy),
                     " ".join('"%s"' % l for l in lay), rr, self._u()))

    def th(self, num, x, y, size, drill, shape="circle", sy=None):
        self.items.append(
            '  (pad "%s" thru_hole %s\n'
            '    (at %s %s)\n'
            '    (size %s %s)\n'
            '    (drill %s)\n'
            '    (layers "*.Cu" "*.Mask")\n'
            '    (uuid "%s")\n'
            '  )' % (num, shape, n(x), n(y), n(size), n(sy if sy else size),
                     n(drill), self._u()))

    def npth(self, x, y, size, drill):
        """Non-plated hole. size must equal drill.

        A size larger than the drill leaves a netless copper annulus on both
        copper layers, which DRC correctly reports as shorting whatever pour
        it lands in, and as a solder-mask bridge. Mask-only relief around the
        hole belongs on *.Mask, not on copper.
        """
        if abs(size - drill) > 1e-9:
            raise ValueError(
                "npth size %s != drill %s: that is a copper annulus with no "
                "net, not a mounting hole" % (size, drill))
        self.items.append(
            '  (pad "" np_thru_hole circle\n'
            '    (at %s %s)\n'
            '    (size %s %s)\n'
            '    (drill %s)\n'
            '    (layers "F.Cu" "B.Cu" "*.Mask")\n'
            '    (uuid "%s")\n'
            '  )' % (n(x), n(y), n(size), n(size), n(drill), self._u()))

    # -- render -----------------------------------------------------------
    def render(self) -> str:
        head = [
            '(footprint "%s"' % self.name,
            '  (version 20240108)',
            '  (generator "1v2boost_gen")',
            '  (generator_version "9.0")',
            '  (layer "F.Cu")',
            '  (descr "%s")' % self.descr,
            '  (tags "%s")' % self.tags,
        ]
        if self.attr:
            head.append("  (attr %s)" % self.attr)
        if self.net_ties:
            # Tells KiCad that pads in the same group are deliberately shorted
            # by the land pattern, so DRC allows the different nets on them
            # instead of reporting a short. Without it a Kelvin land pattern
            # is indistinguishable from a mistake.
            head.append("  (net_tie_pad_groups %s)"
                        % " ".join('"%s"' % g for g in self.net_ties))
        if len(set(self._pad_nums)) != len(self._pad_nums):
            # A footprint that reuses a pad number is building one terminal out
            # of several rectangles. KiCad writes this token into the board on
            # save, so the library copy has to carry it too - otherwise DRC's
            # library-parity check reports the footprint as modified on every
            # run, for a difference nobody introduced.
            head.append("  (duplicate_pad_numbers_are_jumpers no)")
        head.append(
            '  (property "Reference" "REF**"\n'
            '    (at 0 -%s 0)\n'
            '    (layer "%s")\n'
            '%s'
            '    (uuid "%s")\n'
            '    (effects (font (size 1 1) (thickness 0.15)))\n'
            '  )' % (n(self._ref_off),
                     "F.Fab" if self._ref_hidden else "F.SilkS",
                     "    (hide yes)\n" if self._ref_hidden else "",
                     uid(self.name, "ref")))
        head.append(
            '  (property "Value" "%s"\n'
            '    (at 0 %s 0)\n'
            '    (layer "F.Fab")\n'
            '    (uuid "%s")\n'
            '    (effects (font (size 1 1) (thickness 0.15)))\n'
            '  )' % (self.name, n(self._ref_off), uid(self.name, "val")))
        head.append(
            '  (property "Footprint" ""\n'
            '    (at 0 0 0)\n'
            '    (layer "F.Fab")\n'
            '    (hide yes)\n'
            '    (uuid "%s")\n'
            '    (effects (font (size 1 1) (thickness 0.15)))\n'
            '  )' % uid(self.name, "fp"))
        return "\n".join(head + self.items) + "\n)\n"

    _ref_off = 2.0
    net_ties = ()
    # Mechanical items (mounting holes, fiducials) set this: their designator
    # carries no assembly information, and printing it next to a hole sitting
    # 5 mm from the board edge only creates silkscreen clipped by that edge.
    _ref_hidden = False


# --------------------------------------------------------------------------
# Chip packages (IPC-7351B nominal)
# --------------------------------------------------------------------------

CHIP = {
    # name        body_x body_y  pad_x  pad_y  pad_cx
    "0603": (1.60, 0.80, 0.90, 0.95, 0.7875),
    "0805": (2.00, 1.25, 1.05, 1.45, 0.9500),
    "1210": (3.20, 2.50, 1.50, 2.70, 1.5000),
    "1812": (4.50, 3.20, 1.60, 3.40, 1.9500),
    "2512": (6.30, 3.20, 1.65, 3.35, 2.8500),
}

METRIC = {"0603": "1608Metric", "0805": "2012Metric", "1210": "3225Metric",
          "1812": "4532Metric", "2512": "6332Metric"}


def chip(prefix, size, descr, tags):
    bx, by, px, py, pcx = CHIP[size]
    fp = Footprint("%s_%s_%s" % (prefix, size, METRIC[size]), descr, tags)
    fp._ref_off = py / 2 + 0.9
    fp.smd("1", -pcx, 0, px, py)
    fp.smd("2", pcx, 0, px, py)
    fp.rect_outline(bx, by, "F.Fab", FAB_W)
    silk_y = by / 2 + 0.2
    gap = pcx - px / 2 - 0.2
    if gap > 0.1:
        fp.line(-gap, -silk_y, gap, -silk_y, "F.SilkS", SILK_W)
        fp.line(-gap, silk_y, gap, silk_y, "F.SilkS", SILK_W)
    fp.courtyard(2 * (pcx + px / 2) + 0.5, max(by, py) + 0.5)
    return fp


# --------------------------------------------------------------------------
# Specific packages
# --------------------------------------------------------------------------

def fp_shunt_2512_css2h():
    """Bourns CSS2H-2512, with the Kelvin sense fingers Bourns recommends.

    The part is **two-terminal** - "CSS2H" decodes as 2 terminals in the
    ordering table on page 1 of the datasheet, and the plan view shows two end
    terminals and nothing else. An earlier version of this file had a
    four-terminal land pattern with separate sense pads inboard of the current
    pads, which is a package Bourns does not make.

    What Bourns actually publishes (Recommended Pad Layout, page 2) is a
    two-terminal land pattern where each pad grows a 0.40 mm finger that runs
    inward under the body and then out the side, past the body edge:

      solder pads   1.80 wide x 3.40 tall, 3.40 mm gap between them
      sense finger  0.40 mm wide, tip 3.20 mm off the centreline
      body          6.35 +/-0.15 x 3.05 +/-0.20 mm

    Those close on each other: pad span 7.00 against a 6.35 body is 0.325 mm
    of toe, and the 3.40 gap against terminals whose inner edges sit at
    +/-2.035 is 0.335 mm of heel. Toe and heel agreeing to 10 um is the check
    that the callouts were read correctly.

    The finger is *continuous copper with its solder pad* - so this is not a
    four-wire part, and the Kelvin benefit is purely geometric: the sense trace
    leaves from a tip that carries no load current, so it does not pick up the
    IR drop of the copper feeding the shunt. That is why the footprint still
    presents four pads and declares them as two net-tie groups. The schematic
    keeps SRC/ISNS_K and PGND/AGND as distinct nets - which is what documents
    "start the sense trace here, not there" and what keeps the AGND island a
    separate zone - while DRC is told the truth, that each pair is one node.

    Solder mask: the finger is bare only where it clears the body edge
    (|y| > 1.525). The rest is covered. Two exposed 0.40 mm fingers 0.60 mm
    apart, under the body, between two paste-loaded pads, is a solder bridge
    that shorts the shunt out - and a shorted shunt is not a visible failure,
    it is a converter with no current limit.

    Both fingers point +Y, which on this board is toward the controller. That
    matters: a sense trace has to leave its tip heading away from the part. The
    first cut had them pointing -Y, so both traces had to double back along the
    side of the package, and they ran 0.275 mm from the opposite terminal's
    solder pad against a 0.3 mm Power clearance. A net tie does not rescue that
    - KiCad exempts the tied pads from each other, not passing tracks from the
    pads - so the direction is part of the footprint being correct, not a
    layout detail.
    """
    fp = Footprint("R_Shunt_2512_CSS2H",
                   "Bourns CSS2H-2512 current sense resistor, 2-terminal, "
                   "with the Kelvin sense fingers from the datasheet land "
                   "pattern. Pads 1+3 and 2+4 are each one terminal.",
                   "shunt kelvin 2512 current sense bourns css2h")
    fp._ref_off = 4.4
    fp.net_ties = ("1, 3", "2, 4")
    bare = ["F.Cu", "F.Mask"]
    covered = ["F.Cu"]
    # Current-carrying pads: the only ones that get paste.
    fp.smd("1", -2.60, 0, 1.80, 3.40, rratio=0.1)
    fp.smd("2", 2.60, 0, 1.80, 3.40, rratio=0.1)
    # Each sense finger is three rectangles sharing one pad number. Every pair
    # overlaps by 0.20 mm rather than merely abutting: shapes that touch along
    # a zero-width seam are not reliably one copper region, and the tip is the
    # end that matters.
    #
    # The TIP IS EMITTED FIRST on purpose. gen_pcb.py's pad_pos() takes the
    # first pad with a given number, so this is what makes a sense route attach
    # out past the body instead of at the neck underneath it - which would
    # throw away the entire reason the finger exists.
    for sign, sense in ((-1, "3"), (1, "4")):
        # Tip: clear of the body (|y| > 1.525), so it is left bare to probe.
        fp.smd(sense, sign * 0.50, 2.40, 0.40, 1.60, rratio=0, layers=bare)
        # Riser, under the body and mask-covered.
        fp.smd(sense, sign * 0.50, 1.00, 0.40, 2.40, rratio=0, layers=covered)
        # Neck, running inward from the solder pad to the riser.
        fp.smd(sense, sign * 1.10, 0, 1.60, 0.40, rratio=0, layers=covered)
    fp.rect_outline(6.35, 3.05, "F.Fab", FAB_W)
    # Silk above the part only: the fingers occupy the space below it.
    fp.line(-1.9, -1.9, 1.9, -1.9, "F.SilkS", SILK_W)
    fp.text("user", "PADS 1+3 AND 2+4 ARE EACH ONE TERMINAL", 0, -5.8,
            "F.Fab", 0.8)
    fp.courtyard(8.2, 7.4, cy=1.6)
    return fp


def fp_polymer_d10x10():
    """Panasonic SP-Cap / OS-CON style 10 x 10 mm SMD polymer capacitor."""
    fp = Footprint("CP_Polymer_D10x10", "Polymer capacitor, 10x10 mm SMD",
                   "capacitor polymer bulk")
    fp._ref_off = 6.2
    fp.smd("1", -4.00, 0, 3.20, 4.60)   # anode (+)
    fp.smd("2", 4.00, 0, 3.20, 4.60)
    fp.rect_outline(10.3, 10.3, "F.Fab", FAB_W)
    # Silk only above and below the pads. The pads reach x = +/-5.6, so a full
    # 10.6 mm rectangle would run its side edges straight through them.
    fp.line(-5.3, -5.3, 5.3, -5.3, "F.SilkS", SILK_W)
    fp.line(-5.3, 5.3, 5.3, 5.3, "F.SilkS", SILK_W)
    # polarity bar clear of the anode pad's left edge at x = -5.6
    fp.line(-6.3, -3.0, -6.3, 3.0, "F.SilkS", 0.3)
    fp.circle(-7.1, 0, 0.25, "F.SilkS", SILK_W, fill=True)
    fp.courtyard(15.0, 11.2)
    return fp


def fp_polymer_7343():
    """EIA 7343 (D case) tantalum/polymer capacitor."""
    fp = Footprint("CP_Polymer_7343", "Polymer capacitor, EIA 7343 (D case)",
                   "capacitor polymer tantalum 7343")
    fp._ref_off = 3.4
    fp.smd("1", -3.10, 0, 2.40, 2.40)   # anode (+)
    fp.smd("2", 3.10, 0, 2.40, 2.40)
    fp.rect_outline(7.30, 4.30, "F.Fab", FAB_W)
    fp.line(-3.9, -2.4, 3.9, -2.4, "F.SilkS", SILK_W)
    fp.line(-3.9, 2.4, 3.9, 2.4, "F.SilkS", SILK_W)
    # anode bar clear of the pad, which reaches x = -4.3
    fp.line(-4.7, -2.4, -4.7, 2.4, "F.SilkS", 0.3)
    fp.courtyard(10.4, 5.2)
    return fp


def fp_msop10():
    """MSOP-10 (TI DGQ): 3.0 x 3.0 mm body, 0.5 mm pitch, 5 pins per side."""
    fp = Footprint("MSOP-10_3x3mm_P0.5mm",
                   "MSOP-10, 3x3 mm body, 0.5 mm pitch (TI DGQ)",
                   "msop vssop 10 0.5mm")
    fp._ref_off = 3.2
    px, py = 1.45, 0.30
    xc = 2.20
    ys = [-1.0, -0.5, 0.0, 0.5, 1.0]
    for i, y in enumerate(ys):                 # pins 1-5, left, top to bottom
        fp.smd(str(i + 1), -xc, y, px, py)
    for i, y in enumerate(reversed(ys)):       # pins 6-10, right, bottom to top
        fp.smd(str(i + 6), xc, y, px, py)
    fp.rect_outline(3.0, 3.0, "F.Fab", FAB_W)
    fp.line(-1.5, -1.6, 1.5, -1.6, "F.SilkS", SILK_W)
    fp.line(-1.5, 1.6, 1.5, 1.6, "F.SilkS", SILK_W)
    fp.circle(-3.3, -1.0, 0.15, "F.SilkS", SILK_W, fill=True)   # pin 1 marker
    fp.courtyard(6.4, 3.6)
    return fp


def fp_sot23_6():
    """SOT-23-6 (TI DBV): 2.9 x 1.6 mm body, 0.95 mm pitch."""
    fp = Footprint("SOT-23-6", "SOT-23-6, 0.95 mm pitch (TI DBV)",
                   "sot23 6 dbv")
    fp._ref_off = 2.6
    px, py, yc = 0.65, 1.10, 1.25
    xs = [-0.95, 0.0, 0.95]
    for i, x in enumerate(xs):                  # pins 1-3 along +Y edge
        fp.smd(str(i + 1), x, yc, px, py)
    for i, x in enumerate(reversed(xs)):        # pins 4-6 along -Y edge
        fp.smd(str(i + 4), x, -yc, px, py)
    fp.rect_outline(2.9, 1.6, "F.Fab", FAB_W)
    # Silk down the two sides only. The pads occupy both the +Y and -Y edges
    # (y = 0.70 .. 1.80), so a horizontal body line would run across them.
    fp.line(-1.55, -0.85, -1.55, 0.85, "F.SilkS", SILK_W)
    fp.line(1.55, -0.85, 1.55, 0.85, "F.SilkS", SILK_W)
    fp.circle(-1.85, 1.25, 0.15, "F.SilkS", SILK_W, fill=True)
    fp.courtyard(3.8, 3.2)
    return fp


def fp_sot23():
    """SOT-23 3-lead, for the 5.1 V zener."""
    fp = Footprint("SOT-23", "SOT-23, 3 lead", "sot23 3")
    fp._ref_off = 2.6
    px, py, yc = 0.80, 1.10, 1.25
    fp.smd("1", -0.95, yc, px, py)
    fp.smd("2", 0.95, yc, px, py)
    fp.smd("3", 0.0, -yc, px, py)
    fp.rect_outline(2.9, 1.6, "F.Fab", FAB_W)
    # sides only - the pads cover both Y edges
    fp.line(-1.55, -0.85, -1.55, 0.85, "F.SilkS", SILK_W)
    fp.line(1.55, -0.85, 1.55, 0.85, "F.SilkS", SILK_W)
    fp.circle(-1.85, 1.25, 0.15, "F.SilkS", SILK_W, fill=True)
    fp.courtyard(3.8, 3.2)
    return fp


def fp_to220_vertical():
    """TO-220AB, 3 lead, vertical mount, 2.54 mm lead pitch.

    Used for Q1/Q2 (SUP40012EL-GE3) and D1 (STPS40L45CT). Through-hole is
    deliberate: unambiguous land pattern, hand solderable, and the tab bolts
    to a heatsink, which matters at the 28 A corner. Pads are oversized and
    the drill is generous because these leads carry the full switch current.
    """
    fp = Footprint("TO-220-3_Vertical",
                   "TO-220AB 3-lead vertical, 2.54 mm pitch, high current",
                   "to220 to-220 power vertical", attr="through_hole")
    fp._ref_off = 5.8
    for i, x in enumerate((-2.54, 0.0, 2.54)):
        fp.th(str(i + 1), x, 0, 1.80, 1.10,
              shape="rect" if i == 0 else "circle")
    # body outline (10.16 wide x 4.5 deep footprint of the plastic + tab)
    fp.rect_outline(10.16, 4.60, "F.Fab", FAB_W, cy=-2.30)
    # Silk stops short of the lead pads, which reach y = +/-0.9. Outlining the
    # full 4.8 mm body would put the near edge straight through all three.
    fp.rect_outline(10.40, 3.50, "F.SilkS", SILK_W, cy=-2.95)
    fp.circle(-2.54, 2.0, 0.2, "F.SilkS", SILK_W, fill=True)   # pin 1 marker
    fp.courtyard(11.0, 8.4, cy=-1.6)
    return fp


def fp_pg1083():
    """Pulse PG1083NL high-current round-wire inductor (L1).

    Every number below is the SUGGESTED LAND PATTERN callout from Pulse
    datasheet P716.D, page 1:

      pads 1/2   4.00 wide x 5.00 tall, centres 14.50 mm apart
      pad 3      4.00 x 4.00, centre 17.50 mm from the pad 1/2 row
      body       21.70 (across the pad 1/2 row) x 21.50 mm, 10.60 mm high

    Those five figures close on themselves, which is the check that they were
    read correctly: 17.50 + 5.00/2 + 4.00/2 = 22.00 mm of land against a
    21.50 mm body, i.e. every pad overhangs its body edge by exactly 0.25 mm.
    Do not "improve" any of them without re-reading P716.

    How they were obtained is worth recording, because the drawing is not
    trustworthy on its own. The land pattern in P716 is vector art whose
    horizontal and vertical scales differ by about 10% - measuring the drawn
    pads gives 4.00 x 5.00 vertically but only 3.5 mm horizontally, and the
    drawn body works out to 24.4 mm. The callouts are the specification and
    the artwork is not to scale. Anything scaled off that drawing is wrong.

    Pad 3 carries no net. P716's schematic shows pin 3's lead terminating in
    free space - it is a mounting tab, not a winding terminal.

    Note 10 of P716: the core is conductive. Nothing under the body may be an
    exposed via, and the terminal-to-terminal voltage must stay under 24 V
    (SW swings to ~12.6 V here, so that part is satisfied by inspection).
    """
    fp = Footprint("L_Pulse_PG1083",
                   "Pulse PG1083NL round wire power inductor, "
                   "21.70 x 21.50 x 10.60 mm. Land pattern per P716.D. "
                   "Pad 3 is a mounting tab - leave it unconnected.",
                   "inductor pulse pg1083 high current")
    fp._ref_off = 12.6
    # Winding terminals along the +Y edge, so that on this board they face the
    # switch leg; the part is non-polarised, so 1-vs-2 is arbitrary.
    fp.smd("1", -7.25, 8.5, 4.0, 5.0)
    fp.smd("2", 7.25, 8.5, 4.0, 5.0)
    fp.smd("3", 0.0, -9.0, 4.0, 4.0)         # mounting tab, no net
    fp.rect_outline(21.7, 21.5, "F.Fab", FAB_W)
    # Silk clears the pads rather than being broken around them: the pads
    # reach y = +/-11.0 and x = +/-9.25, so a 22.3 x 22.6 rectangle passes
    # outside all three without a single crossing.
    fp.rect_outline(22.3, 22.6, "F.SilkS", SILK_W)
    fp.circle(-11.6, 11.6, 0.2, "F.SilkS", SILK_W, fill=True)   # pin 1 marker
    # Clear of the value field, which _ref_off also puts at y = +12.6.
    fp.text("user", "PAD3 = MOUNTING TAB, NO NET", 0, 14.0, "F.Fab", 1.0)
    fp.text("user", "NO EXPOSED VIAS UNDER BODY", 0, 15.4, "F.Fab", 1.0)
    fp.courtyard(23.0, 23.2)
    return fp


def fp_solder_pad(name, w, h):
    """Large tinned pad for soldering heavy wire or braid directly.

    No paste aperture: these are hand-soldered, and a stencil aperture this
    size would flood the joint.
    """
    fp = Footprint(name, "High-current solder-direct pad, %gx%g mm" % (w, h),
                   "solder pad terminal high current", attr="smd")
    fp._ref_off = h / 2 + 1.2
    fp.smd("1", 0, 0, w, h, rratio=0.1, layers=["F.Cu", "B.Cu", "F.Mask", "B.Mask"])
    fp.rect_outline(w + 0.4, h + 0.4, "F.SilkS", SILK_W)
    fp.courtyard(w + 1.0, h + 1.0)
    return fp


def fp_solder_pad_pair(name, w, h, gap, descr, vertical=False):
    """Two large tinned pads for soldering heavy wire or braid directly.

    Pad 1 is the positive terminal, pad 2 the return. No paste apertures:
    these are hand-soldered and a stencil aperture this size would flood the
    joint. Copper is present on both layers and stitched with vias in the
    board file.
    """
    fp = Footprint(name, descr, "solder pad terminal high current", attr="smd")
    # Stacked pads put the upper one h/2 + gap/2 above the origin, so a
    # designator offset of only h/2 lands on the copper.
    fp._ref_off = (h + (h + gap) / 2.0 + 1.6) if vertical else (h / 2 + 1.6)
    # Top layer only: the B.Cu pour underneath is PGND, so putting the
    # positive terminal on both layers would create an isolated island.
    # The PGND terminal reaches the pour through a via array placed in
    # gen_pcb.py instead.
    lay = ["F.Cu", "F.Mask"]
    if vertical:
        cy = (h + gap) / 2.0
        fp.smd("1", 0, -cy, w, h, rratio=0.08, layers=lay)
        fp.smd("2", 0, cy, w, h, rratio=0.08, layers=lay)
        fp.rect_outline(w + 0.5, h + 0.5, "F.SilkS", SILK_W, cy=-cy)
        fp.rect_outline(w + 0.5, h + 0.5, "F.SilkS", SILK_W, cy=cy)
        fp.text("user", "+", -(w / 2 + 1.6), -cy, "F.SilkS", 1.5)
        fp.text("user", "-", -(w / 2 + 1.6), cy, "F.SilkS", 1.5)
        fp.courtyard(w + 1.0, 2 * cy + h + 1.0)
    else:
        cx = (w + gap) / 2.0
        fp.smd("1", -cx, 0, w, h, rratio=0.08, layers=lay)
        fp.smd("2", cx, 0, w, h, rratio=0.08, layers=lay)
        fp.rect_outline(w + 0.5, h + 0.5, "F.SilkS", SILK_W, cx=-cx)
        fp.rect_outline(w + 0.5, h + 0.5, "F.SilkS", SILK_W, cx=cx)
        fp.text("user", "+", -cx, -(h / 2 + 1.0), "F.SilkS", 1.5)
        fp.text("user", "-", cx, -(h / 2 + 1.0), "F.SilkS", 1.5)
        fp.courtyard(2 * cx + w + 1.0, h + 1.0)
    return fp


def fp_sma():
    """DO-214AC (SMA). Pin 1 = cathode (band end)."""
    fp = Footprint("D_SMA", "Diode, DO-214AC (SMA)", "diode sma do-214ac")
    fp._ref_off = 2.6
    fp.smd("1", -2.15, 0, 1.60, 1.75)
    fp.smd("2", 2.15, 0, 1.60, 1.75)
    fp.rect_outline(4.30, 2.60, "F.Fab", FAB_W)
    fp.line(-2.6, -1.5, 2.6, -1.5, "F.SilkS", SILK_W)
    fp.line(-2.6, 1.5, 2.6, 1.5, "F.SilkS", SILK_W)
    fp.line(-3.3, -1.5, -3.3, 1.5, "F.SilkS", 0.3)      # cathode band
    fp.courtyard(7.2, 3.4)
    return fp


def fp_sod123():
    """SOD-123. Pin 1 = cathode (band end)."""
    fp = Footprint("D_SOD-123", "Diode, SOD-123", "diode sod-123")
    fp._ref_off = 1.9
    fp.smd("1", -1.65, 0, 0.90, 1.20)
    fp.smd("2", 1.65, 0, 0.90, 1.20)
    fp.rect_outline(2.70, 1.60, "F.Fab", FAB_W)
    fp.line(-1.35, -1.0, 1.35, -1.0, "F.SilkS", SILK_W)
    fp.line(-1.35, 1.0, 1.35, 1.0, "F.SilkS", SILK_W)
    fp.line(-2.4, -1.0, -2.4, 1.0, "F.SilkS", 0.3)      # cathode band
    fp.courtyard(5.4, 2.4)
    return fp


def fp_pinheader_1x02():
    fp = Footprint("PinHeader_1x02_P2.54mm", "2-pin 2.54 mm header",
                   "connector header 2.54", attr="through_hole")
    fp._ref_off = 3.9
    fp.th("1", 0, -1.27, 1.70, 1.00, shape="rect")
    fp.th("2", 0, 1.27, 1.70, 1.00)
    fp.rect_outline(2.54, 5.08, "F.Fab", FAB_W)
    fp.rect_outline(2.80, 5.34, "F.SilkS", SILK_W)
    fp.courtyard(3.4, 5.9)
    return fp


def fp_testpoint():
    fp = Footprint("TestPoint_Pad_D1.5mm", "Test point, 1.5 mm pad",
                   "test point probe", attr="smd")
    fp._ref_off = 1.8
    fp.smd("1", 0, 0, 1.50, 1.50, rratio=0.5)
    fp.circle(0, 0, 1.1, "F.SilkS", SILK_W)
    fp.courtyard(2.4, 2.4)
    return fp


def fp_mounting_hole():
    fp = Footprint("MountingHole_3.2mm_M3", "M3 mounting hole",
                   "mounting hole m3", attr="exclude_from_pos_files exclude_from_bom")
    fp._ref_hidden = True
    fp._ref_off = 4.4
    fp.npth(0, 0, 3.2, 3.2)
    fp.circle(0, 0, 3.2, "F.SilkS", SILK_W)
    fp.courtyard(6.8, 6.8)
    return fp


def fp_fiducial():
    fp = Footprint("Fiducial_1mm", "Fiducial, 1 mm copper, 2 mm mask opening",
                   "fiducial", attr="exclude_from_pos_files exclude_from_bom")
    fp._ref_hidden = True
    fp._ref_off = 2.4
    fp.items.append(
        '  (pad "1" smd circle\n'
        '    (at 0 0)\n'
        '    (size 1 1)\n'
        '    (layers "F.Cu" "F.Mask")\n'
        '    (solder_mask_margin 0.5)\n'
        # Keep the surrounding pour back far enough that its mask opening
        # cannot merge with the fiducial's. A fiducial has no net, so any
        # copper the mask aperture reaches counts as a bridge - and a fiducial
        # whose contrast ring is flooded is useless to the placement machine.
        '    (clearance 1)\n'
        '    (zone_connect 0)\n'
        '    (uuid "%s")\n'
        '  )' % uid("fid", 1))
    fp.courtyard(3.0, 3.0)
    return fp


# --------------------------------------------------------------------------

def build_all():
    fps = [
        chip("R", "0603", "Resistor, 0603 (1608 metric)", "resistor 0603"),
        chip("R", "0805", "Resistor, 0805 (2012 metric)", "resistor 0805"),
        chip("R", "1210", "Resistor, 1210 (3225 metric)", "resistor 1210"),
        chip("R", "2512", "Resistor, 2512 (6332 metric)", "resistor 2512"),
        chip("C", "0603", "Capacitor, 0603 (1608 metric)", "capacitor 0603"),
        chip("C", "0805", "Capacitor, 0805 (2012 metric)", "capacitor 0805"),
        chip("C", "1210", "Capacitor, 1210 (3225 metric)", "capacitor 1210"),
        chip("C", "1812", "Capacitor, 1812 (4532 metric)", "capacitor 1812"),
        fp_shunt_2512_css2h(),
        fp_polymer_d10x10(),
        fp_polymer_7343(),
        fp_msop10(),
        fp_sot23_6(),
        fp_sot23(),
        fp_to220_vertical(),
        fp_pg1083(),
        fp_solder_pad_pair("SolderPad_Pair_Input_14x10mm", 14.0, 10.0, 6.0,
                           "Cell input terminals, 2x 14x10 mm solder pads "
                           "(28 A), + above -", vertical=True),
        fp_solder_pad_pair("SolderPad_Pair_Output_8x6mm", 8.0, 6.0, 5.0,
                           "Output terminals, 2x 8x6 mm solder pads, "
                           "+ above -", vertical=True),
        fp_sma(),
        fp_sod123(),
        fp_pinheader_1x02(),
        fp_testpoint(),
        fp_mounting_hole(),
        fp_fiducial(),
    ]
    return fps


FOOTPRINT_NAMES = [
    "R_0603_1608Metric", "R_0805_2012Metric", "R_1210_3225Metric",
    "R_2512_6332Metric", "C_0603_1608Metric", "C_0805_2012Metric",
    "C_1210_3225Metric", "C_1812_4532Metric", "R_Shunt_2512_CSS2H",
    "CP_Polymer_D10x10", "CP_Polymer_7343", "MSOP-10_3x3mm_P0.5mm",
    "SOT-23-6", "SOT-23", "TO-220-3_Vertical", "L_Pulse_PG1083",
    "SolderPad_Pair_Input_14x10mm", "SolderPad_Pair_Output_8x6mm",
    "D_SMA", "D_SOD-123",
    "PinHeader_1x02_P2.54mm", "TestPoint_Pad_D1.5mm",
    "MountingHole_3.2mm_M3", "Fiducial_1mm",
]


def write_library(root):
    outdir = os.path.join(root, "lib", "1v2boost.pretty")
    os.makedirs(outdir, exist_ok=True)
    written = []
    for fp in build_all():
        path = os.path.join(outdir, fp.name + ".kicad_mod")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fp.render())
        written.append(fp.name)
    # Delete footprints this file no longer generates. Writing without pruning
    # left L_Coilcraft_SER2915 sitting in the library after the part was
    # replaced - a land pattern for an unbuyable part, in a library whose whole
    # claim is that it is generated and self-contained.
    stale = []
    for name in sorted(os.listdir(outdir)):
        if name.endswith(".kicad_mod") and name[:-10] not in written:
            os.remove(os.path.join(outdir, name))
            stale.append(name[:-10])
    return outdir, written, stale


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir, written, stale = write_library(root)
    print("wrote %d footprints to %s" % (len(written), outdir))
    if stale:
        print("removed %d stale footprint(s): %s" % (len(stale),
                                                     ", ".join(stale)))
    missing = set(FOOTPRINT_NAMES) ^ set(written)
    print("name list matches generated set:", not missing,
          ("" if not missing else missing))
