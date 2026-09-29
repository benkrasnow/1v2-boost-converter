# 1.2 V → 12 V / 20 W Boost Converter (KiCad 9)

Boost converter for an electromechanical cell: **0.9–1.6 V in (up to 28 A) →
12 V at 20 W**, biased from a separate low-current 12 V rail.

Open `1v2boost.kicad_pro` in KiCad 9. The project is **fully self-contained** —
every symbol and footprint lives in `lib/`, referenced through `${KIPRJMOD}`,
so no stock or external library is needed.

| | |
|---|---|
| Controller | TPS40210DGQ (not LM5155 — see below) |
| Gate driver | UCC27511A, 4 A/8 A — required, not optional |
| Topology | single-stage non-synchronous boost, 100 kHz |
| Efficiency | 89.2 % at 1.2 V, 84.0 % at the 0.9 V corner |
| Board | 2-layer, 2 oz copper, 150 × 100 mm |
| BOM | 41 line items, 60 fitted parts + 1 DNP |

## The two findings that shaped the design

**LM5155 cannot do this.** Its maximum duty cycle is spec'd at **90 % minimum**
(93 % typ) at 100 kHz. This converter needs D = 0.910 at nominal input and
0.937 at the 0.9 V corner. TPS40210 has no hard duty limit at all — only a
200 ns worst-case minimum off-time, giving D_max = 0.98.

**LM5122 (the synchronous, more efficient option) cannot do it either.** Its
current-sense common-mode range is 3 V–65 V, and in a boost that common mode
*is* the 1.2 V input. Only a ground-referenced low-side shunt works down here.

## Verification status

Checked with KiCad 10.0.5 (`kicad-cli`) on 2026-07-24:

| Check | Result |
|---|---|
| `sch erc` | **0 violations** |
| `pcb drc --schematic-parity` | **0 parity issues** — board matches schematic |
| `pcb drc` errors | 36 unconnected pads (see below) |
| `pcb drc` warnings | 9 dangling track stubs |
| clearance / shorts / holes / silk | **clean** |

## Read before fabricating

- **21 connections are still unrouted**, all small-signal, around the
  controller and the UVLO comparator. The generator refuses to emit copper
  that would violate clearance, so it leaves these as ratsnest rather than
  shipping near-shorts. **This board is not fabricable until they are
  routed** — open it in KiCad and finish them.
- **Take R2's sense connections at the finger tips, not the solder pads.** The
  shunt is a two-terminal part; its Kelvin behaviour comes entirely from the
  shape of the land pattern, and the two are the same net, so nothing will stop
  you attaching in the wrong place. See §7 of the design notes.

Full reasoning, loss budget, control-loop design and bring-up procedure:
**[docs/design-notes.md](docs/design-notes.md)**

## Regenerating

Everything is generated from `tools/netlist.py`, which is the single source of
truth for the circuit — the schematic, board and BOM cannot disagree with it.

```sh
python tools/build.py          # everything below, then ERC + DRC
```

`build.py` finds `kicad-cli` automatically (override with `KICAD_CLI`). It
runs the generators in order, fills the zones with KiCad's own filler, then
runs `validate.py`, ERC and DRC. Individual steps:

```sh
python tools/symbols.py        # lib/1v2boost.kicad_sym
python tools/footprints.py     # lib/1v2boost.pretty/
python tools/gen_schematic.py  # .kicad_sch, .kicad_pro, lib tables
python tools/gen_pcb.py        # .kicad_pcb  (reads KiCad's netlist export)
python tools/gen_bom.py        # bom/1v2boost-bom.csv
python tools/validate.py       # netlist round-trip + layer checks
python tools/design.py         # design report and margin checks
```

### How schematic changes reach the PCB

KiCad has no headless equivalent of Eeschema's **Update PCB from Schematic**
(F8) — no `kicad-cli sch update-pcb`, no `BOARD_NETLIST_UPDATER` in pcbnew's
Python, and the KiCad 10 IPC bindings are not installed. So `gen_pcb.py`
instead builds the board *from* `kicad-cli sch export netlist`, which is
KiCad's own authoritative reading of the schematic — net names, component
fields and DNP/BOM flags all come from there. The proof that it worked is
`pcb drc --schematic-parity` reporting **0 parity issues**; pressing F8 in
Eeschema should report nothing to change.
