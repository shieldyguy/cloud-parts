"""Symbol and footprint agree: the mechanical checks of every part's KiCad pairing.

1. pins have pads: every pin number of the symbol, across all its units, is a pad on the footprint.
2. pads have pins: every numbered pad of the footprint is a symbol pin, except mechanical pads
   (MP, SH, unnumbered) and the pads pairing-exceptions.csv lists with the datasheet page that lets
   them float (an NC pin, a die pad the maker says may be left open).
3. footprint fits symbol: the footprint is the symbol's own default footprint or matches one of its
   footprint filters (ki_fp_filters), so a symbol drawn for one package (a SOT-223 pin order, a
   TSSOP pinout) is not paired with another; generic symbols with no default and no filter pass.
   A pairing that differs for a reason the datasheet gives is listed in pairing-exceptions.csv.
4. size code: a passive whose size column is an EIA code (0402, 0603, ...) sits on a footprint
   of that code.
An exception that no longer matches a finding is itself a failure (stale), so the list stays true.

Reads the files under kicad/ (symbols/<Lib>.kicad_sym, footprints/<Lib>.pretty/<Name>.kicad_mod).
    python3 lib/kicad_pins.py [KICAD_DIR]      every finding over selection.csv; exit 1 if any
Standard library only."""
import csv
import fnmatch
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCEPTIONS = os.path.join(ROOT, "pairing-exceptions.csv")
MECHANICAL = re.compile(r"^(MP|SH|)$")
EIA = {"01005", "0201", "0402", "0603", "0805", "1008", "1206", "1210", "1812", "2010", "2220", "2512"}
_SYM = re.compile(r'^\t\(symbol "((?:[^"\\]|\\.)*)"', re.M)
_PROP = re.compile(r'\(property\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"')


def _blocks(text):
    """{name: (extends or None, block)} of the top-level symbols in a library file."""
    out = {}
    starts = [(m.start(), m.group(1)) for m in _SYM.finditer(text)]
    for i, (s, name) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        blk = text[s:e]
        ext = re.match(r'\t\(symbol "(?:[^"\\]|\\.)*"\s*\(extends\s+"((?:[^"\\]|\\.)*)"\)', blk)
        out[name] = (ext.group(1) if ext else None, blk)
    return out


def expand(number):
    """A pin number as pad numbers: KiCad 10's stacked pin "[1,15,38-39]" is several pads."""
    m = re.fullmatch(r"\[(.*)\]", number.strip())
    if not m:
        return {number}
    out = set()
    for part in m.group(1).split(","):
        part = part.strip()
        r = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        out |= {str(i) for i in range(int(r.group(1)), int(r.group(2)) + 1)} if r else {part}
    return out


class Library:
    def __init__(self, kicad_dir):
        self.dir, self._libs = kicad_dir, {}

    def _lib(self, lib):
        if lib not in self._libs:
            p = os.path.join(self.dir, "symbols", lib + ".kicad_sym")
            text = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
            self._libs[lib] = _blocks(text)
        return self._libs[lib]

    def _chain(self, ref):
        """The symbol's block and its parents' blocks, child first; [] when not found."""
        lib, _, name = ref.partition(":")
        blocks, out = self._lib(lib), []
        while name in blocks and name not in [n for n, _b in out]:
            ext, blk = blocks[name]
            out.append((name, blk))
            if not ext:
                break
            name = ext
        return out

    def pins(self, ref):
        """Set of pin numbers of symbol Lib:Name (its parent's when it extends one), or None."""
        ch = self._chain(ref)
        if not ch:
            return None
        out = set()
        for n in re.findall(r'\(number\s+"((?:[^"\\]|\\.)*)"', ch[-1][1]):
            out |= expand(n)
        return out

    def props(self, ref):
        """{property: value} of the symbol, a child's own values over its parent's."""
        out = {}
        for _n, blk in reversed(self._chain(ref)):
            head = blk.split("\n\t\t(symbol ", 1)[0]
            out.update({k: v for k, v in _PROP.findall(head)})
        return out

    def _fp_path(self, ref):
        lib, _, name = ref.partition(":")
        return os.path.join(self.dir, "footprints", lib + ".pretty", name + ".kicad_mod")

    def pads(self, ref):
        """Set of pad numbers of footprint Lib:Name, or None."""
        p = self._fp_path(ref)
        if not os.path.exists(p):
            return None
        return {n for n in re.findall(r'\(pad\s+"((?:[^"\\]|\\.)*)"', open(p, encoding="utf-8").read()) if n}


def failures(rows, lib):
    """[(mpn, symbol, footprint, missing pad numbers)] for rows whose pins lack pads."""
    out = []
    for r in rows:
        pins, pads = lib.pins(r["kicad_symbol"]), lib.pads(r["footprint"])
        if pins is None or pads is None:
            out.append((r["mpn"], r["kicad_symbol"], r["footprint"], ["(symbol or footprint not found)"]))
            continue
        miss = sorted(pins - pads, key=lambda s: (len(s), s))
        if miss:
            out.append((r["mpn"], r["kicad_symbol"], r["footprint"], miss))
    return out


def _key(s):
    return (len(s), s)


def unused_pads(rows, lib):
    """{mpn: 'pad pad ...'} for footprint pads (not mechanical) that no symbol pin uses."""
    out = {}
    for r in rows:
        pins, pads = lib.pins(r["kicad_symbol"]), lib.pads(r["footprint"])
        if pins is None or pads is None:
            continue
        extra = sorted((p for p in pads - pins if not MECHANICAL.match(p)), key=_key)
        if extra:
            out[r["mpn"]] = " ".join(extra)
    return out


def fits_symbol(ref_fp, props):
    """True when footprint Lib:Name is the symbol's default or matches a footprint filter."""
    default = props.get("Footprint", "")
    filters = props.get("ki_fp_filters", "").split()
    if not default and not filters:
        return True
    if default == ref_fp:
        return True
    name = ref_fp.partition(":")[2]
    return any(fnmatch.fnmatchcase(ref_fp if ":" in f else name, f) for f in filters)


def package_mismatches(rows, lib):
    """{mpn: footprint} for rows whose footprint is neither the symbol's default nor in its filters."""
    return {r["mpn"]: r["footprint"] for r in rows if not fits_symbol(r["footprint"], lib.props(r["kicad_symbol"]))}


def size_mismatches(rows):
    """{mpn: 'size -> footprint'} for passives whose EIA size code is not their footprint's."""
    out = {}
    for r in rows:
        s = (r.get("size") or "").strip()
        if s in EIA and not re.search(r"(^|[_:])%s(_|$)" % s, r["footprint"]):
            out[r["mpn"]] = "%s on %s" % (s, r["footprint"])
    return out


def exceptions(path=EXCEPTIONS):
    """{(mpn, check): detail} from pairing-exceptions.csv (columns mpn, check, detail, reason)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {(r["mpn"], r["check"]): r["detail"] for r in csv.DictReader(f)}


def report(rows, lib, exc=None):
    """{check: (findings not excepted, stale exceptions)} for the four checks.
    findings: [(mpn, detail)]; stale: [(mpn, detail)] exceptions that match no finding."""
    exc = exceptions() if exc is None else exc
    found = {
        "pins have pads": {m: "pins without a pad: " + " ".join(miss) + " (%s on %s)" % (s, f)
                           for m, s, f, miss in failures(rows, lib)},
        "pads have pins": unused_pads(rows, lib),
        "footprint fits symbol": package_mismatches(rows, lib),
        "size code": size_mismatches(rows),
    }
    out = {}
    for check, got in found.items():
        allowed = {m: d for (m, c), d in exc.items() if c == check}
        bad = sorted((m, d) for m, d in got.items() if allowed.get(m) != d)
        stale = sorted((m, d) for m, d in allowed.items() if got.get(m) != d)
        out[check] = (bad, stale)
    return out


def main(argv):
    kd = argv[0] if argv else os.path.join(ROOT, "kicad")
    rows = list(csv.DictReader(open(os.path.join(ROOT, "selection.csv"), encoding="utf-8")))
    n = 0
    for check, (bad, stale) in report(rows, Library(kd)).items():
        for m, d in bad:
            print("%-16s %-24s %s" % (check, m, d))
        for m, d in stale:
            print("%-16s %-24s exception no longer matches: %s" % (check, m, d))
        print("%s: %d finding(s), %d stale exception(s) over %d parts" % (check, len(bad), len(stale), len(rows)))
        n += len(bad) + len(stale)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
