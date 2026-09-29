"""Reads KiCad's own exported netlist, so the board is built from what KiCad
thinks the schematic says rather than from what this project thinks it says.

kicad-cli has no headless equivalent of Eeschema's "Update PCB from Schematic"
(F8) -- there is no `sch update-pcb`, pcbnew's Python module no longer exposes
BOARD_NETLIST_UPDATER, and the KiCad 10 IPC bindings (kipy) are not installed.
What KiCad *does* offer headlessly is its authoritative netlist exporter, so we
take that as the source of truth and generate the board from it. The result is
verifiable: `kicad-cli pcb drc --schematic-parity` is KiCad itself confirming
the board and schematic agree.

Two things here cannot be guessed and must come from KiCad:

  * net naming - local labels are sheet-qualified ("/DAMP") while power and
    global labels are not ("PGND"). Getting this wrong produces a board that
    looks correct and reports a net_conflict on every pad.
  * component metadata - field values and the dnp / exclude_from_bom attribute
    flags, which DRC compares between symbol and footprint.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# locating kicad-cli
# --------------------------------------------------------------------------

def find_cli():
    """Absolute path to kicad-cli, or None. KICAD_CLI overrides the search."""
    env = os.environ.get("KICAD_CLI")
    if env and os.path.isfile(env):
        return env

    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]
    found = []
    for r in roots:
        base = os.path.join(r, "KiCad")
        if not os.path.isdir(base):
            continue
        for ver in os.listdir(base):
            cand = os.path.join(base, ver, "bin", "kicad-cli.exe")
            if os.path.isfile(cand):
                found.append((ver, cand))
    if found:
        # highest version wins
        found.sort(key=lambda t: [int(p) if p.isdigit() else p
                                  for p in re.split(r"[.\-]", t[0])])
        return found[-1][1]

    for name in ("kicad-cli", "kicad-cli.exe"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                return cand
    return None


def find_python():
    """Absolute path to KiCad's bundled Python (has the pcbnew module)."""
    cli = find_cli()
    if not cli:
        return None
    cand = os.path.join(os.path.dirname(cli), "python.exe")
    return cand if os.path.isfile(cand) else None


def export(sch=None, out=None):
    """Run `kicad-cli sch export netlist`. Returns the netlist path."""
    cli = find_cli()
    if not cli:
        raise RuntimeError(
            "kicad-cli not found. Set KICAD_CLI to its full path.")
    sch = sch or os.path.join(ROOT, "1v2boost.kicad_sch")
    out = out or os.path.join(ROOT, "build", "1v2boost.net")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([cli, "sch", "export", "netlist",
                    "--format", "kicadsexpr", "-o", out, sch],
                   check=True, capture_output=True)
    return out


# --------------------------------------------------------------------------
# s-expression parsing
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+')


def _parse(text):
    stack = [[]]
    for tok in _TOKEN.findall(text):
        if tok == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            stack.pop()
        elif tok.startswith('"'):
            stack[-1].append(tok[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(tok)
    return stack[0][0]


def _kids(node, tag):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]


def _one(node, tag, default=None):
    got = _kids(node, tag)
    if not got:
        return default
    return got[0][1] if len(got[0]) > 1 else default


class Netlist:
    """comps: ref -> {value, footprint, description, fields{}}
       nets:  ordered list of (name, [(ref, pin), ...])"""

    def __init__(self, comps, nets):
        self.comps = comps
        self.nets = nets

    def net_of(self):
        """(ref, pin) -> kicad net name"""
        out = {}
        for name, nodes in self.nets:
            for rp in nodes:
                out[rp] = name
        return out

    def bare_to_kicad(self):
        """Our internal bare net name -> KiCad's name ("DAMP" -> "/DAMP").

        Matching on the trailing path element is safe here because the design
        is a single flat sheet, so names are unique once the leading "/" is
        dropped. Collisions are reported rather than silently resolved.
        """
        out = {}
        for name, _ in self.nets:
            bare = name.rsplit("/", 1)[-1]
            if bare in out and out[bare] != name:
                raise RuntimeError(
                    "ambiguous net name %r maps to both %r and %r"
                    % (bare, out[bare], name))
            out[bare] = name
        return out


def parse(path=None):
    path = path or os.path.join(ROOT, "build", "1v2boost.net")
    with open(path, encoding="utf-8") as fh:
        tree = _parse(fh.read())

    comps = {}
    for block in _kids(tree, "components"):
        for comp in _kids(block, "comp"):
            ref = _one(comp, "ref")
            fields = {}
            for fb in _kids(comp, "fields"):
                for f in _kids(fb, "field"):
                    # (field (name "MPN") "value")   value absent when empty
                    fname = f[1][1] if isinstance(f[1], list) else None
                    fval = f[2] if len(f) > 2 and isinstance(f[2], str) else ""
                    if fname:
                        fields[fname] = fval
            # KiCad links a footprint to its symbol by UUID path, not by
            # reference: "<sheet path><symbol uuid>". A footprint without one
            # is an orphan to "Update PCB from Schematic", which will offer to
            # delete and re-place it rather than update it.
            sheet = "/"
            for sp in _kids(comp, "sheetpath"):
                sheet = _one(sp, "tstamps", "/")
            uuid = _one(comp, "tstamps", "")

            comps[ref] = {
                "value": _one(comp, "value", ""),
                "footprint": _one(comp, "footprint", ""),
                "description": _one(comp, "description", ""),
                "fields": fields,
                "path": sheet.rstrip("/") + "/" + uuid if uuid else "",
                "sheetfile": next(
                    (_one(p, "value", "") for p in _kids(comp, "property")
                     if _one(p, "name") == "Sheetfile"), ""),
            }

    nets = []
    for block in _kids(tree, "nets"):
        for net in _kids(block, "net"):
            name = _one(net, "name")
            nodes = [(_one(nd, "ref"), _one(nd, "pin"))
                     for nd in _kids(net, "node")]
            nets.append((name, nodes))
    return Netlist(comps, nets)


def load(refresh=True):
    """Export from the schematic (unless refresh=False) and parse."""
    path = os.path.join(ROOT, "build", "1v2boost.net")
    if refresh or not os.path.isfile(path):
        path = export(out=path)
    return parse(path)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("kicad-cli: %s" % find_cli())
    nl = load()
    print("%d components, %d nets" % (len(nl.comps), len(nl.nets)))
    m = nl.bare_to_kicad()
    renamed = {k: v for k, v in m.items() if k != v}
    print("%d nets are sheet-qualified by KiCad:" % len(renamed))
    print("   ", ", ".join("%s->%s" % kv for kv in sorted(renamed.items())))
