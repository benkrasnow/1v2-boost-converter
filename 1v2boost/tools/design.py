"""
Design calculations for the 1.2 V -> 12 V / 20 W boost converter (TPS40210).

Single source of truth: the schematic generator, the BOM and docs/design-notes.md
all pull their numbers from here. Run directly to print the full design report.

    python tools/design.py

All datasheet references are to TPS40210/TPS40211, SLUS772G (rev June 2020).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------

VOUT = 12.0          # V, regulated output
POUT = 20.0          # W, required output power
IOUT = POUT / VOUT   # A, 1.667 A

VIN_NOM = 1.2        # V, nominal cell voltage
VIN_MIN = 0.9        # V, must still deliver full POUT here
VIN_MAX = 1.6        # V, highest expected cell voltage

# --------------------------------------------------------------------------
# TPS40210 datasheet constants (SLUS772G)
# --------------------------------------------------------------------------

VREF = 0.700         # V, error amplifier reference
V_ISNS_OC = 0.150    # V, overcurrent threshold at ISNS pin (typ; 120/150/180)
A_CS = 5.6           # V/V, current sense amplifier gain (typ; 4.2/5.6/7.2)
A_CS_MAX = 7.2       # V/V, worst case for the slope-comp constraint
T_OFF_MIN = 200e-9   # s, minimum off time (max spec; 170 ns typ)
T_ON_MIN = 400e-9    # s, minimum on time at VDD=12V (max spec; 275 ns typ)
R_SS_CHG = 430e3     # ohm, internal SS charge resistance (typ)
V_BP = 8.0           # V, internal regulator output
I_GDRV = 0.4         # A, gate drive source/sink current -> why U2 exists

# --------------------------------------------------------------------------
# Chosen operating point and components
# --------------------------------------------------------------------------

FSW = 100e3          # Hz
VDD = 12.0           # V, bias rail feeding the VDD pin (sets slope comp, Eq 17)

L = 1.5e-6           # H, Pulse Electronics PG1083.152NL
# Replaces the Coilcraft SER2915L-152KL, which is Digi-Key Marketplace only
# with zero direct stock. PG1083 is better on the term that dominates here -
# DCR 1.4 mohm max vs 1.65 - and smaller (21.7 x 21.5 x 10.6 mm vs 29 x 29),
# which is why the land pattern had to be redrawn.
#
# What is given up is saturation margin. The SER2915 is a powder core rated
# Isat > 100 A that rolls off gradually; PG1083 is rated 52 A. That still
# clears the 30.7 A worst-case peak by 1.7x, but the overcurrent backstop no
# longer sits below the saturation current, so a hard fault saturates the
# inductor before the comparator trips. See section 4 of the design notes -
# this is why the shunt value matters more than it used to.
L_DCR = 1.4e-3       # ohm (max)
L_IRMS_20C = 30.0    # A, conservative read of the 40 A rating
L_IRMS_40C = 40.0    # A, manufacturer current rating
L_ISAT = 52.0        # A
# Bourns CSS2H-2512R-1L00F, 1 mohm, 5 W, 2512.
# NOT the 4-terminal Kelvin part this design was originally drawn around: no
# 1.5 mohm value exists in that family and the entire CSS2H-2512 series is
# 2-terminal. Sense is taken at the 0.40 mm fingers in Bourns' own land
# pattern, which leave the current path before the tap, so the Kelvin geometry
# survives even though the part has only two terminals. The value is set by the
# slope-compensation ceiling, not by loss - 1 mohm is the largest stocked value
# that stays under it (1.8 and 2 mohm do not).
R_ISNS = 1.0e-3      # ohm
# Vishay SUP40012EL-GE3, TO-220AB: 40 V, 150 A, Vgs(th) 2.5 V max, Vgs +-20 V.
# Replaces the obsolete IRLB3034PBF. Values below are from datasheet 76965,
# extracted rather than recalled: RDS(on) 1.49 mohm typ / 1.79 max at
# Vgs = 10 V, Id = 30 A; Qg 130 nC typ; Qgs 33.6 nC; Qgd 6.7 nC typ.
# The Qgd is the headline - 6.7 nC against the IRLB3034's 40 nC - so switching
# loss drops several fold and more than pays for the slightly higher Rds(on).
RDSON_1 = 1.49e-3    # ohm, single device at 25 C (typ)
N_FET = 2            # paralleled low-side FETs
RDSON_HOT = 1.4      # multiplier at operating junction temperature
# STPS40L45CT, low-drop dual Schottky in TO-263 (D2PAK), common cathode.
# Both elements paralleled: 45 V, 40 A total. Vf ~0.40 V at ~15 A per element.
VF_DIODE = 0.40      # V
QGD_TOTAL = 13.4e-9  # C, total Miller charge of the paralleled FETs (2x 6.7 nC)
QG_TOTAL = 260e-9    # C, total gate charge at 12 V (2x 130 nC)
COSS_TOTAL = 4.0e-9  # F, effective output capacitance
R_PATH_IN = 0.5e-3   # ohm, input solder pads + PCB copper budget
P_CORE = 0.15        # W, inductor core loss estimate
P_CAPS = 0.19        # W, capacitor ESR + snubber

# Feedback divider: VOUT = VREF * (1 + RFB_TOP/RFB_BOT)
RFB_TOP = 100e3
RFB_BOT = 6.19e3

# --------------------------------------------------------------------------
# Capacitor banks - all ceramic, no polymer
#
# The polymer bulk caps and the 22 uF ceramics were removed in favour of a
# single ceramic type per rail: fewer parts, two fewer BOM lines, and none of
# the polymer cost. The consequence is a 7x drop in output capacitance, which
# is not a swap - it moves the loop, so the compensation below is recomputed
# from these numbers rather than carried over.
#
# DC-bias derating dominates the choice and the two rails behave very
# differently. A 25 V part at 12 V loses most of its capacitance; a 6.3 V part
# at 1.2 V barely notices. Both figures below are conservative assumptions and
# are flagged in the BOM: confirm them against the manufacturer's bias curve
# for the exact part before committing.
COUT_N = 8
COUT_NOM = 47e-6         # F, 47uF/25V X5R 1210
COUT_DERATE = 0.40       # at 12 V bias
COUT_TOTAL = COUT_N * COUT_NOM * COUT_DERATE

CIN_N = 6                # main bank
# The damping leg has to grow now that the bank is ceramic. Previously the
# 470 uF polymer's own ESR did most of the damping; low-ESR ceramics raise the
# input filter's Q, so R1 needs a capacitor comparable to the bank behind it
# rather than a token one.
CIN_DAMP_N = 3           # series-R damping leg with R1
CIN_NOM = 100e-6         # F, 100uF/6.3V X5R 1210
CIN_DERATE = 0.70        # at 1.2 V bias - a 6.3 V part is barely stressed
CIN_TOTAL = CIN_N * CIN_NOM * CIN_DERATE
CIN_DAMP = CIN_DAMP_N * CIN_NOM * CIN_DERATE

# ESR per ceramic at 100 kHz, and RMS ripple current a 1210 X5R can carry
# before self-heating becomes the limit.
CER_ESR = 3e-3           # ohm
CER_IRMS_1210 = 2.0      # A


def cout_ripple_pp(vin: float) -> float:
    """Output ripple. During t_on the bank alone carries the load."""
    op = solve_operating_point(vin)
    return IOUT * op.duty / (FSW * COUT_TOTAL) + op.irms_cout * CER_ESR


def cin_ripple_pp(vin: float) -> float:
    """Input ripple: triangular inductor ripple into the bank, plus ESR."""
    op = solve_operating_point(vin)
    return (op.i_ripple / (8 * FSW * CIN_TOTAL)
            + op.i_ripple * CER_ESR / CIN_N)


# --------------------------------------------------------------------------
# Operating point solver
# --------------------------------------------------------------------------

@dataclass
class OperatingPoint:
    vin: float
    duty: float
    iin: float
    i_ripple: float
    i_peak: float
    irms_switch: float
    irms_diode: float
    irms_cout: float
    losses: dict
    pin: float
    pout: float
    efficiency: float

    @property
    def p_loss(self) -> float:
        return sum(self.losses.values())


def solve_operating_point(vin: float, pout: float = POUT,
                          iterations: int = 60) -> OperatingPoint:
    """Self-consistently solve duty, input current and losses at a given Vin.

    Losses depend on current, current depends on losses, so iterate to a fixed
    point rather than assuming an efficiency.
    """
    iout = pout / VOUT
    iin = pout / (vin * 0.85)   # seed
    duty = 1.0 - vin / VOUT

    losses: dict = {}
    for _ in range(iterations):
        # Series resistance seen by the input current, averaged over the cycle.
        r_series = (R_PATH_IN + L_DCR
                    + duty * (R_ISNS + RDSON_1 / N_FET * RDSON_HOT))
        v_eff = vin - iin * r_series
        # Duty from the real conversion ratio, including the rectifier drop.
        duty = 1.0 - v_eff / (VOUT + VF_DIODE)
        duty = min(max(duty, 0.0), 0.99)

        i_ripple = v_eff * duty / (L * FSW)
        irms_sw = iin * math.sqrt(duty)

        losses = {
            "input path": iin ** 2 * R_PATH_IN,
            "inductor DCR": iin ** 2 * L_DCR,
            "inductor core": P_CORE,
            "shunt R_ISNS": irms_sw ** 2 * R_ISNS,
            "FET conduction": irms_sw ** 2 * (RDSON_1 / N_FET * RDSON_HOT),
            "FET switching": 0.5 * (VOUT + VF_DIODE) * iin
                             * (2 * QGD_TOTAL / I_GDRV_EFF) * FSW,
            "FET Coss": 0.5 * COSS_TOTAL * (VOUT + VF_DIODE) ** 2 * FSW,
            "Schottky D1": VF_DIODE * iout,
            "caps + snubber": P_CAPS,
        }
        pin_new = pout + sum(losses.values())
        iin_new = pin_new / vin
        if abs(iin_new - iin) < 1e-9:
            iin = iin_new
            break
        iin = 0.5 * iin + 0.5 * iin_new   # damped update

    pin = pout + sum(losses.values())
    i_ripple = (vin - iin * (R_PATH_IN + L_DCR)) * duty / (L * FSW)
    return OperatingPoint(
        vin=vin,
        duty=duty,
        iin=iin,
        i_ripple=i_ripple,
        i_peak=iin + i_ripple / 2,
        irms_switch=iin * math.sqrt(duty),
        irms_diode=iin * math.sqrt(1 - duty),
        irms_cout=iout * math.sqrt(duty / (1 - duty)),
        losses=losses,
        pin=pin,
        pout=pout,
        efficiency=pout / pin,
    )


# Effective gate drive current with the UCC27511A (4 A source / 8 A sink).
# Using the weaker of the two edges is the conservative choice.
I_GDRV_EFF = 4.0


# --------------------------------------------------------------------------
# Derived design values
# --------------------------------------------------------------------------

def duty_max(fsw: float = FSW) -> float:
    """Max achievable duty, set solely by minimum off time (no hard duty spec)."""
    return 1.0 - T_OFF_MIN * fsw


def slope_compensation_slope(vdd: float = VDD, fsw: float = FSW) -> float:
    """Eq 17: S_e = (V_DD / 20) * f_SW, in V/s at the PWM comparator."""
    return (vdd / 20.0) * fsw


def r_isns_max(vin: float, a_cs: float = A_CS) -> float:
    """Eq 19: value making S_e exactly half the current down-slope.

    TI recommends using no more than 80% of this. Note the constraint pushes
    R_ISNS *down*, which happens to align with the efficiency requirement.
    """
    se = slope_compensation_slope()
    return 2.0 * se * L / (a_cs * (VOUT + VF_DIODE - vin))


def current_downslope(vin: float, r_isns: float = R_ISNS) -> float:
    """Eq 18: m2, down-slope of the sense waveform at the PWM comparator, V/s."""
    return A_CS * r_isns * (VOUT + VF_DIODE - vin) / L


def oscillator_rt(fsw: float = FSW, ct: float = 330e-12) -> float:
    """Timing resistor for a given frequency.

    Calibrated against the datasheet EC test condition
    (R_T = 182 kohm, C_T = 330 pF -> 300 kHz), giving R*C*f ~= 18.0.
    """
    return 18.0 / (ct * fsw)


def rhp_zero(vin: float, rload: float | None = None) -> float:
    """Right-half-plane zero of the boost power stage, Hz."""
    rload = rload if rload is not None else VOUT / IOUT
    dprime = vin / VOUT
    return dprime ** 2 * rload / (2 * math.pi * L)


def modulator_dc_gain(vin: float, rload: float | None = None) -> float:
    rload = rload if rload is not None else VOUT / IOUT
    dprime = vin / VOUT
    return rload * dprime / (2 * R_ISNS * A_CS)


def load_pole(rload: float | None = None) -> float:
    rload = rload if rload is not None else VOUT / IOUT
    return 2.0 / (2 * math.pi * rload * COUT_TOTAL)


def compensation(fc: float = 600.0, vin: float = VIN_NOM) -> dict:
    """Type II compensation around the internal voltage-mode error amplifier.

    COMP/FB form an inverting network: v_comp/v_out = -Z_comp / RFB_TOP.
    """
    a_m = modulator_dc_gain(vin)
    fp = load_pole()
    r_comp = RFB_TOP * fc / (a_m * fp)
    f_zero = fc / 4.0
    c_comp = 1.0 / (2 * math.pi * r_comp * f_zero)
    f_hf = FSW / 10.0
    c_hf = 1.0 / (2 * math.pi * r_comp * f_hf)
    return {
        "fc": fc, "a_m": a_m, "f_pole": fp,
        "r_comp": r_comp, "c_comp": c_comp, "c_hf": c_hf,
        "f_zero": f_zero, "f_hf_pole": f_hf,
    }


def vout_actual() -> float:
    return VREF * (1 + RFB_TOP / RFB_BOT)


def oc_trip_current() -> float:
    """Peak switch current at which the 150 mV OC comparator trips."""
    return V_ISNS_OC / R_ISNS


def soft_start_time(c_ss: float) -> float:
    """Approximate time for SS to rise through the usable ramp window."""
    return R_SS_CHG * c_ss * math.log(V_BP / (V_BP - VREF - 0.7))


# --------------------------------------------------------------------------
# Cell undervoltage lockout (U3, TLV3011)
#
# Nothing inside the TPS40210 watches the cell. It runs from the 12 V bias, so
# its own UVLO looks at VDD, and the 150 mV overcurrent threshold across a
# 1.5 mohm shunt trips at 100 A - a short-circuit backstop, not a brownout
# detector. Without U3 the converter keeps trying at any input voltage, and
# because a regulating converter is a constant-power load the input current
# climbs as the cell sags: 26 A at 0.9 V, ~50 A near 0.5 V, past the
# inductor's saturation rating. It does not fail gracefully - it collapses
# the input, hiccup-retries through soft start, and repeats at full current.
#
# Divider: REF (1.242 V) - R13 - CMP_TH - R14 - AGND, with R15 from CMP_TH to
# the open-drain output for hysteresis and R16 pulling that output up to the
# zener rail. Cell on IN-, threshold on IN+, so cell-OK -> output low ->
# DIS/EN low -> enabled.
#
# These numbers used to live only in a comment, and the comment was wrong -
# it claimed 0.90 V rising / 0.84 V falling with 60 mV of hysteresis, where
# the fitted parts give 1.045 V / 0.837 V and 207 mV. The resistors are
# correct and are left alone; 26k1/100k lands at 1.65x the recovery step,
# against 1.71x for the best E96 pair that meets every constraint. What was
# missing was the calculation, so it is written down here and checked.
# --------------------------------------------------------------------------

UVLO_VREF = 1.242      # V, TLV3011 internal reference (pin 5)
UVLO_VSUP = 5.1        # V, local zener rail from R12/D4 (TLV3011 max 5.5 V)
UVLO_R_TOP = 10e3      # R13, REF -> CMP_TH
UVLO_R_BOT = 26.1e3    # R14, CMP_TH -> AGND
UVLO_R_HYST = 100e3    # R15, CMP_TH -> OUT
UVLO_R_PULLUP = 100e3  # R16, VSUP -> OUT

# Series resistance upstream of the sense point: cell internal + leads +
# connector + input solder pads. ASSUMPTION - the cell is characterised only
# as "low internal resistance". It sets the size the hysteresis has to beat,
# so measure it during bring-up and re-run this if it is materially higher.
R_SRC_UVLO = 4e-3      # ohm

# Open-circuit voltage of a healthy cell. The rising threshold is compared
# against the UNLOADED cell - at release there is no load yet - so this, not
# the 1.2 V loaded nominal, is what bounds it from above. ASSUMPTION.
V_CELL_OC = 1.25       # V

# Margin required between the hysteresis and the recovery step. 1.0 would be
# the bare edge of not chattering; this is the usual factor for a threshold
# built on an assumed source resistance.
UVLO_MARGIN = 1.4

# Logic-high level the DIS/EN pin must reach to hold the controller off.
# ASSUMPTION, not extracted from SLUS772G. R15 loads the R16 pullup, so the
# high level is worth computing even against an approximate threshold.
V_DIS_HIGH_MIN = 2.0   # V


def _uvlo_threshold(r_pull: float, v_pull: float) -> float:
    """CMP_TH with one extra resistor r_pull returning to v_pull."""
    g = 1 / UVLO_R_TOP + 1 / UVLO_R_BOT + 1 / r_pull
    i = UVLO_VREF / UVLO_R_TOP + v_pull / r_pull
    return i / g


def uvlo_thresholds() -> dict:
    """Rising and falling cell thresholds, plus the resulting DIS/EN level.

    Output low (converter enabled): R15 hangs from CMP_TH to ground and pulls
    the threshold down - that is the falling trip point. Output released:
    R15 and R16 are one series path from the zener rail into CMP_TH, pushing
    the threshold up - the rising release point. R15 sets both, which is why
    it cannot be picked independently of R14.

    The comparator's own offset (+-1 mV) and the reference tolerance (0.5%,
    ~6 mV) are small against the resistor tolerances and are not modelled.
    """
    v_fall = _uvlo_threshold(UVLO_R_HYST, 0.0)
    r_series = UVLO_R_HYST + UVLO_R_PULLUP
    v_rise = _uvlo_threshold(r_series, UVLO_VSUP)
    # With the output released, the only path into EN is R16 -> R15 -> CMP_TH,
    # so the pin is a divider tap and does not reach the rail.
    i_pull = (UVLO_VSUP - v_rise) / r_series
    return {
        "rising": v_rise,
        "falling": v_fall,
        "hysteresis": v_rise - v_fall,
        "v_en_high": UVLO_VSUP - i_pull * UVLO_R_PULLUP,
    }


def uvlo_recovery_step(v_fall: float | None = None) -> float:
    """How far the cell springs back the instant the UVLO drops the load.

    The hysteresis has to exceed this. If it does not, simply removing the
    load lifts the cell past the rising threshold on its own, the converter
    restarts into the same sag, and the UVLO turns into a slow hiccup
    oscillator - the exact failure mode it exists to prevent.
    """
    if v_fall is None:
        v_fall = uvlo_thresholds()["falling"]
    return solve_operating_point(v_fall).iin * R_SRC_UVLO


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

CORNERS = [VIN_MAX, VIN_NOM, VIN_MIN]


def report() -> str:
    out = []
    w = out.append
    w("=" * 74)
    w("  1.2 V -> 12 V / 20 W BOOST CONVERTER - DESIGN REPORT")
    w("  Controller: TPS40210DGQ   Driver: UCC27511A   f_sw = %.0f kHz"
      % (FSW / 1e3))
    w("=" * 74)

    w("\n-- Duty cycle headroom " + "-" * 51)
    w("  Max duty (1 - t_OFF(min)*f_sw, worst case 200 ns) : %.3f"
      % duty_max())
    for vin in CORNERS:
        op = solve_operating_point(vin)
        w("  Required duty at Vin = %.2f V                      : %.3f  %s"
          % (vin, op.duty, "OK" if op.duty < duty_max() else "FAIL"))
    w("  Min on-time %.0f ns vs actual on-time at 0.9 V     : %.2f us"
      % (T_ON_MIN * 1e9, solve_operating_point(VIN_MIN).duty / FSW * 1e6))

    w("\n-- Operating points " + "-" * 54)
    w("  %-8s %7s %7s %8s %8s %8s %7s" %
      ("Vin", "Duty", "Iin", "Ipk", "Ploss", "Pout", "Eff"))
    for vin in CORNERS:
        op = solve_operating_point(vin)
        w("  %-8s %7.3f %6.1fA %7.1fA %7.2fW %7.1fW %6.1f%%" %
          ("%.2f V" % vin, op.duty, op.iin, op.i_peak,
           op.p_loss, op.pout, op.efficiency * 100))

    w("\n-- Loss breakdown at Vin = 1.20 V " + "-" * 40)
    op = solve_operating_point(VIN_NOM)
    for k, v in sorted(op.losses.items(), key=lambda kv: -kv[1]):
        w("  %-22s %6.3f W   (%4.1f%% of Pin)" % (k, v, 100 * v / op.pin))
    w("  %-22s %6.3f W" % ("TOTAL", op.p_loss))
    w("  Pin = %.2f W, Pout = %.2f W, efficiency = %.1f%%"
      % (op.pin, op.pout, op.efficiency * 100))

    w("\n-- Current stress " + "-" * 56)
    for vin in CORNERS:
        op = solve_operating_point(vin)
        w("  Vin %.2f V: I_ripple %.1f A, I_peak %.1f A, "
          "Irms(FET) %.1f A, Irms(Cout) %.1f A"
          % (vin, op.i_ripple, op.i_peak, op.irms_switch, op.irms_cout))

    w("\n-- Slope compensation (Eq 17-19) " + "-" * 41)
    w("  S_e = (VDD/20)*f_sw                  : %.0f V/s" %
      slope_compensation_slope())
    for vin in CORNERS:
        w("  Vin %.2f V: m2 = %.0f V/s, S_e/m2 = %.2f "
          "(1.0 = ideal, >=0.5 required)"
          % (vin, current_downslope(vin),
             slope_compensation_slope() / current_downslope(vin)))
    w("  R_ISNS(max) at Vin=0.9V, typ gain    : %.3f mohm (80%% -> %.3f)"
      % (r_isns_max(VIN_MIN) * 1e3, r_isns_max(VIN_MIN) * 0.8 * 1e3))
    w("  R_ISNS(max) at Vin=0.9V, worst gain  : %.3f mohm (80%% -> %.3f)"
      % (r_isns_max(VIN_MIN, A_CS_MAX) * 1e3,
         r_isns_max(VIN_MIN, A_CS_MAX) * 0.8 * 1e3))
    w("  Chosen R_ISNS                        : %.3f mohm  %s"
      % (R_ISNS * 1e3,
         "OK" if R_ISNS <= 0.8 * r_isns_max(VIN_MIN, A_CS_MAX) else "FAIL"))

    w("\n-- Control loop " + "-" * 58)
    for vin in CORNERS:
        w("  RHP zero at Vin = %.2f V             : %.2f kHz"
          % (vin, rhp_zero(vin) / 1e3))
    comp = compensation()
    w("  Load pole                            : %.1f Hz" % comp["f_pole"])
    w("  Modulator DC gain                    : %.1f (%.1f dB)"
      % (comp["a_m"], 20 * math.log10(comp["a_m"])))
    w("  Target crossover                     : %.0f Hz" % comp["fc"])
    w("  R_COMP                               : %.1f kohm" %
      (comp["r_comp"] / 1e3))
    w("  C_COMP (zero at %.0f Hz)              : %.1f nF"
      % (comp["f_zero"], comp["c_comp"] * 1e9))
    w("  C_HF   (pole at %.0f kHz)             : %.0f pF"
      % (comp["f_hf_pole"] / 1e3, comp["c_hf"] * 1e12))

    w("\n-- Component values " + "-" * 54)
    w("  Oscillator: C_T = 330 pF -> R_T      : %.0f kohm" %
      (oscillator_rt() / 1e3))
    w("  Feedback divider %.0f k / %.2f k      : Vout = %.3f V"
      % (RFB_TOP / 1e3, RFB_BOT / 1e3, vout_actual()))
    w("  Overcurrent trip (peak switch)       : %.0f A" % oc_trip_current())
    w("  Soft start, C_SS = 220 nF            : %.0f ms"
      % (soft_start_time(220e-9) * 1e3))
    irms = solve_operating_point(VIN_MIN).irms_cout
    w("\n-- Capacitor banks (all ceramic) " + "-" * 41)
    w("  Output %dx %.0f uF/25V 1210, %.0f%% bias : %.0f uF effective"
      % (COUT_N, COUT_NOM * 1e6, COUT_DERATE * 100, COUT_TOTAL * 1e6))
    w("  Output ripple current / cap          : %.1f A rms total, %.2f A each"
      % (irms, irms / COUT_N))
    w("  Output ripple at 1.2 V / 0.9 V       : %.0f mV / %.0f mV pp"
      % (cout_ripple_pp(VIN_NOM) * 1e3, cout_ripple_pp(VIN_MIN) * 1e3))
    w("  Input  %dx %.0f uF/6.3V 1210, %.0f%% bias: %.0f uF effective"
      % (CIN_N, CIN_NOM * 1e6, CIN_DERATE * 100, CIN_TOTAL * 1e6))
    w("  Input  damping leg %dx + R1          : %.0f uF"
      % (CIN_DAMP_N, CIN_DAMP * 1e6))
    w("  Input ripple at 1.2 V / 0.9 V        : %.0f mV / %.0f mV pp"
      % (cin_ripple_pp(VIN_NOM) * 1e3, cin_ripple_pp(VIN_MIN) * 1e3))

    uv = uvlo_thresholds()
    step = uvlo_recovery_step()
    w("\n-- Cell undervoltage lockout (U3) " + "-" * 40)
    w("  Divider R13/R14 %.0fk / %.1fk, R15 %.0fk, R16 %.0fk"
      % (UVLO_R_TOP / 1e3, UVLO_R_BOT / 1e3,
         UVLO_R_HYST / 1e3, UVLO_R_PULLUP / 1e3))
    w("  Rising (release) threshold           : %.3f V" % uv["rising"])
    w("  Falling (trip) threshold             : %.3f V" % uv["falling"])
    w("  Hysteresis                           : %.0f mV"
      % (uv["hysteresis"] * 1e3))
    w("  Cell recovery step at trip (%.0f mohm) : %.0f mV  (%.1f A x R_src)"
      % (R_SRC_UVLO * 1e3, step * 1e3,
         solve_operating_point(uv["falling"]).iin))
    w("  Hysteresis / recovery step           : %.2fx  (need %.1fx)"
      % (uv["hysteresis"] / step, UVLO_MARGIN))
    w("  DIS/EN level when disabled           : %.2f V" % uv["v_en_high"])

    w("\n-- Checks " + "-" * 63)
    checks = [
        ("Duty at 0.9 V below max duty",
         solve_operating_point(VIN_MIN).duty < duty_max()),
        ("Full 20 W delivered at 0.9 V",
         solve_operating_point(VIN_MIN).pout >= POUT - 1e-6),
        ("Efficiency at 1.2 V above 85%",
         solve_operating_point(VIN_NOM).efficiency > 0.85),
        ("R_ISNS satisfies slope comp (worst-case gain)",
         R_ISNS <= 0.8 * r_isns_max(VIN_MIN, A_CS_MAX)),
        ("Slope comp at least half the down-slope at all corners",
         all(slope_compensation_slope() / current_downslope(v) >= 0.5
             for v in CORNERS)),
        ("Crossover at least 5x below worst RHP zero",
         compensation()["fc"] <= min(rhp_zero(v) for v in CORNERS) / 5),
        ("Output voltage within 1% of 12 V",
         abs(vout_actual() - VOUT) / VOUT < 0.01),
        ("Inductor peak current below saturation (%.0f A)" % L_ISAT,
         solve_operating_point(VIN_MIN).i_peak < L_ISAT),
        ("Inductor DC current below 40 C rise rating (%.0f A)" % L_IRMS_40C,
         solve_operating_point(VIN_MIN).iin < L_IRMS_40C),
        ("Timing resistor within 100k-1M",
         100e3 <= oscillator_rt() <= 1e6),
        ("Output ripple under 2% of 12 V at worst corner",
         cout_ripple_pp(VIN_MIN) < 0.02 * VOUT),
        ("Output cap ripple current within 1210 rating (%.1f A each)"
         % CER_IRMS_1210,
         solve_operating_point(VIN_MIN).irms_cout / COUT_N < CER_IRMS_1210),
        ("Input ripple under 5% of 0.9 V at worst corner",
         cin_ripple_pp(VIN_MIN) < 0.05 * VIN_MIN),
        ("Damping leg at least a third of the main input bank",
         CIN_DAMP >= CIN_TOTAL / 3),
        ("UVLO hysteresis beats the %.0f mV recovery step by %.1fx"
         % (uvlo_recovery_step() * 1e3, UVLO_MARGIN),
         uvlo_thresholds()["hysteresis"]
         >= UVLO_MARGIN * uvlo_recovery_step()),
        ("UVLO trips at or below the 0.9 V full-power corner",
         uvlo_thresholds()["falling"] <= VIN_MIN),
        ("UVLO releases below a healthy open-circuit cell (%.2f V)"
         % V_CELL_OC,
         uvlo_thresholds()["rising"] <= V_CELL_OC - 0.15),
        ("DIS/EN reaches a logic high when disabled (>%.1f V)"
         % V_DIS_HIGH_MIN,
         uvlo_thresholds()["v_en_high"] > V_DIS_HIGH_MIN),
    ]
    for name, ok in checks:
        w("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    w("")
    w("  %d/%d checks passed" % (sum(1 for _, o in checks if o), len(checks)))
    w("=" * 74)
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(report())
