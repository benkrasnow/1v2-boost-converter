"""
The circuit: every component, its pin-to-net mapping, and its BOM data.

This is the single authoritative description of the design. The schematic,
the PCB and the BOM are all generated from it, so they cannot disagree.

Distributor note
----------------
Every fitted part carries a `dk` field: a Digi-Key part number that was looked
up on digikey.com on 2026-07-25 and found Active, with non-zero *direct* stock.
Marketplace listings were rejected. Cut Tape (CT) numbers are used wherever
Digi-Key offers one, so the quantities here can be ordered as-is.

This was not true before. Of the 14 part numbers this file used to carry, six
did not exist at all and one was Marketplace-only; four of the four Digi-Key
numbers that were spot-checked were wrong. They had been written down without
ever being checked against a distributor. Do not add an MPN or a `dk` value to
this file that you have not personally seen on the distributor's site.

Grounding note
--------------
PGND and AGND are deliberately separate nets. Their ONLY connection is at the
low-side terminal of the shunt R2: pin 2, the solder pad carrying the return
current, is PGND; pin 4, the sense finger on that same terminal, is AGND.

R2 is a two-terminal part, so pins 2 and 4 are one node - the footprint says
so with a net tie. The Kelvin behaviour is geometric, not electrical: the
finger runs out past the body into copper that carries no load current, so a
trace leaving it does not pick up the IR drop of the copper feeding the shunt.
Attach AGND at the finger, never at the solder pad, and add no other AGND-PGND
connection anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Comp:
    ref: str
    symbol: str                 # symbol name in 1v2boost.kicad_sym
    value: str
    footprint: str              # footprint name in 1v2boost.pretty
    nets: dict                  # pin number (str) -> net name
    mpn: str = ""
    mfr: str = ""
    dk: str = ""                # Digi-Key part number, Cut Tape where offered
    descr: str = ""
    dnp: bool = False
    in_bom: bool = True
    # schematic placement, mm, filled in below
    at: tuple = (0.0, 0.0)
    rot: int = 0


C = []          # component list, populated in order


def add(**kw):
    c = Comp(**kw)
    C.append(c)
    return c


# ==========================================================================
# Input terminals, bulk and damping
# ==========================================================================

add(ref="J1", symbol="Conn_Power_2Pad", value="CELL 1.2V 28A",
    footprint="SolderPad_Pair_Input_14x10mm",
    nets={"1": "VCELL", "2": "PGND"},
    descr="Cell input, solder wire or braid directly")

# Input bank: all ceramic, one part number. The 22 uF ceramics and the 470 uF
# polymers they sat beside are both gone. Boost input current is continuous
# (L1 is in series with the source), so this bank never sees the fast
# switching edges - it absorbs the 5-9 A of inductor ripple, and a 6.3 V part
# at 1.2 V bias keeps ~70% of its rated value, which is why one ceramic type
# can replace both banks here. See tools/design.py for the ripple numbers.
for i in range(1, 7):
    add(ref="C%d" % i, symbol="C", value="100u/6.3V X5R",
        footprint="C_1210_3225Metric",
        nets={"1": "VCELL", "2": "PGND"},
        mpn="CL32A107MQVNNWE", mfr="Samsung", dk="1276-3366-1-ND",
        descr="Ceramic 100uF 6.3V X5R 1210")

# Damping leg. A low-impedance cell on long leads can resonate with the input
# capacitance; R1 gives the network a real part so the converter's negative
# input resistance cannot sustain oscillation. This leg grew from one cap to
# three: the 470 uF polymer it replaced contributed its own ESR to the
# damping, and low-ESR ceramics do not, so the leg needs capacitance
# comparable to the main bank to do the same job.
for i in range(7, 10):
    add(ref="C%d" % i, symbol="C", value="100u/6.3V X5R",
        footprint="C_1210_3225Metric",
        nets={"1": "VCELL", "2": "DAMP"},
        mpn="CL32A107MQVNNWE", mfr="Samsung", dk="1276-3366-1-ND",
        descr="Ceramic 100uF 6.3V X5R 1210, input damping leg")
add(ref="R1", symbol="R", value="0R10 1W",
    footprint="R_1210_3225Metric",
    nets={"1": "DAMP", "2": "PGND"},
    mpn="RCWE1210R100FKEA", mfr="Vishay Dale", dk="541-4390-1-ND",
    descr="0.1 ohm 1% 1W 1210 current sense, input filter damping")

# ==========================================================================
# Power stage
# ==========================================================================

# Only pads 1 and 2 appear below. Pad 3 of the PG1083 is a mounting tab whose
# lead terminates in free space in the P716 schematic, so it deliberately gets
# no net; giving it one would tie the board to the conductive core.
add(ref="L1", symbol="L", value="1.5uH PG1083.152NL",
    footprint="L_Pulse_PG1083",
    nets={"1": "VCELL", "2": "SW"},
    mpn="PG1083.152NL", mfr="Pulse Electronics", dk="553-3164-ND",
    descr="1.5uH 1.4mohm Isat 52A Irms 40A round wire inductor")

add(ref="Q1", symbol="Q_NMOS_GDS", value="SUP40012EL-GE3",
    footprint="TO-220-3_Vertical",
    nets={"1": "GATE", "2": "SW", "3": "SRC"},
    mpn="SUP40012EL-GE3", mfr="Vishay", dk="SUP40012EL-GE3-ND",
    descr="N-MOSFET 40V 150A 1.49mohm logic level TO-220AB")
add(ref="Q2", symbol="Q_NMOS_GDS", value="SUP40012EL-GE3",
    footprint="TO-220-3_Vertical",
    nets={"1": "GATE", "2": "SW", "3": "SRC"},
    mpn="SUP40012EL-GE3", mfr="Vishay", dk="SUP40012EL-GE3-ND",
    descr="N-MOSFET 40V 150A 1.49mohm logic level TO-220AB")

# Current sense shunt. Sets both the current-mode ramp and the overcurrent
# trip. See design.py: slope compensation caps this at 1.73 mohm.
#
# The part is the Bourns CSS2H-2512R-1L00F, a 1 mohm TWO-terminal shunt. The
# 1.5 mohm 4-terminal Kelvin part this board was originally drawn around -
# CSS2H-2512R-L150F - does not exist; "CSS2H" decodes as 2 terminals in the
# datasheet's ordering table, and no 1.5 mohm value is made in the family. The
# 1.8/2/3 mohm parts that are stocked are all above the 1.73 mohm slope-comp
# ceiling, so 1 mohm is the only legal in-stock choice.
#
# The four pins below survive that change and are not a leftover. Bourns'
# recommended land pattern grows a 0.40 mm sense finger off each solder pad,
# running out past the body where it carries no load current. Pins 1 and 3 are
# one terminal and pins 2 and 4 are the other; the footprint says so with
# (net_tie_pad_groups "1, 3" "2, 4"). Keeping ISNS_K and AGND as their own nets
# is what makes "the sense trace starts at the finger" a rule the tools can
# check rather than a comment nobody reads.
add(ref="R2", symbol="R_Shunt_Kelvin", value="1m0 5W",
    footprint="R_Shunt_2512_CSS2H",
    nets={"1": "SRC", "2": "PGND", "3": "ISNS_K", "4": "AGND"},
    mpn="CSS2H-2512R-1L00F", mfr="Bourns", dk="CSS2H-2512R-1L00FCT-ND",
    descr="1 mohm 5W current sense, 2512 (2-terminal)")

# Dual common-cathode Schottky, both halves paralleled.
add(ref="D1", symbol="D_Schottky_Dual_CC", value="STPS40L45CT",
    footprint="TO-220-3_Vertical",
    nets={"1": "SW", "2": "VOUT", "3": "SW"},
    mpn="STPS40L45CT", mfr="STMicroelectronics", dk="497-7567-5-ND",
    descr="Schottky 45V 2x20A low drop, common cathode, TO-220AB")

# Snubber across the switch node. TO-220 lead inductance makes this necessary
# rather than optional; tune on the bench.
add(ref="R3", symbol="R", value="2R2 1W",
    footprint="R_1210_3225Metric",
    nets={"1": "SW", "2": "SNUB"},
    mpn="ESR25JZPJ2R2", mfr="Rohm", dk="RHM2.2AXCT-ND",
    descr="2.2 ohm 5% 1W 1210, switch node snubber")
add(ref="C12", symbol="C", value="2n2/100V",
    footprint="C_1210_3225Metric",
    nets={"1": "SNUB", "2": "PGND"},
    mpn="C1210C222J1GACTU", mfr="KEMET",
    dk="399-C1210C222J1GACTUCT-ND",
    descr="Ceramic 2.2nF 100V C0G 1210, snubber")

# Output bank: all ceramic, one part number, replacing 6x 22 uF ceramic plus
# 3x 330 uF polymer. Two constraints bind, and eight is what satisfies both:
# 6.5 A rms of ripple current at the 0.9 V corner shared across the bank
# (0.81 A each against a ~2 A 1210 limit), and enough capacitance after DC
# bias derating to hold ripple near 1%. A 25 V X5R at 12 V keeps only ~40% of
# its rating, so 8x 47 uF is ~150 uF, not 376 uF - the loop compensation in
# design.py is computed from the derated figure.
for i in range(13, 21):
    add(ref="C%d" % i, symbol="C", value="47u/25V X5R",
        footprint="C_1210_3225Metric",
        nets={"1": "VOUT", "2": "PGND"},
        mpn="TMK325ABJ476MM-P", mfr="Taiyo Yuden", dk="587-5479-1-ND",
        descr="Ceramic 47uF 25V X5R 1210")

add(ref="J2", symbol="Conn_Power_2Pad", value="VOUT 12V 1.7A",
    footprint="SolderPad_Pair_Output_8x6mm",
    nets={"1": "VOUT", "2": "PGND"},
    descr="12 V output terminals")

# ==========================================================================
# Gate driver
# ==========================================================================
# TPS40210's own driver is only 400 mA. Driving 260 nC of gate charge with it
# would cost ~2.2 W in switching loss, so U2 is mandatory, not a refinement.

add(ref="U2", symbol="UCC27511A", value="UCC27511ADBVR",
    footprint="SOT-23-6",
    nets={"1": "V12", "2": "OUTH", "3": "OUTL", "4": "PGND",
          "5": "PGND", "6": "GDRV"},
    mpn="UCC27511ADBVR", mfr="Texas Instruments", dk="296-49474-1-ND",
    descr="4A/8A single low-side gate driver, split outputs, SOT-23-6")

# Split gate resistors: slower turn-on to tame the switch-node edge, fast
# turn-off to minimise the loss that dominates at high duty.
add(ref="R4", symbol="R", value="2R2", footprint="R_0805_2012Metric",
    nets={"1": "OUTH", "2": "GATE"}, mpn="RC0805FR-072R2L", mfr="Yageo", dk="311-2.20CRCT-ND",
    descr="Turn-on gate resistor")
add(ref="R5", symbol="R", value="1R0", footprint="R_0805_2012Metric",
    nets={"1": "OUTL", "2": "GATE"}, mpn="RC0805FR-071RL", mfr="Yageo", dk="311-1.00CRCT-ND",
    descr="Turn-off gate resistor")
add(ref="R6", symbol="R", value="10k", footprint="R_0805_2012Metric",
    nets={"1": "GATE", "2": "PGND"},
    mpn="RC0805FR-0710KL", mfr="Yageo", dk="311-10.0KCRCT-ND",
    descr="Gate pulldown, holds FETs off when the driver is unpowered")
add(ref="C22", symbol="C", value="1u/25V", footprint="C_0805_2012Metric",
    nets={"1": "V12", "2": "PGND"}, mpn="C0805C105K3RACTU", mfr="KEMET", dk="399-C0805C105K3RACTUCT-ND",
    descr="Driver bulk decoupling")
add(ref="C23", symbol="C", value="100n/25V", footprint="C_0603_1608Metric",
    nets={"1": "V12", "2": "PGND"}, mpn="C0603C104K5RACTU", mfr="KEMET", dk="399-C0603C104K5RACTUCT-ND",
    descr="Driver HF decoupling")

# ==========================================================================
# Controller
# ==========================================================================

add(ref="U1", symbol="TPS40210DGQ", value="TPS40210DGQ",
    footprint="MSOP-10_3x3mm_P0.5mm",
    nets={"1": "RC", "2": "SS", "3": "EN", "4": "COMP", "5": "FB",
          "6": "AGND", "7": "ISNS", "8": "GDRV", "9": "BP", "10": "V12"},
    mpn="TPS40210DGQR", mfr="Texas Instruments", dk="296-26969-1-ND",
    descr="Wide-input non-synchronous boost controller, MSOP-10")

# Oscillator: R*C*f ~= 18.0, calibrated to the datasheet EC point
# (182k / 330pF -> 300 kHz). 549k with 330 pF gives ~99 kHz.
add(ref="R7", symbol="R", value="549k 1%", footprint="R_0603_1608Metric",
    nets={"1": "V12", "2": "RC"}, mpn="RC0603FR-07549KL", mfr="Yageo", dk="311-549KHRCT-ND",
    descr="Oscillator timing resistor, 100 kHz")
add(ref="C24", symbol="C", value="330p C0G", footprint="C_0603_1608Metric",
    nets={"1": "RC", "2": "AGND"}, mpn="CL10C331JB8NNNC", mfr="Samsung", dk="1276-1073-1-ND",
    descr="Oscillator timing capacitor")

add(ref="C25", symbol="C", value="220n", footprint="C_0603_1608Metric",
    nets={"1": "SS", "2": "AGND"},
    mpn="GRM188R71H224KAC4D", mfr="Murata", dk="490-12546-1-ND",
    descr="Soft start, ~18 ms - long on purpose to limit cell inrush")
add(ref="C26", symbol="C", value="1u/16V", footprint="C_0603_1608Metric",
    nets={"1": "BP", "2": "AGND"}, mpn="C0603C105K4RACTU", mfr="KEMET", dk="399-C0603C105K4RACTUCT-ND",
    descr="BP regulator bypass")

# Feedback divider: 0.700 V reference -> 12.009 V
add(ref="R8", symbol="R", value="100k 1%", footprint="R_0603_1608Metric",
    nets={"1": "VOUT", "2": "FB"}, mpn="RC0603FR-07100KL", mfr="Yageo", dk="311-100KHRCT-ND",
    descr="Feedback divider, upper")
add(ref="R9", symbol="R", value="6k19 1%", footprint="R_0603_1608Metric",
    nets={"1": "FB", "2": "AGND"}, mpn="ERJ-3EKF6191V", mfr="Panasonic", dk="P6.19KHCT-ND",
    descr="Feedback divider, lower")

# Type II compensation. Crossover ~600 Hz, forced low by the RHP zero, which
# falls to 4.3 kHz at the 0.9 V corner.
# Recomputed for the all-ceramic output bank. Dropping the polymers took
# C_out from ~1050 uF to ~150 uF, which raises the plant gain at every
# frequency, so R10 comes down from 33k2 to hold the same 600 Hz crossover.
# These are the E96/E12 neighbours of design.py's 4.8 kohm / 223 nF / 3.3 nF.
add(ref="R10", symbol="R", value="4k75 1%", footprint="R_0603_1608Metric",
    nets={"1": "COMP", "2": "CZ"}, mpn="RC0603FR-074K75L", mfr="Yageo", dk="311-4.75KHRCT-ND",
    descr="Compensation resistor")
add(ref="C27", symbol="C", value="220n", footprint="C_0603_1608Metric",
    nets={"1": "CZ", "2": "FB"}, mpn="GRM188R71H224KAC4D", mfr="Murata", dk="490-12546-1-ND",
    descr="Compensation zero, ~152 Hz")
add(ref="C28", symbol="C", value="3n3", footprint="C_0603_1608Metric",
    nets={"1": "COMP", "2": "FB"}, mpn="GCM1885C1H332FA16D", mfr="Murata", dk="490-GCM1885C1H332FA16DCT-ND",
    descr="HF pole, ~10 kHz")

# ISNS filter. 100 ohm keeps the 3 uA bias current from adding a meaningful
# offset to a signal that is only ~30 mV at full load.
add(ref="R11", symbol="R", value="100R", footprint="R_0603_1608Metric",
    nets={"1": "ISNS_K", "2": "ISNS"}, mpn="RC0603FR-07100RL", mfr="Yageo", dk="311-100HRCT-ND",
    descr="Current sense filter resistor")
add(ref="C29", symbol="C", value="1n", footprint="C_0603_1608Metric",
    nets={"1": "ISNS", "2": "AGND"}, mpn="CL10C102JB8NNNC", mfr="Samsung", dk="1276-1091-1-ND",
    descr="Current sense filter capacitor")

# ==========================================================================
# Bias input
# ==========================================================================

add(ref="J3", symbol="Conn_01x02", value="12V BIAS",
    footprint="PinHeader_1x02_P2.54mm",
    nets={"1": "V12AUX", "2": "PGND"},
    mpn="PREC002SAAN-RC", mfr="Sullins", dk="35-PREC002SAAN-RC-ND",
    descr="Auxiliary 12 V bias input, ~60 mA")
add(ref="D3", symbol="D_Schottky", value="SS14", footprint="D_SMA",
    nets={"1": "V12", "2": "V12AUX"},
    mpn="SS14", mfr="onsemi", dk="SS14CT-ND",
    descr="Schottky 40V 1A, reverse polarity protection on the bias rail")
add(ref="C30", symbol="C", value="10u/25V", footprint="C_1210_3225Metric",
    nets={"1": "V12", "2": "PGND"}, mpn="C3225X7R1E106M250AC", mfr="TDK", dk="445-1434-1-ND",
    descr="Bias bulk")
add(ref="C31", symbol="C", value="100n/25V", footprint="C_0603_1608Metric",
    nets={"1": "V12", "2": "AGND"}, mpn="C0603C104K5RACTU", mfr="KEMET", dk="399-C0603C104K5RACTUCT-ND",
    descr="Controller VDD decoupling")

# ==========================================================================
# Cell undervoltage lockout
# ==========================================================================
# Without this, a dying cell makes the converter run into current limit,
# hiccup, recover and repeat. TLV3011 tops out at 5.5 V so it gets its own
# zener-derived rail rather than running from the 12 V bias.

add(ref="R12", symbol="R", value="10k", footprint="R_0603_1608Metric",
    nets={"1": "V12", "2": "V5C"}, mpn="RC0603FR-0710KL", mfr="Yageo", dk="311-10.0KHRCT-ND",
    descr="Zener dropper, ~690 uA")
add(ref="D4", symbol="D_Zener", value="5V1 0.5W", footprint="D_SOD-123",
    nets={"1": "V5C", "2": "AGND"},
    mpn="MMSZ5231B-7-F", mfr="Diodes Incorporated",
    dk="MMSZ5231B-FDICT-ND",
    descr="5.1 V 0.5 W zener, comparator supply")
add(ref="C32", symbol="C", value="100n", footprint="C_0603_1608Metric",
    nets={"1": "V5C", "2": "AGND"}, mpn="C0603C104K5RACTU", mfr="KEMET", dk="399-C0603C104K5RACTUCT-ND",
    descr="Comparator supply bypass")

add(ref="U3", symbol="TLV3011", value="TLV3011AIDBVR",
    footprint="SOT-23-6",
    nets={"1": "EN", "2": "AGND", "3": "CMP_TH", "4": "CELL_S",
          "5": "CMP_REF", "6": "V5C"},
    mpn="TLV3011AIDBVR", mfr="Texas Instruments", dk="296-39259-1-ND",
    descr="Open-drain comparator with 1.242 V reference, SOT-23-6")

# 1.045 V rising / 0.837 V falling, 207 mV hysteresis. R15 sets both thresholds,
# so it cannot be changed independently of R14 - run design.py uvlo_thresholds()
# rather than adjusting these by inspection.
add(ref="R13", symbol="R", value="10k 1%", footprint="R_0603_1608Metric",
    nets={"1": "CMP_REF", "2": "CMP_TH"}, mpn="RC0603FR-0710KL", mfr="Yageo", dk="311-10.0KHRCT-ND",
    descr="UVLO threshold, upper")
add(ref="R14", symbol="R", value="26k1 1%", footprint="R_0603_1608Metric",
    nets={"1": "CMP_TH", "2": "AGND"}, mpn="RC0603FR-0726K1L", mfr="Yageo", dk="311-26.1KHRCT-ND",
    descr="UVLO threshold, lower")
add(ref="R15", symbol="R", value="100k", footprint="R_0603_1608Metric",
    nets={"1": "EN", "2": "CMP_TH"}, mpn="RC0603FR-07100KL", mfr="Yageo", dk="311-100KHRCT-ND",
    descr="UVLO hysteresis")
add(ref="R16", symbol="R", value="100k", footprint="R_0603_1608Metric",
    nets={"1": "V5C", "2": "EN"}, mpn="RC0603FR-07100KL", mfr="Yageo", dk="311-100KHRCT-ND",
    descr="Open-drain pullup for DIS/EN")
add(ref="R17", symbol="R", value="1k", footprint="R_0603_1608Metric",
    nets={"1": "VCELL", "2": "CELL_S"}, mpn="RC0603FR-071KL", mfr="Yageo", dk="311-1.00KHRCT-ND",
    descr="Cell sense filter")
add(ref="C33", symbol="C", value="100n", footprint="C_0603_1608Metric",
    nets={"1": "CELL_S", "2": "AGND"},
    mpn="C0603C104K5RACTU", mfr="KEMET", dk="399-C0603C104K5RACTUCT-ND",
    descr="Cell sense filter, 100 us - rides out cell noise")
add(ref="C34", symbol="C", value="10n", footprint="C_0603_1608Metric",
    nets={"1": "CMP_REF", "2": "AGND"}, mpn="CL10B103KB8NNNC", mfr="Samsung", dk="1276-1009-1-ND",
    descr="Reference bypass")

# Fit JP1 to bypass the cell UVLO entirely and force the controller enabled.
add(ref="JP1", symbol="Jumper_2", value="EN BYPASS",
    footprint="R_0805_2012Metric",
    nets={"1": "EN", "2": "AGND"}, dnp=True,
    mpn="RC0805JR-070RL", mfr="Yageo", dk="311-0.0ARCT-ND",
    descr="Solder jumper: fit to force enable, ignoring cell UVLO")

# ==========================================================================
# Test points and mechanical
# ==========================================================================

TESTPOINTS = [("TP1", "SW"), ("TP2", "ISNS"), ("TP3", "COMP"), ("TP4", "FB"),
              ("TP5", "GATE"), ("TP6", "VCELL"), ("TP7", "VOUT"),
              ("TP8", "PGND"), ("TP9", "EN"), ("TP10", "BP"),
              ("TP11", "AGND"), ("TP12", "V12")]
for ref, net in TESTPOINTS:
    add(ref=ref, symbol="TestPoint", value="TP_" + net,
        footprint="TestPoint_Pad_D1.5mm", nets={"1": net},
        in_bom=False, descr="Test point: " + net)

for ref in ("H1", "H2", "H3", "H4"):
    add(ref=ref, symbol="MountingHole", value="M3",
        footprint="MountingHole_3.2mm_M3", nets={}, in_bom=False,
        descr="M3 mounting hole")
# Power flags. These carry no parts; they only tell ERC that these rails are
# externally driven rather than left floating.
for i, net in enumerate(("VCELL", "PGND", "AGND", "VOUT", "V12AUX", "V12",
                         "V5C"), start=1):
    add(ref="#FLG%d" % i, symbol="PWR_FLAG", value="PWR_FLAG",
        footprint="", nets={"1": net}, in_bom=False,
        descr="ERC power flag on " + net)

for ref in ("FID1", "FID2"):
    add(ref=ref, symbol="Fiducial", value="FID",
        footprint="Fiducial_1mm", nets={}, in_bom=False,
        descr="Fiducial")


# ==========================================================================
# Derived views
# ==========================================================================

POWER_NETS = {"VCELL": "VCELL", "VOUT": "VOUT", "V12": "V12", "V5C": "V5C",
              "PGND": "PGND", "AGND": "AGND"}


def all_nets():
    nets = {}
    for c in C:
        for pin, net in c.nets.items():
            nets.setdefault(net, []).append((c.ref, pin))
    return nets


def check():
    """Structural checks that must hold before anything is generated."""
    problems = []
    refs = [c.ref for c in C]
    dupes = {r for r in refs if refs.count(r) > 1}
    if dupes:
        problems.append("duplicate refs: %s" % sorted(dupes))

    nets = all_nets()
    for net, conns in sorted(nets.items()):
        if len(conns) < 2:
            problems.append("net %r has only %d connection(s): %s"
                            % (net, len(conns), conns))

    # The AGND/PGND star point must be the shunt and nothing else.
    agnd = {ref for ref, _ in nets.get("AGND", [])}
    pgnd = {ref for ref, _ in nets.get("PGND", [])}
    if "R2" not in (agnd & pgnd):
        problems.append("R2 must bridge AGND and PGND (Kelvin star point)")

    # Every symbol and footprint must exist in the local libraries.
    import symbols, footprints
    symbols.symbol_defs()
    for c in C:
        if c.symbol not in symbols.PIN_MAP and c.symbol not in symbols.SYMBOL_NAMES:
            problems.append("%s: unknown symbol %r" % (c.ref, c.symbol))
        if c.footprint and c.footprint not in footprints.FOOTPRINT_NAMES:
            problems.append("%s: unknown footprint %r" % (c.ref, c.footprint))
        # Every net-bearing pin must exist on the symbol.
        pins = symbols.PIN_MAP.get(c.symbol, {})
        for pin in c.nets:
            if pin not in pins:
                problems.append("%s: pin %r not on symbol %s"
                                % (c.ref, pin, c.symbol))
    return problems


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    probs = check()
    nets = all_nets()
    print("components : %d" % len(C))
    print("in BOM     : %d" % sum(1 for c in C if c.in_bom))
    print("nets       : %d" % len(nets))
    print()
    for net, conns in sorted(nets.items()):
        print("  %-9s %2d  %s" % (net, len(conns),
                                  " ".join("%s.%s" % cp for cp in conns)))
    print()
    if probs:
        print("PROBLEMS:")
        for p in probs:
            print("  - " + p)
    else:
        print("All structural checks passed.")
