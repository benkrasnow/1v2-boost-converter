"""
Generates lib/1v2boost.kicad_sym - a fully self-contained symbol library.

Every symbol the project uses lives here, including generic passives, so the
project has no dependency whatsoever on the stock KiCad libraries.

Pinouts for the three ICs were taken from the manufacturer datasheets and are
noted inline; they are NOT guesses.
"""

from __future__ import annotations

FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) (hide yes))"

# Pin sides -> (angle pointing from endpoint toward body)
ANGLE = {"L": 0, "R": 180, "U": 270, "D": 90}


def _prop(name, value, x, y, rot=0, hide=True, bold=False):
    eff = FONT_HIDE if hide else FONT
    return ('    (property "%s" "%s" (at %s %s %d)\n      %s\n    )'
            % (name, value, _n(x), _n(y), rot, eff))


def _n(v):
    """Format a number the way KiCad does (no trailing .0 noise)."""
    if isinstance(v, int):
        return str(v)
    s = "%.6f" % v
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


class Pin:
    """A symbol pin. `x`/`y` are the connection point in library coordinates.

    The schematic generator needs these to draw wires, so pins carry their
    geometry rather than being rendered straight to text.
    """

    __slots__ = ("num", "x", "y", "angle", "text")

    def __init__(self, num, x, y, angle, text):
        self.num, self.x, self.y = num, x, y
        self.angle, self.text = angle, text


def _pin(number, name, etype, x, y, side, length=2.54, hide=False):
    h = " (hide yes)" if hide else ""
    text = (
        '      (pin %s line (at %s %s %d) (length %s)%s\n'
        '        (name "%s" %s)\n'
        '        (number "%s" %s)\n'
        '      )' % (etype, _n(x), _n(y), ANGLE[side], _n(length), h,
                     name, FONT, number, FONT))
    return Pin(number, x, y, ANGLE[side], text)


def _rect(x1, y1, x2, y2, fill="background"):
    return ('      (rectangle (start %s %s) (end %s %s)\n'
            '        (stroke (width 0.254) (type default))\n'
            '        (fill (type %s))\n'
            '      )' % (_n(x1), _n(y1), _n(x2), _n(y2), fill))


def _poly(points, width=0.254, fill="none"):
    pts = " ".join("(xy %s %s)" % (_n(x), _n(y)) for x, y in points)
    return ('      (polyline\n'
            '        (pts %s)\n'
            '        (stroke (width %s) (type default))\n'
            '        (fill (type %s))\n'
            '      )' % (pts, _n(width), fill))


def _circle(cx, cy, r, fill="none"):
    return ('      (circle (center %s %s) (radius %s)\n'
            '        (stroke (width 0.254) (type default))\n'
            '        (fill (type %s))\n'
            '      )' % (_n(cx), _n(cy), _n(r), fill))


def symbol(name, ref, value, footprint, datasheet, description, body, pins,
           hide_pin_numbers=False, hide_pin_names=False, name_offset=0.508,
           power=False, in_bom=True, ref_at=(0, 5.08), val_at=(0, -5.08),
           keywords=None):
    out = ['  (symbol "%s"' % name]
    if power:
        out.append("    (power)")
    out.append("    (pin_numbers%s)"
               % (" (hide yes)" if hide_pin_numbers else ""))
    out.append("    (pin_names (offset %s)%s)"
               % (_n(name_offset), " (hide yes)" if hide_pin_names else ""))
    out.append("    (exclude_from_sim no)")
    out.append("    (in_bom %s)" % ("yes" if in_bom else "no"))
    out.append("    (on_board yes)")
    out.append(_prop("Reference", ref, ref_at[0], ref_at[1], hide=False))
    out.append(_prop("Value", value, val_at[0], val_at[1], hide=False))
    out.append(_prop("Footprint", footprint, 0, -7.62))
    out.append(_prop("Datasheet", datasheet, 0, -10.16))
    out.append(_prop("Description", description, 0, -12.7))
    if keywords:
        out.append(_prop("ki_keywords", keywords, 0, -15.24))
    out.append('    (symbol "%s_0_1"' % name)
    out.extend(body)
    out.append("    )")
    out.append('    (symbol "%s_1_1"' % name)
    out.extend(p.text for p in pins)
    out.append("    )")
    out.append("  )")
    PIN_MAP[name] = {p.num: (p.x, p.y, p.angle) for p in pins}
    return name, "\n".join(out)


# symbol name -> {pin number: (x, y) in library coordinates}
PIN_MAP: dict = {}


# --------------------------------------------------------------------------
# Generic passives
# --------------------------------------------------------------------------

def sym_resistor():
    body = [_rect(-1.016, -2.54, 1.016, 2.54, fill="none")]
    pins = [_pin("1", "~", "passive", 0, 3.81, "D", 1.27),
            _pin("2", "~", "passive", 0, -3.81, "U", 1.27)]
    return symbol("R", "R", "R", "", "~", "Resistor", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(2.032, 0), val_at=(0, 0), keywords="R res resistor")


def sym_shunt_kelvin():
    """Two-terminal current sense resistor drawn with its Kelvin taps.

    This is NOT a four-terminal part, and an earlier version of this file
    wrongly said it was. The Bourns CSS2H-2512 has two terminals; pin 3 is the
    same node as pin 1 and pin 4 the same node as pin 2. They appear as
    separate pins because the footprint's sense fingers are separate *places*
    on that node - copper that carries no load current - and the schematic is
    where that distinction gets recorded.

    Keeping them as distinct nets is what makes the layout rules checkable:
    ISNS_K must start at the finger and not at the solder pad, and AGND must
    reach PGND at that finger and nowhere else. The footprint declares
    (net_tie_pad_groups "1, 3" "2, 4") so DRC knows the pairs are one node.

    The short bars from pins 3 and 4 into the body are drawn as taps off the
    force leads rather than as independent terminals, so the symbol reads the
    way the part actually is.
    """
    body = [_rect(-2.54, -2.54, 2.54, 2.54, fill="none"),
            # taps: pin 3 joins the pin 1 lead, pin 4 joins the pin 2 lead
            _poly([(-2.54, -2.54), (-2.54, -3.81), (-3.81, -3.81),
                   (-3.81, 0)], 0.152),
            _poly([(2.54, -2.54), (2.54, -3.81), (3.81, -3.81),
                   (3.81, 0)], 0.152)]
    pins = [_pin("1", "F1", "passive", -5.08, 0, "L", 2.54),
            _pin("2", "F2", "passive", 5.08, 0, "R", 2.54),
            _pin("3", "K1", "passive", -2.54, -6.35, "U", 2.54),
            _pin("4", "K2", "passive", 2.54, -6.35, "U", 2.54)]
    return symbol("R_Shunt_Kelvin", "R", "1m0", "", "~",
                  "2-terminal current sense resistor with Kelvin sense taps "
                  "(pins 1+3 and 2+4 are each one terminal)", body, pins,
                  ref_at=(0, 4.318), val_at=(0, -8.89),
                  keywords="shunt kelvin current sense")


def sym_capacitor():
    body = [
        _poly([(-2.032, -0.762), (2.032, -0.762)], 0.508),
        _poly([(-2.032, 0.762), (2.032, 0.762)], 0.508),
    ]
    pins = [_pin("1", "~", "passive", 0, 3.81, "D", 2.794),
            _pin("2", "~", "passive", 0, -3.81, "U", 2.794)]
    return symbol("C", "C", "C", "", "~", "Unpolarized capacitor", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(2.54, 0.508), val_at=(2.54, -2.032),
                  keywords="cap capacitor")


def sym_capacitor_pol():
    body = [
        _poly([(-2.032, -0.762), (2.032, -0.762)], 0.508),
        _poly([(-2.032, 0.762), (2.032, 0.762)], 0.508),
        _poly([(-1.524, 2.286), (-0.508, 2.286)], 0.254),
        _poly([(-1.016, 1.778), (-1.016, 2.794)], 0.254),
    ]
    pins = [_pin("1", "~", "passive", 0, 3.81, "D", 2.794),
            _pin("2", "~", "passive", 0, -3.81, "U", 2.794)]
    return symbol("C_Polarized", "C", "C", "", "~",
                  "Polarized capacitor (polymer/electrolytic)", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(2.54, 0.508), val_at=(2.54, -2.032),
                  keywords="cap capacitor polarized polymer")


def sym_inductor():
    body = [
        _poly([(0, -2.54), (0, -1.27)], 0.254),
        _poly([(0, 2.54), (0, 1.27)], 0.254),
    ]
    # four half-circle humps
    for i, yc in enumerate((-0.9525, -0.3175, 0.3175, 0.9525)):
        body.append(_circle(0.0, yc, 0.3175))
    pins = [_pin("1", "~", "passive", 0, 3.81, "D", 1.27),
            _pin("2", "~", "passive", 0, -3.81, "U", 1.27)]
    return symbol("L", "L", "L", "", "~", "Inductor", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(2.54, 0), val_at=(-2.54, 0),
                  keywords="inductor choke coil")


def _diode_body(extra=()):
    body = [
        _poly([(-1.27, 1.27), (-1.27, -1.27), (1.27, 0), (-1.27, 1.27)],
              0.254, fill="outline"),
        _poly([(1.27, 1.27), (1.27, -1.27)], 0.254),
    ]
    body.extend(extra)
    return body


def sym_diode_schottky():
    extra = [_poly([(1.27, 1.27), (0.762, 1.27), (0.762, 0.762)], 0.254),
             _poly([(1.27, -1.27), (1.778, -1.27), (1.778, -0.762)], 0.254)]
    body = _diode_body(extra)
    pins = [_pin("1", "K", "passive", 3.81, 0, "R", 2.54),
            _pin("2", "A", "passive", -3.81, 0, "L", 2.54)]
    return symbol("D_Schottky", "D", "D_Schottky", "", "~",
                  "Schottky barrier rectifier", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(0, 2.54), val_at=(0, -2.54),
                  keywords="diode schottky rectifier")


def sym_diode_schottky_dual_cc():
    """Dual Schottky, common cathode (STPS40L45CT in TO-220).

    Both halves are paralleled in this design, which halves the current per
    element and so lowers Vf where it matters.
    """
    body = [
        # upper diode, anode pin 1 -> common cathode
        _poly([(-3.81, 2.54), (-1.27, 2.54)], 0.254),
        _poly([(-1.27, 3.81), (-1.27, 1.27), (1.27, 2.54), (-1.27, 3.81)],
              0.254, fill="outline"),
        _poly([(1.27, 3.81), (1.27, 1.27)], 0.254),
        _poly([(1.27, 3.81), (0.762, 3.81)], 0.254),
        _poly([(1.27, 1.27), (1.778, 1.27)], 0.254),
        # lower diode, anode pin 3 -> common cathode
        _poly([(-3.81, -2.54), (-1.27, -2.54)], 0.254),
        _poly([(-1.27, -1.27), (-1.27, -3.81), (1.27, -2.54), (-1.27, -1.27)],
              0.254, fill="outline"),
        _poly([(1.27, -1.27), (1.27, -3.81)], 0.254),
        _poly([(1.27, -1.27), (0.762, -1.27)], 0.254),
        _poly([(1.27, -3.81), (1.778, -3.81)], 0.254),
        # common cathode tie to pin 2
        _poly([(1.27, 2.54), (2.54, 2.54), (2.54, -2.54), (1.27, -2.54)],
              0.254),
        _poly([(2.54, 0), (3.81, 0)], 0.254),
    ]
    pins = [_pin("1", "A1", "passive", -6.35, 2.54, "L", 2.54),
            _pin("2", "K", "passive", 6.35, 0, "R", 2.54),
            _pin("3", "A2", "passive", -6.35, -2.54, "L", 2.54)]
    return symbol("D_Schottky_Dual_CC", "D", "D_Schottky_Dual_CC", "", "~",
                  "Dual Schottky rectifier, common cathode", body, pins,
                  ref_at=(0, 5.588), val_at=(0, -5.588),
                  keywords="diode schottky dual common cathode")


def sym_diode_zener():
    extra = [_poly([(1.27, 1.27), (0.762, 1.27)], 0.254),
             _poly([(1.27, -1.27), (1.778, -1.27)], 0.254)]
    body = _diode_body(extra)
    pins = [_pin("1", "K", "passive", 3.81, 0, "R", 2.54),
            _pin("2", "A", "passive", -3.81, 0, "L", 2.54)]
    return symbol("D_Zener", "D", "D_Zener", "", "~", "Zener diode",
                  body, pins, hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(0, 2.54), val_at=(0, -2.54),
                  keywords="diode zener")


def sym_diode_tvs():
    extra = [_poly([(1.27, 1.27), (0.762, 1.27)], 0.254),
             _poly([(1.27, -1.27), (1.778, -1.27)], 0.254)]
    body = _diode_body(extra)
    pins = [_pin("1", "A1", "passive", 3.81, 0, "R", 2.54),
            _pin("2", "A2", "passive", -3.81, 0, "L", 2.54)]
    return symbol("D_TVS", "D", "D_TVS", "", "~",
                  "Bidirectional TVS suppressor", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(0, 2.54), val_at=(0, -2.54),
                  keywords="diode tvs transient suppressor")


def sym_nmos():
    body = [
        # gate bar
        _poly([(-2.54, 0), (-1.27, 0)], 0.254),
        _poly([(-1.27, 1.905), (-1.27, -1.905)], 0.254),
        # channel segments
        _poly([(-0.762, 1.905), (-0.762, 1.016)], 0.381),
        _poly([(-0.762, 0.508), (-0.762, -0.508)], 0.381),
        _poly([(-0.762, -1.016), (-0.762, -1.905)], 0.381),
        # drain
        _poly([(-0.762, 1.524), (2.54, 1.524), (2.54, 2.54)], 0.254),
        # source
        _poly([(-0.762, -1.524), (2.54, -1.524), (2.54, -2.54)], 0.254),
        # body connection + arrow
        _poly([(-0.762, 0), (2.54, 0), (2.54, -1.524)], 0.254),
        _poly([(0.508, 0), (1.524, 0.381), (1.524, -0.381), (0.508, 0)],
              0.254, fill="outline"),
        # body diode
        _poly([(3.302, 0.762), (3.302, -0.762)], 0.254),
    ]
    pins = [_pin("1", "G", "input", -5.08, 0, "L", 2.54),
            _pin("2", "D", "passive", 2.54, 5.08, "D", 2.54),
            _pin("3", "S", "passive", 2.54, -5.08, "U", 2.54)]
    return symbol("Q_NMOS_GDS", "Q", "Q_NMOS_GDS", "", "~",
                  "N-channel MOSFET, gate/drain/source", body, pins,
                  ref_at=(5.08, 2.54), val_at=(5.08, 0),
                  keywords="nmos mosfet transistor")


# --------------------------------------------------------------------------
# Integrated circuits (pinouts verified against datasheets)
# --------------------------------------------------------------------------

def _ic(name, ref, value, footprint, datasheet, description,
        left, right, width=17.78, keywords=None):
    """Build a rectangular IC symbol.

    left/right are lists of (number, name, etype); pins are spaced 2.54 mm
    starting 2.54 mm below the top edge.
    """
    rows = max(len(left), len(right))
    height = (rows + 1) * 2.54
    half_w, half_h = width / 2.0, height / 2.0
    body = [_rect(-half_w, half_h, half_w, -half_h)]
    pins = []
    for i, (num, pname, etype) in enumerate(left):
        y = half_h - 2.54 * (i + 1)
        pins.append(_pin(num, pname, etype, -half_w - 2.54, y, "L", 2.54))
    for i, (num, pname, etype) in enumerate(right):
        y = half_h - 2.54 * (i + 1)
        pins.append(_pin(num, pname, etype, half_w + 2.54, y, "R", 2.54))
    return symbol(name, ref, value, footprint, datasheet, description,
                  body, pins, ref_at=(0, half_h + 2.54),
                  val_at=(0, -half_h - 2.54), keywords=keywords)


def sym_tps40210():
    """TPS40210DGQ, MSOP-10 (DGQ).

    Pinout from SLUS772G Pin Functions table:
      1 RC, 2 SS, 3 DIS/EN, 4 COMP, 5 FB, 6 GND, 7 ISNS, 8 GDRV, 9 BP, 10 VDD
    """
    left = [("1", "RC", "passive"),
            ("2", "SS", "passive"),
            ("3", "DIS/EN", "input"),
            ("5", "FB", "input"),
            ("4", "COMP", "output")]
    right = [("10", "VDD", "power_in"),
             ("9", "BP", "power_out"),
             ("8", "GDRV", "output"),
             ("7", "ISNS", "input"),
             ("6", "GND", "power_in")]
    return _ic("TPS40210DGQ", "U", "TPS40210DGQ",
               "1v2boost:MSOP-10_3x3mm_P0.5mm",
               "https://www.ti.com/lit/ds/symlink/tps40210.pdf",
               "Wide-input non-synchronous boost controller, ground-referenced "
               "current sense, MSOP-10",
               left, right, width=20.32,
               keywords="boost controller pwm current mode")


def sym_ucc27511():
    """UCC27511A, SOT-23-6 (DBV).

    Pinout from SLUSAW9F Pin Functions - UCC27511:
      1 VDD, 2 OUTH, 3 OUTL, 4 GND, 5 IN-, 6 IN+
    Split OUTH/OUTL allow independent turn-on and turn-off gate resistors.
    """
    left = [("6", "IN+", "input"),
            ("5", "IN-", "input"),
            ("4", "GND", "power_in")]
    right = [("1", "VDD", "power_in"),
             ("2", "OUTH", "output"),
             ("3", "OUTL", "output")]
    return _ic("UCC27511A", "U", "UCC27511A", "1v2boost:SOT-23-6",
               "https://www.ti.com/lit/ds/symlink/ucc27511.pdf",
               "4-A/8-A single-channel low-side gate driver, split outputs",
               left, right, width=15.24,
               keywords="gate driver mosfet low-side")


def sym_tlv3011():
    """TLV3011, SOT-23-6 (DBV).

    Pinout from SBOS300C Pin Functions:
      1 OUT, 2 V-, 3 IN+, 4 IN-, 5 REF, 6 V+
    Open-drain output, 1.242 V integrated reference, 1.8-5.5 V supply.
    """
    left = [("3", "IN+", "input"),
            ("4", "IN-", "input"),
            ("2", "V-", "power_in")]
    right = [("6", "V+", "power_in"),
             ("1", "OUT", "open_collector"),
             ("5", "REF", "output")]
    return _ic("TLV3011", "U", "TLV3011", "1v2boost:SOT-23-6",
               "https://www.ti.com/lit/ds/symlink/tlv3011.pdf",
               "Nanopower open-drain comparator with 1.242 V reference",
               left, right, width=15.24,
               keywords="comparator reference open-drain")


# --------------------------------------------------------------------------
# Connectors, jumpers, test points, mechanical
# --------------------------------------------------------------------------

def sym_power_terminal():
    body = [_rect(-2.54, 2.54, 2.54, -2.54),
            _circle(1.27, 1.27, 0.635),
            _circle(1.27, -1.27, 0.635)]
    pins = [_pin("1", "+", "passive", -5.08, 1.27, "L", 2.54),
            _pin("2", "-", "passive", -5.08, -1.27, "L", 2.54)]
    return symbol("Conn_Power_2Pad", "J", "Conn_Power_2Pad", "", "~",
                  "High-current solder-direct terminal pair", body, pins,
                  ref_at=(0, 5.08), val_at=(0, -5.08),
                  keywords="connector terminal power solder pad")


def sym_header_2():
    body = [_rect(-1.27, 2.54, 1.27, -2.54),
            _rect(-0.635, 1.905, 0.635, 0.635),
            _rect(-0.635, -0.635, 0.635, -1.905)]
    pins = [_pin("1", "1", "passive", -3.81, 1.27, "L", 2.54),
            _pin("2", "2", "passive", -3.81, -1.27, "L", 2.54)]
    return symbol("Conn_01x02", "J", "Conn_01x02", "", "~",
                  "2-pin 2.54 mm header", body, pins,
                  ref_at=(0, 5.08), val_at=(0, -5.08),
                  keywords="connector header pin")


def sym_jumper():
    body = [_circle(-1.27, 0, 0.508, fill="outline"),
            _circle(1.27, 0, 0.508, fill="outline"),
            _poly([(-1.27, 1.016), (1.27, 1.016)], 0.254)]
    pins = [_pin("1", "1", "passive", -3.81, 0, "L", 2.54),
            _pin("2", "2", "passive", 3.81, 0, "R", 2.54)]
    return symbol("Jumper_2", "JP", "Jumper_2", "", "~",
                  "Solder jumper, 2 pad", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(0, 2.54), val_at=(0, -2.54),
                  keywords="jumper solder link")


def sym_testpoint():
    body = [_circle(0, 1.524, 0.762)]
    pins = [_pin("1", "1", "passive", 0, -2.54, "U", 2.54)]
    return symbol("TestPoint", "TP", "TestPoint", "", "~", "Test point",
                  body, pins, hide_pin_numbers=True, hide_pin_names=True,
                  ref_at=(0, 4.572), val_at=(0, -2.54),
                  keywords="test point probe tp")


def sym_mounting_hole():
    body = [_circle(0, 0, 1.27), _circle(0, 0, 0.635)]
    return symbol("MountingHole", "H", "MountingHole", "", "~",
                  "Mounting hole", body, [],
                  ref_at=(0, 3.302), val_at=(0, -3.302),
                  keywords="mounting hole mechanical")


def sym_fiducial():
    body = [_circle(0, 0, 0.508, fill="outline"), _circle(0, 0, 1.27)]
    return symbol("Fiducial", "FID", "Fiducial", "", "~",
                  "Fiducial marker for pick and place", body, [],
                  ref_at=(0, 3.302), val_at=(0, -3.302),
                  keywords="fiducial marker")


# --------------------------------------------------------------------------
# Power / ground symbols
# --------------------------------------------------------------------------

def sym_power_rail(name, description):
    body = [_poly([(-1.016, 1.27), (0, 2.54), (1.016, 1.27)], 0.254),
            _poly([(0, 0), (0, 2.54)], 0.254)]
    pins = [_pin("1", name, "power_in", 0, 0, "D", 0, hide=True)]
    return symbol(name, "#PWR", name, "", "~", description, body, pins,
                  hide_pin_numbers=True, hide_pin_names=True, in_bom=False,
                  ref_at=(0, -3.81), val_at=(0, 3.556),
                  keywords="power rail")


def sym_pwr_flag():
    """Marks a net as externally driven so ERC stops asking who powers it.

    Needed on rails that reach the board through a connector or a passive
    (VOUT arrives via the rectifier, V12 via the reverse-protection diode,
    V5C via the zener dropper) rather than from an IC's power output pin.
    """
    body = [_poly([(0, 0), (0, 1.27)], 0.254),
            _poly([(0, 1.27), (-1.016, 2.032), (0, 2.794), (1.016, 2.032),
                   (0, 1.27)], 0.254)]
    pins = [_pin("1", "pwr", "power_out", 0, 0, "D", 0, hide=True)]
    return symbol("PWR_FLAG", "#FLG", "PWR_FLAG", "", "~",
                  "Marks a net as power-driven for ERC", body, pins,
                  hide_pin_numbers=True, hide_pin_names=True, in_bom=False,
                  ref_at=(0, -1.27), val_at=(0, 3.556),
                  keywords="power flag erc")


def sym_ground(name, description):
    body = [_poly([(0, 0), (0, -1.27)], 0.254),
            _poly([(-1.27, -1.27), (1.27, -1.27)], 0.254),
            _poly([(-0.762, -1.905), (0.762, -1.905)], 0.254),
            _poly([(-0.254, -2.54), (0.254, -2.54)], 0.254)]
    pins = [_pin("1", name, "power_in", 0, 0, "D", 0, hide=True)]
    return symbol(name, "#PWR", name, "", "~", description, body, pins,
                  hide_pin_numbers=True, hide_pin_names=True, in_bom=False,
                  ref_at=(0, 1.27), val_at=(0, -3.81),
                  keywords="power ground gnd")


# --------------------------------------------------------------------------

def symbol_defs():
    """Return [(name, text), ...] for every symbol, and populate PIN_MAP."""
    return [
        sym_resistor(),
        sym_shunt_kelvin(),
        sym_capacitor(),
        sym_capacitor_pol(),
        sym_inductor(),
        sym_diode_schottky(),
        sym_diode_schottky_dual_cc(),
        sym_diode_zener(),
        sym_diode_tvs(),
        sym_nmos(),
        sym_tps40210(),
        sym_ucc27511(),
        sym_tlv3011(),
        sym_power_terminal(),
        sym_header_2(),
        sym_jumper(),
        sym_testpoint(),
        sym_mounting_hole(),
        sym_fiducial(),
        sym_pwr_flag(),
        sym_power_rail("VCELL", "1.2 V cell input rail"),
        sym_power_rail("VOUT", "12 V regulated output rail"),
        sym_power_rail("V12", "12 V auxiliary bias rail"),
        sym_power_rail("V5C", "5.1 V zener-derived comparator supply"),
        sym_ground("PGND", "Power ground"),
        sym_ground("AGND", "Analog / quiet ground (Kelvin shunt reference)"),
    ]


def build_library() -> str:
    parts = [text for _, text in symbol_defs()]
    head = ('(kicad_symbol_lib\n'
            '  (version 20241209)\n'
            '  (generator "1v2boost_gen")\n'
            '  (generator_version "9.0")\n')
    return head + "\n".join(parts) + "\n)\n"


def lib_symbols_block(prefix="1v2boost", indent="    "):
    """Symbol definitions re-emitted with library-qualified names.

    A .kicad_sch embeds a copy of every symbol it uses under `lib_symbols`,
    with names like "1v2boost:R".

    Only the top-level symbol takes the library prefix. The child unit symbols
    keep their bare "<name>_<unit>_<style>" form - KiCad rejects a colon in a
    unit name, and eeschema's own output writes them unqualified too.
    """
    out = []
    for name, text in symbol_defs():
        body = text.replace('  (symbol "%s"' % name,
                            '  (symbol "%s:%s"' % (prefix, name), 1)
        out.append("\n".join(indent + line for line in body.split("\n")))
    return "\n".join(out)


SYMBOL_NAMES = [
    "R", "R_Shunt_Kelvin", "C", "C_Polarized", "L", "D_Schottky",
    "D_Schottky_Dual_CC", "D_Zener",
    "D_TVS", "Q_NMOS_GDS", "TPS40210DGQ", "UCC27511A", "TLV3011",
    "Conn_Power_2Pad", "Conn_01x02", "Jumper_2", "TestPoint", "MountingHole",
    "Fiducial", "PWR_FLAG", "VCELL", "VOUT", "V12", "V5C", "PGND", "AGND",
]


if __name__ == "__main__":
    import sys, os
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "lib", "1v2boost.kicad_sym")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    text = build_library()
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("wrote %s (%d bytes, %d symbols)"
          % (out, len(text), text.count("\n  (symbol ")))
