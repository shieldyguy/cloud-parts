"""Symbol and footprint agree for every part (lib/kicad_pins.py): pins have pads, pads have pins,
the footprint fits the symbol, the size code fits the footprint; no part is left flagged, every
exception and every corrected pairing gives its datasheet page, and the checks catch each kind of
disagreement on a small made-up library."""
import csv
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import kicad_pins  # noqa: E402


def rows():
    with open(os.path.join(REPO, "selection.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def library():
    """The KiCad files this repository ships, under kicad/."""
    if os.path.isdir(os.path.join(REPO, "kicad", "symbols")):
        return kicad_pins.Library(os.path.join(REPO, "kicad"))
    pytest.skip("kicad/ is not here")


def test_every_part_passes_every_check():
    rep = kicad_pins.report(rows(), library())
    assert set(rep) == {"pins have pads", "pads have pins", "footprint fits symbol", "size code"}
    for check, (bad, stale) in rep.items():
        assert bad == [], (check, bad[:5])
        assert stale == [], (check, stale[:5])


def test_no_part_is_flagged():
    assert [r["mpn"] for r in rows() if (r.get("pairing_note") or "").lower().startswith("flagged")] == []


def test_pins_have_pads_has_no_exceptions():
    assert all(e[1] != "pins have pads" for e in kicad_pins.exceptions())


def test_every_corrected_pairing_and_exception_cites_its_source():
    """A pairing that is not the KiCad library's own default carries the datasheet
    page it was corrected from, in selection.csv's pairing_note; every exception
    carries its reason and names a part."""
    sel = {r["mpn"]: r for r in rows()}
    noted = [r for r in sel.values() if (r.get("pairing_note") or "").strip()]
    assert len(noted) >= 90
    for r in noted:
        assert re.search(r"\bp\.\d", r["pairing_note"]), r["mpn"]
    with open(kicad_pins.EXCEPTIONS, encoding="utf-8") as f:
        exc = list(csv.DictReader(f))
    assert exc
    for r in exc:
        assert r["reason"].strip() and r["mpn"] in sel, r["mpn"]
        assert r["check"] in ("pads have pins", "footprint fits symbol", "size code"), r
        if r["check"] == "pads have pins":      # a pad the datasheet itself lets float
            assert re.search(r"\bp\.\d", r["reason"]), r["mpn"]


def test_own_items_cite_their_datasheet_page():
    """The cloud_parts library (this repository's own symbols and footprints,
    drawn where KiCad has none) says on each item the datasheet page it is from."""
    sym = os.path.join(REPO, "kicad", "symbols", "cloud_parts.kicad_sym")
    if not os.path.isfile(sym):
        pytest.skip("kicad/ is not here")
    text = open(sym, encoding="utf-8").read()
    descs = re.findall(r'\(property "Description" "([^"]*)"', text)
    assert descs and all(re.search(r"p\.\d", d) for d in descs)
    fp = os.path.join(REPO, "kicad", "footprints", "cloud_parts.pretty")
    for f in sorted(os.listdir(fp)):
        t = open(os.path.join(fp, f), encoding="utf-8").read()
        d = re.findall(r'\(descr "((?:[^"\\]|\\.)*)"', t)
        assert d and re.search(r"p\.\d", d[0]), f


# ---------------------------------------------------------------- the checks on a made-up library
SYM = '''(kicad_symbol_lib
\t(version 20251024)
\t(symbol "Q3"
\t\t(property "Footprint" "T:SOT-23" (at 0 0 0))
\t\t(property "ki_fp_filters" "SOT?23*" (at 0 0 0))
\t\t(symbol "Q3_1_1"
\t\t\t(pin input line (at 0 0 0) (length 1) (name "G") (number "1"))
\t\t\t(pin passive line (at 0 0 0) (length 1) (name "S") (number "2"))
\t\t\t(pin passive line (at 0 0 0) (length 1) (name "D") (number "3"))
\t\t)
\t)
\t(symbol "R2"
\t\t(symbol "R2_1_1"
\t\t\t(pin passive line (at 0 0 0) (length 1) (name "~") (number "1"))
\t\t\t(pin passive line (at 0 0 0) (length 1) (name "~") (number "2"))
\t\t)
\t)
)
'''


def fp(n, pads):
    return '(footprint "%s"\n%s)\n' % (n, "".join('\t(pad "%s" smd rect (at 0 0) (size 1 1))\n' % p for p in pads))


@pytest.fixture
def made_up(tmp_path):
    os.makedirs(tmp_path / "symbols")
    (tmp_path / "symbols" / "T.kicad_sym").write_text(SYM)
    d = tmp_path / "footprints" / "T.pretty"
    os.makedirs(d)
    for name, pads in [("SOT-23", "123"), ("SOT-89-3", "1232"), ("SOIC-8", "12345678"), ("R_0603", "12"),
                       ("R_0805", "12"), ("Conn", ["1", "2", "MP", "MP"])]:
        (d / (name + ".kicad_mod")).write_text(fp(name, pads))
    return kicad_pins.Library(str(tmp_path))


def row(mpn, sym, foot, size=""):
    return {"mpn": mpn, "kicad_symbol": "T:" + sym, "footprint": "T:" + foot, "size": size}


def test_each_check_catches_its_case(made_up):
    rs = [row("ok", "Q3", "SOT-23"), row("res", "R2", "R_0603", "0603"), row("conn", "R2", "Conn"),
          row("fewpads", "Q3", "R_0805"), row("so8", "Q3", "SOIC-8"), row("sot89", "Q3", "SOT-89-3"),
          row("size", "R2", "R_0805", "0603")]
    rep = kicad_pins.report(rs, made_up, exc={})
    got = {c: [m for m, _d in bad] for c, (bad, _s) in rep.items()}
    assert got["pins have pads"] == ["fewpads"]
    assert got["pads have pins"] == ["so8"]                  # MP pads of "conn" are mechanical
    assert got["footprint fits symbol"] == ["fewpads", "so8", "sot89"]
    assert got["size code"] == ["size"]


def test_an_exception_must_match_and_must_still_be_needed(made_up):
    rs = [row("so8", "Q3", "SOIC-8")]
    ok = kicad_pins.report(rs, made_up, exc={("so8", "pads have pins"): "4 5 6 7 8",
                                             ("so8", "footprint fits symbol"): "T:SOIC-8"})
    assert all(bad == [] and stale == [] for bad, stale in ok.values())
    wrong = kicad_pins.report(rs, made_up, exc={("so8", "pads have pins"): "4 5"})
    assert wrong["pads have pins"][0] == [("so8", "4 5 6 7 8")]
    assert wrong["pads have pins"][1] == [("so8", "4 5")]
    stale = kicad_pins.report([row("ok", "Q3", "SOT-23")], made_up, exc={("ok", "size code"): "x"})
    assert stale["size code"] == ([], [("ok", "x")])
