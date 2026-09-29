# 1.2 V → 12 V / 20 W Boost Converter — Design Notes

Rev A · KiCad 9 project · 2-layer, 2 oz copper, 150 × 100 mm

Source: an electromechanical cell, nominally 1.2 V, noisy and inconsistent,
with low internal resistance. A separate low-current 12 V rail biases the
controller, so there is no startup problem to solve.

Run `python tools/design.py` to reproduce every number below.

---

## 1. Specification

| | |
|---|---|
| Input | 0.9 V … 1.2 V nom … 1.6 V |
| Input current | 18.7 A at 1.2 V; **26.5 A at 0.9 V** |
| Output | 12.0 V regulated, 20 W (1.67 A) |
| Hold-up requirement | full 20 W maintained down to 0.9 V input |
| Switching frequency | 100 kHz |
| Efficiency | 91.8 % at 1.6 V, **89.2 % at 1.2 V**, 84.0 % at 0.9 V |

---

## 2. Why not the LM5155

The LM5155 was the starting point, and it does not work here. From its
datasheet (SNVSB75E) electrical characteristics:

| Parameter | Min | Typ | Max |
|---|---|---|---|
| `D_MAX2` maximum duty cycle limit (R_T = 220 kΩ, i.e. 100 kHz) | **90 %** | 93 % | 96 % |

A 1.2 V → 12 V boost needs D = 0.90 ideally, and **0.910** once the series
resistance and rectifier drop are included. At the 0.9 V corner it needs
**0.937**. That does not close against a 90 % guaranteed floor, and it leaves
nothing at all for the input sag the design is explicitly required to ride
through. TI also states plainly: *"Minimum boost supply voltage 1.5 V when
BIAS ≥ 3.5 V."*

### Why not the LM5122 either

The obvious way to claw back efficiency is a synchronous controller, and the
LM5122 is the natural candidate — roughly 3 points better. It is disqualified
outright by one line in its Recommended Operating Conditions:

> Current sense common mode range (CSP, CSN): **3 V to 65 V**

The LM5122 senses inductor current on the input side of the inductor, so the
common-mode voltage at CSP/CSN *is* the power-stage input voltage — 1.2 V here.
In a boost there is no node sitting at a steady ≥ 3 V to move the shunt to.
Any controller that senses current high-side is unusable at this input
voltage. Only a **ground-referenced low-side shunt** works, which is exactly
what the LM5155 offers and why it was a sensible first choice.

---

## 3. Why the TPS40210

It keeps the low-side ground-referenced sense that the low input voltage
demands, and removes the duty-cycle ceiling:

- **No hard maximum-duty specification.** The only constraint is
  `t_OFF(min)` = 170 ns typ / **200 ns max**, giving
  `D_max = 1 − 200 ns × 100 kHz = 0.98` against the 0.937 worst case. The duty
  problem disappears with room to spare.
- Ground-referenced ISNS — valid at a 1.2 V power-stage input.
- `VDD` 4.5–52 V, so it runs directly off the 12 V auxiliary rail. **The IC
  never touches the 1.2 V rail at all**; it only sees ISNS (ground
  referenced), FB (from the 12 V output) and its own bias. That is why the
  "minimum input voltage" of the controller is irrelevant to this design.
- Internal slope compensation, 150 mV overcurrent threshold with
  soft-start-based hiccup retry.
- MSOP-10, hand-solderable.

Verified pinout (SLUS772G): `RC=1, SS=2, DIS/EN=3, COMP=4, FB=5, GND=6,
ISNS=7, GDRV=8, BP=9, VDD=10`.

### Its one weakness, and the fix

`I_GDRV` is only **375–400 mA**. Driving 260 nC of gate charge with that would
put roughly **2.2 W** into switching loss — more than a tenth of the entire
output. **U2 (UCC27511A, 4 A source / 8 A sink, SOT-23-6)** sits between GDRV
and the gates and brings this to 0.48 W. It is a required part, not a
refinement.

Verified pinout (SLUSAW9F): `VDD=1, OUTH=2, OUTL=3, GND=4, IN−=5, IN+=6`.
The split OUTH/OUTL outputs let R4 slow the turn-on edge while R5 keeps
turn-off fast, which is the right trade at 91 % duty.

---

## 4. The current-sense resistor is set by stability, not by loss

This is the least obvious result in the design.

At 19 A the instinct is to make the shunt as small as possible. But
TPS40210's slope compensation is *fixed*, which places an **upper** bound on
R_ISNS (datasheet §7.3.8):

```
S_e        = (V_DD / 20) × f_SW                     … eq 17
m2         = A_CS × R_ISNS × (V_OUT + V_D − V_IN)/L … eq 18
R_ISNS(max)= 2 · S_e · L / (A_CS · (V_OUT + V_D − V_IN))  … eq 19
```

With V_DD = 12 V and f_SW = 100 kHz, `S_e = 60,000 V/s`. At the 0.9 V corner
and worst-case gain (A_CS = 7.2), eq 19 gives 2.17 mΩ, and TI recommends using
**≤ 80 %** of it → **1.73 mΩ**.

**R2 = 1 mΩ** therefore satisfies stability with margin. It is not the value
this section was originally written around, and it is worth being clear that
availability, not analysis, picked it. The 1.5 mΩ 4-terminal Kelvin part the
design started with does not exist: the whole Bourns CSS2H-2512 family is
2-terminal and has no 1.5 mΩ value. Of the values that are actually stocked,
1.8, 2 and 3 mΩ all sit above the 1.73 mΩ ceiling derived above, so 1 mΩ is
the only legal choice left.

The cost is that the compensating ramp now lands at roughly **1.4–1.5 ×** the
current down-slope rather than the 0.93–0.99 × of the 1.5 mΩ part. The
datasheet's stated ideal is "preferably equal". Over-compensation is stable —
it is under-compensation that produces subharmonic oscillation — but it pushes
the loop toward voltage-mode-with-feedforward behaviour and away from true
current mode. Expect the measured transient response to be somewhat slower
than the small-signal model in §6 predicts.

### The consequence: overcurrent trip is a backstop only, and now a weak one

150 mV / 1 mΩ = **150 A peak**. That is short-circuit protection, not a
precision current limit, and it cannot be improved without breaking either
stability (larger shunt → subharmonic oscillation) or efficiency (a 5 mΩ shunt
sized for a 30 A trip would burn 1.8 W).

**This is materially worse than it was, and the reason deserves stating
plainly: the trip point is now above the inductor's saturation current.** The
original pairing was a 100 A trip against the SER2915L's soft powder core
rated Isat > 100 A — the comparator acted before the magnetics gave up. The
in-stock PG1083 saturates at 52 A, so in a hard fault the inductor saturates,
current rises almost without limit, and the shunt only reaches 150 mV well
after that. What is left protecting the board:

- The FETs are rated 150 A continuous / 600 A pulsed, so they survive the
  excursion the inductor allows.
- Hiccup retry via the SS pin limits the duration of any fault.
- The 18 ms soft start keeps startup inrush away from this region entirely.

That is thinner than the original design intended. If you want the trip back
below saturation you need a different inductor, not a different shunt — the
slope-compensation ceiling forbids the larger shunt that would do it.

---

## 5. Loss budget at 1.2 V / 18.7 A

| Item | W | % of input |
|---|---|---|
| Schottky D1 (0.40 V × 1.67 A) | 0.667 | 3.0 |
| Inductor DCR (1.4 mΩ worst case) | 0.489 | 2.2 |
| FET conduction (2 × paralleled, hot) | 0.331 | 1.5 |
| Shunt R2 (1 mΩ) | 0.317 | 1.4 |
| Capacitors + snubber | 0.190 | 0.8 |
| Input pads and copper (0.5 mΩ budget) | 0.175 | 0.8 |
| Inductor core | 0.150 | 0.7 |
| FET switching (with U2) | 0.078 | 0.3 |
| FET Coss | 0.031 | 0.1 |
| **Total** | **2.43** | |

P_in = 22.4 W → P_out = 20.0 W, **η = 89.2 %**.

This is 2.8 points better than the 86.4 % the design carried before, and the
gain came entirely from parts that had to be substituted for availability
reasons rather than from any deliberate optimisation:

- **FET switching loss fell 0.479 W → 0.078 W.** The obsolete IRLB3034PBF has
  Qgd ≈ 40 nC; the in-stock Vishay SUP40012EL-GE3 has **6.7 nC** at essentially
  the same 1.49 mΩ on-resistance. Nothing else in this table moved as far.
- **Shunt loss fell 0.508 W → 0.317 W** because the forced move to 1 mΩ also
  cuts I²R — the same change that weakened the overcurrent trip in §4.
- **Inductor DCR loss fell 0.614 W → 0.489 W**: the PG1083 is 1.4 mΩ max
  against the SER2915L's 1.65 mΩ, and physically smaller.

The Schottky is now comfortably the largest single loss, at more than the next
two combined. If this design is ever revisited for efficiency, that is where
the work is — a synchronous rectifier would take roughly 0.5 W of it, at the
cost of a controller that cannot be used at 1.2 V input (see §1).

At the 0.9 V corner losses rise to 5.26 W and efficiency falls to 79.2 %, but
full 20 W is still delivered — that corner costs 26.5 A of input current.

### Where the efficiency went, if you want it back

TO-220 through-hole packages were chosen for Q1/Q2/D1 deliberately: the land
pattern is unambiguous, they are hand-solderable, and they bolt to a heatsink,
which matters at 28 A. That costs roughly **1.5 points** versus a PQFN part.
Swapping Q1/Q2 for two **BSC010N04LS** (40 V, 1.0 mΩ, Qgd 19 nC) in SuperSO8
recovers most of it — mainly by cutting switching loss, since Qgd drops from
80 nC to 38 nC. That requires a new footprint and gives up the bolt-on
heatsink.

---

## 6. Operating point and control loop

| | 1.6 V | 1.2 V | 0.9 V |
|---|---|---|---|
| Duty | 0.876 | 0.910 | 0.937 (limit 0.98) |
| Input current | 13.6 A | 18.7 A | 26.5 A |
| Inductor ripple | 9.2 A | 7.0 A | 5.3 A |
| Peak inductor current | 18.5 A | 22.8 A | 30.7 A |
| C_OUT ripple current | 4.4 A | 5.3 A | 6.5 A rms |
| RHP zero | 13.6 kHz | 7.6 kHz | **4.3 kHz** |

The right-half-plane zero is what makes this loop slow. At 0.9 V it falls to
4.3 kHz, so crossover is set to **600 Hz** — roughly a seventh of it. Type II
compensation around the internal error amplifier:

- R10 = 4.75 kΩ, C27 = 220 nF (zero at ~150 Hz), C28 = 3.3 nF (pole at 10 kHz)
- Feedback: R8 = 100 kΩ, R9 = 6.19 kΩ → 0.700 V × (1 + 100/6.19) = **12.009 V**

These values are **not** the ones a polymer-bulk version of this board would
use. Dropping from ~1050 µF to 150 µF of effective output capacitance (§8)
raises the modulator gain and moves the load pole out to 294 Hz, so R10 comes
down by 7× to hold the same 600 Hz crossover. Recompute rather than carry
values over if the output bank changes again — `design.py compensation()`.

A slow loop means transient response is carried by output capacitance, and
with an all-ceramic bank there is much less of it. C_OUT is sized by ripple
current (6.5 A rms at the 0.9 V corner, 0.81 A per capacitor) rather than by
ripple voltage; expect worse load-step response than the polymer version and
check it on the bench before committing.

**Oscillator:** R7 = 549 kΩ with C24 = 330 pF. Calibrated against the
datasheet test point (182 kΩ / 330 pF → 300 kHz), i.e. `R·C·f ≈ 18.0`. Verify
on the bench and trim R7; the datasheet prefers 68–120 pF for C_T, which is
not reachable at 100 kHz without exceeding the 1 MΩ resistor limit.

---

## 7. Grounding — read before modifying the layout

**PGND and AGND are separate nets, and their only connection is at the low-side
terminal of the shunt R2.** Pin 2, the solder pad carrying the return current,
is PGND. Pin 4, the sense finger on that same terminal, is AGND.

R2 is a two-terminal part, so pins 2 and 4 are one node — the footprint says so
with `(net_tie_pad_groups "1, 3" "2, 4")`. **The Kelvin behaviour here is
geometric, not electrical.** Bourns' land pattern grows a 0.40 mm finger off
each solder pad that runs inward under the body and then out the side, past the
body edge. That tip carries no load current, so a trace leaving it does not
pick up the IR drop of the copper feeding the shunt. Attach AGND at the tip,
never at the solder pad — the two are the same net, and nothing but this
sentence and the geometry will stop you getting it wrong.

Adding any other AGND–PGND connection puts switch current into the sense
measurement and will show up as duty-cycle jitter and a shifted current limit.
The board reflects this: AGND is an isolated F.Cu island under the controller,
tied to R2 pad 4 by a single trace.

Both sense fingers point +Y, toward the controller, so each trace leaves its
tip heading away from the package. Pointing them the other way forces both
traces to double back along the side of the opposite terminal's solder pad,
which fails the 0.3 mm Power clearance — and a net tie does not rescue that,
because KiCad exempts the tied *pads* from each other, not passing tracks from
the pads.

U2's ground returns to PGND, not AGND — the driver sinks up to 8 A and must
not share the analog return.

---

## 8. Input network

The cell is described as noisy and inconsistent with low internal resistance,
which is the classic setup for input-filter interaction. A boost draws
continuous input current, so the ceramics mainly absorb the 5–9 A of inductor
ripple; the real risk is resonance between the input capacitance and the lead
inductance, driven by the converter's negative input resistance
(−V_in²/P_in ≈ −63 mΩ at nominal).

Both banks are **all ceramic** — the polymer parts and the 22 µF ceramics were
removed in favour of one part number per rail: fewer components, two fewer BOM
lines, no polymer cost.

| | Part | Nominal | After DC bias |
|---|---|---|---|
| C1–C6 (main input, at L1) | 100 µF/6.3 V X5R 1210 | 600 µF | **420 µF** (70 %) |
| C7–C9 (damping leg, with R1) | same | 300 µF | **210 µF** |
| C13–C20 (output) | 47 µF/25 V X5R 1210 | 376 µF | **150 µF** (40 %) |

**DC-bias derating is what drives the part count, and it is wildly asymmetric.**
A 25 V X5R at 12 V keeps only ~40 % of its rating; a 6.3 V part at 1.2 V is
barely stressed and keeps ~70 %. Both figures are **assumptions** — confirm
them against the manufacturers' bias curves for `TMK325ABJ476MM-P` (Taiyo
Yuden) and `CL32A107MQVNNWE` (Samsung) before ordering.

**C7–C9 + R1 (300 µF in series with 0.1 Ω) is a deliberate damping leg**, so
the input filter has a real part and cannot sustain oscillation against the
cell. It grew from one capacitor to three with the ceramic change: the 470 µF
polymer used to contribute its own ESR to the damping, and low-ESR ceramics do
not, so R1 needs comparable capacitance behind it or the filter Q rises.

Resulting ripple: **117 mV pp output** at 1.2 V (123 mV at 0.9 V, ~1 % of 12 V)
and 24 mV pp at the input. Output ripple current is 0.81 A rms per capacitor
against a ~2 A 1210 limit.

---

## 9. Cell undervoltage lockout

**Nothing inside the TPS40210 watches the cell.** It runs from the 12 V bias,
so its own UVLO monitors VDD, and the 150 mV overcurrent threshold across a
1 mΩ shunt trips at 150 A — a short-circuit backstop, not a brownout
detector. Without U3 the converter keeps trying at any input voltage.

That matters because a regulating converter is a **constant-power load**: as
the cell sags the input current rises to compensate, and the losses rise as
I², which drags the cell down further.

| V_cell | I_in for 20 W out | |
|---|---|---|
| 1.2 V | 18.7 A | nominal |
| 0.9 V | 26.5 A | design corner |
| 0.8 V | 31.1 A | |
| 0.7 V | 38.4 A | past L1 saturation (52 A peak) |

The failure is not a graceful brownout. The converter collapses the input,
hits current limit, hiccup-retries through soft start, recovers and repeats —
each cycle a full inrush event at 30 A+, which is harder on the FETs, the
inductor and the cell than simply stopping. Duty cycle is *not* the binding
constraint; the 98% ceiling is not reached until well below 0.5 V.

U3 (TLV3011, open-drain, 1.242 V integrated reference) compares the filtered
cell voltage against a divider off its own reference and pulls DIS/EN high
when the cell is too low. Thresholds are computed in `design.py`
(`uvlo_thresholds`), not asserted here:

| | |
|---|---|
| Rising (release) | **1.045 V** |
| Falling (trip) | **0.837 V** |
| Hysteresis | **207 mV** |
| DIS/EN level when disabled | 3.07 V |

The two thresholds are compared against different things. Falling is compared
against the **loaded** cell, and 0.837 V sits just under the 0.9 V full-power
corner, so the converter meets spec and then quits. Rising is compared against
the **unloaded** cell — at release there is no load yet — so what bounds it is
the open-circuit voltage of a healthy cell (~1.25 V assumed), not the 1.2 V
loaded nominal.

**Hysteresis is the essential part, not a nicety.** When the UVLO drops the
load the cell springs back by I·R_source — 29.1 A × ~4 mΩ ≈ **117 mV** at the
trip point. If the hysteresis were smaller than that, removing the load would
lift the cell past the rising threshold on its own, the converter would
restart into the same sag, and the UVLO would become a slow hiccup oscillator:
precisely the failure it exists to prevent. 207 mV gives 1.78× margin.
(The margin improved with the 1 mΩ shunt: lower shunt loss means less input
current at the trip point, so the cell springs back less far.) R17/C33 (100 µs)
separately keeps switching noise on the cell from tripping it.

The 4 mΩ source resistance is an **assumption** — the cell is characterised
only as "low internal resistance". Measure it during bring-up and re-run
`design.py` if it is materially higher; the margin shrinks as R_source grows.

TLV3011's absolute maximum supply is **7 V**, so it cannot run from the 12 V
bias. R12 + D4 (5.1 V zener) make a local ~690 µA rail for it.

Polarity: cell on IN−, reference on IN+, so cell-OK → output low → DIS/EN low
→ enabled. **Fit JP1 to bypass the UVLO entirely** and force the controller
enabled (default: do not fit).

---

## 10. Files

```
1v2boost.kicad_pro / .kicad_sch / .kicad_pcb
sym-lib-table, fp-lib-table      project-local, via ${KIPRJMOD}
lib/1v2boost.kicad_sym           26 symbols - everything, including R/C/L
lib/1v2boost.pretty/             24 footprints - everything
bom/1v2boost-bom.csv             41 line items, 60 fitted + 1 DNP
tools/design.py                  all calculations, run it
tools/netlist.py                 the circuit; single source of truth
tools/symbols.py, footprints.py  library generators
tools/gen_schematic.py, gen_pcb.py, gen_bom.py
tools/validate.py                structural + netlist + clearance checks
```

**Self-containment:** every symbol and footprint is authored into the
project-local libraries, including generic passives. The project depends on
nothing outside its own directory — no stock KiCad library is referenced, so
there is nothing to break when libraries move or change.

Regenerate everything:

```
python tools/symbols.py && python tools/footprints.py
python tools/gen_schematic.py && python tools/gen_pcb.py && python tools/gen_bom.py
python tools/validate.py
```

---

## 11. Sourcing — every part checked against Digi-Key

Checked on **2026-07-25**. Every fitted line in `bom/1v2boost-bom.csv` carries a
Digi-Key part number that was looked up on the site and found **Active with
non-zero direct stock**. Marketplace listings were rejected. The numbers live in
`tools/netlist.py` as the `dk` field, so the schematic symbol properties, the
BOM and the netlist all read from one place; there is no separate table to
drift.

This check was not a formality. Of the 14 part numbers the design previously
carried, **six did not exist at all**, one was Marketplace-only, and **four of
the four Digi-Key numbers spot-checked were wrong**. They had been written down
without ever being checked against a distributor. What changed:

| Ref | Was | Problem | Now |
|---|---|---|---|
| Q1,Q2 | IRLB3034PBF | Obsolete | SUP40012EL-GE3 (Vishay) |
| L1 | SER2915L-152KL | Marketplace only, 0 direct | PG1083.152NL (Pulse) |
| R2 | CSS2H-2512R-L150F | Does not exist; family is 2-terminal | CSS2H-2512R-1L00F |
| C13–C20 | GRM32ER61E476ME15L | Does not exist (25 V not made) | TMK325ABJ476MM-P |
| C1–C9 | GRM32ER60J107ME20L | Not For New Designs | CL32A107MQVNNWE |
| C12 | CGA5L2C0G2A222J | Does not exist | C1210C222J1GACTU |
| R1 | ERJ-P14F0R10U | Does not exist | RCWE1210R100FKEA |
| R3 | ERJ-P14F2R20U | Does not exist | ESR25JZPJ2R2 |
| D4 | MMSZ5231BT1G | 0 stock | MMSZ5231B-7-F |

The ~25 generic passives that previously carried no part number at all now
carry real ones too.

### L1's land pattern has been redrawn — done

`L_Pulse_PG1083` is now authored from the SUGGESTED LAND PATTERN on page 1 of
Pulse datasheet **P716.D (01/19)**: pads 1/2 4.00 × 5.00 mm on 14.50 mm
centres, pad 3 4.00 × 4.00 mm, 17.50 mm from the pad 1/2 row, body
21.70 × 21.50 × 10.60 mm. Those five numbers close on each other — 17.50 +
5.00/2 + 4.00/2 = 22.00 mm of land against a 21.50 mm body, so every pad
overhangs its edge by exactly 0.25 mm — which is the check that they were read
correctly.

Worth knowing if you ever re-derive them: **the drawing in P716 is not to
scale.** It is vector art whose horizontal and vertical scales differ by about
10%. Measure the drawn pads and you get 4.00 × 5.00 mm vertically but 3.5 mm
horizontally, and the body works out to 24.4 mm instead of 21.7. The callouts
are the specification; anything scaled off the artwork is wrong.

Two consequences beyond the pad sizes:

- **Pad 3 carries no net.** P716's schematic shows pin 3's lead terminating in
  free space — it is a mounting tab, not a winding terminal.
- **Note 10: the core is conductive.** Nothing under the body may be an
  exposed via, and terminal-to-terminal voltage must stay under 24 V (SW peaks
  near 12.6 V, so that half is satisfied by inspection). The via constraint is
  now enforced in code — `assert_no_vias_under_l1()` in `tools/gen_pcb.py`
  fails the build if either via generator drops one inside L1's courtyard.
  Both of those generators are clearance-driven and neither knows about the
  core, so this had to be checked rather than assumed.

L1 sits at (86.0, 18.5), moved up and left of where the larger Coilcraft part
was. Three constraints pin it there: the SW pad has to clear the VCELL zone's
x = 90 edge or the filler carves the pour back under the switch node; the body
has to stay above Q1/Q2's courtyard at y = 46.2; and the y = 4 stitching-via
row has to stay outside the courtyard, whose top edge is at y = 6.9.

### R2's land pattern has been redrawn too — done

`R_Shunt_2512_CSS2H` is now Bourns' own Recommended Pad Layout (datasheet
page 2): solder pads 1.80 × 3.40 mm with a 3.40 mm gap, plus a 0.40 mm sense
finger off each pad whose tip sits 3.20 mm off the centreline. Body
6.35 ± 0.15 × 3.05 ± 0.20 mm. Those close on each other — 7.00 mm of pad span
against a 6.35 mm body is 0.325 mm of toe, and the 3.40 mm gap against terminal
inner edges at ±2.035 is 0.335 mm of heel. Toe and heel agreeing to 10 µm is
the check that the callouts were read correctly.

The part really is two-terminal — "CSS2H" decodes as 2 terminals in the
datasheet's own ordering table — so the four pads are two terminals, declared
as net-tie groups. See §7 for why that is the right representation rather than
collapsing the nets.

Two choices worth knowing about:

- **The sense fingers are mask-covered except at the tip.** Two exposed 0.40 mm
  fingers, 0.60 mm apart, under the body, between two paste-loaded pads, is a
  solder bridge across the shunt. A shorted shunt is not a visible failure — it
  is a converter with no current limit at all.
- **The fingers point at the controller.** The reason is in §7; it is a
  clearance constraint, not cosmetics.

### Nothing is blocking fabrication on parts any more

Both substitutions that were flagged here now have land patterns drawn from the
manufacturer's own documents. What remains is routing (§13), not sourcing.

---

## 12. Verification status — please read

Verified with **KiCad 10.0.5** (`kicad-cli`) on 2026-07-24 — run
`python tools/build.py` to reproduce:

| Check | Result |
|---|---|
| `sch erc --severity-all` | **0 errors, 0 warnings** |
| `pcb drc --schematic-parity` | **0 parity issues** |
| clearance, shorts, hole-to-hole, solder-mask, silkscreen | **clean** |
| unconnected pads | **36 (errors)** — see below |
| dangling track stubs | 9 (warnings) |

`tools/validate.py` now covers only what DRC cannot: that the schematic
encodes the circuit `netlist.py` intends. It re-extracts the netlist from wire
and label geometry with an independent parser and matches netlist.py exactly
(161 pins, 27 nets), cross-checks that against KiCad's own netlist export, and
verifies every pad and graphic carries a layer assignment.

> **An earlier version of this file claimed the design was verified when it was
> not.** `validate.py` used to carry its own clearance and courtyard sweep and
> reported "All checks passed" on a board that KiCad's DRC then failed with
> **835 violations** — one regex in the footprint copier had stripped the layer
> assignment from every pad and graphic, and the geometric checker never looked
> at layers. Those overlapping checks have been deleted rather than repaired.
> KiCad is the authority.

### The remaining blocker

**36 pads are still unconnected, and three of those are power-stage
connections**: D1 pad 2 (cathode) to the VOUT pour, Q2 pad 3 (source) to the
SRC track, and Q1↔Q2 gate. The rest are small-signal (V12 distribution, the
comparator network, and short controller stubs). The generator's router
refuses to lay copper that would violate clearance and leaves the remainder as
ratsnest — deliberate, but it means **the board is not fabricable until these
are routed by hand in KiCad**.

### One more thing that needs your attention

1. **21 nets arrive partly as ratsnest.** The PCB generator refuses to emit
   copper that would violate clearance, so rather than shipping a board with
   sub-clearance gaps it leaves what it cannot route safely for you. All
   zones, the switch node, the FET sources, the input and output pours and the
   PGND via stitching are routed. What remains is small-signal work in the
   controller area — roughly 20 short connections. Run `python
   tools/validate.py` for the exact list, then route them interactively in
   KiCad; it is maybe half an hour.

---

## 13. Bring-up

1. **Bias only first.** Apply 12 V to J3 with no cell. Confirm BP ≈ 8 V at
   TP10, and that DIS/EN (TP9) sits high — the cell UVLO should be holding the
   converter off. No switching.
2. **Check the oscillator.** Ground TP9 or fit JP1 to force enable, and
   confirm ~100 kHz at TP5. Trim R7 if it is off; the R·C·f ≈ 18.0
   calibration is approximate.
3. **Low current first.** Feed the input from a bench supply at 1.2 V with a
   current limit of 5 A and a light load. Confirm 12 V at TP7 and a clean
   ramp on the soft start.
4. **Tune the snubber.** Watch TP1 (SW) with a short ground spring. TO-220
   lead inductance will ring; adjust R3/C12 to damp it without burning more
   than a few hundred mW in R3. Confirm the peak stays well under the 40 V
   FET and 45 V diode ratings.
5. **Check for subharmonic oscillation.** At full load, look for alternating
   wide and narrow pulses at TP5. The design targets S_e ≈ m2, so there should
   be none; if you see it, R2 is too large for the actual inductance.
6. **Then bring the cell in.** Verify the UVLO thresholds (1.045 V rising,
   0.837 V falling) by ramping a bench supply through them, and confirm the
   converter comes up cleanly rather than hiccuping. **Then measure the source
   resistance** — trip the UVLO at full load and scope how far the cell springs
   back when the load drops. That step must be comfortably under the 207 mV
   hysteresis; if it is not, the cell's internal resistance is higher than the
   4 mΩ assumed and R15 needs to come down (see `design.py`).
7. **Thermals.** At the 0.9 V / 28 A corner the board dissipates 5.3 W and the
   inductor runs near its 30 A / 40 °C-rise rating. That corner wants airflow
   or a heatsink; it is meant to be occasional, not continuous duty.
