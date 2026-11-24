"""The passive series ladders: capacitors, inductors and ferrite beads.

Each series is one maker's datasheet expanded to one row per part number. These
tests check the things that would quietly go wrong in that expansion -- a field
missing from a row, a page citation outside the PDF, a part number whose own
code disagrees with the value filed beside it -- and that a search for an
ordinary value finds a part. They are not a check on whether a value is
plausible: only the datasheet decides that.
"""
import csv
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import cloud_parts as E  # noqa: E402

PP = E.PP
SERIES = {
    "yageo-cc-np0": "capacitor", "yageo-cc-x7r": "capacitor", "yageo-cc-x5r": "capacitor",
    "kyocera-taj": "capacitor", "panasonic-fk": "capacitor", "panasonic-svpf": "capacitor",
    "sunlord-swpa": "inductor", "cjiang-fnr": "inductor",
    "sunlord-sdcl": "inductor", "sunlord-sdfl": "inductor",
    "sunlord-gz": "ferrite_bead",
}


def selection():
    with open(os.path.join(REPO, "selection.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def table(kind):
    path = os.path.join(REPO, "table", kind + ".jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_every_series_is_present():
    got = {r["series_id"] for r in selection()}
    missing = sorted(set(SERIES) - got)
    assert not missing, missing


def test_every_row_carries_every_field_of_its_kind():
    sel = {r["mpn"]: r for r in selection()}
    for kind in sorted(set(SERIES.values())):
        want = set(PP.kinds()[kind]["fields"]) | {"mount", "temp", "channels", "automotive"}
        for row in table(kind):
            if sel[row["mpn"]]["series_id"] not in SERIES:
                continue
            got = set(row["fields"])
            assert got == want, (row["mpn"], sorted(want - got), sorted(got - want))
            for name, cell in row["fields"].items():
                assert "value" in cell, (row["mpn"], name)
                if cell["value"] is None:
                    assert cell.get("null_reason"), (row["mpn"], name)
                else:
                    assert cell.get("page"), (row["mpn"], name)


def test_page_citations_are_inside_the_datasheet():
    pages = {r["mpn"]: int(r["pages"]) for r in selection() if r["series_id"] in SERIES}
    kinds = {r["mpn"]: r["kind"] for r in selection() if r["series_id"] in SERIES}
    seen = 0
    for kind in sorted(set(kinds.values())):
        for row in table(kind):
            if row["mpn"] not in pages:
                continue
            seen += 1
            for name, cell in row["fields"].items():
                p = cell.get("page")
                if p is not None:
                    assert 1 <= p <= pages[row["mpn"]], (row["mpn"], name, p)
    assert seen == len(pages)


def _cap_from_code(code):
    """The Yageo CC capacitance code as farads, by the datasheet's own rule."""
    if "R" in code:
        return float(code.replace("R", ".").rstrip(".") if not code.startswith("R")
                     else "0." + code[1:]) * 1e-12
    return int(code[:2]) * (10 ** int(code[2])) * 1e-12


def test_mlcc_part_numbers_agree_with_their_values():
    sel = {r["mpn"]: r for r in selection()}
    pat = re.compile(r"^CC(\d{4})([A-Z])R(NPO|X7R|X5R)([0-9A-Z])(?:BB|BN)(.{2,3})$")
    n = 0
    for row in table("capacitor"):
        s = sel[row["mpn"]]
        if not s["series_id"].startswith("yageo-cc"):
            continue
        m = pat.match(row["mpn"])
        assert m, row["mpn"]
        assert m.group(1) == s["size"], row["mpn"]
        assert abs(_cap_from_code(m.group(5)) - row["fields"]["capacitance"]["value"]) < 1e-18 \
            or abs(_cap_from_code(m.group(5)) / row["fields"]["capacitance"]["value"] - 1) < 1e-9, \
            (row["mpn"], row["fields"]["capacitance"]["value"])
        n += 1
    assert n > 1500, n


def test_inductor_and_bead_part_numbers_carry_their_size():
    sel = {r["mpn"]: r for r in selection()}
    sizes = {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}
    n = 0
    for kind in ("inductor", "ferrite_bead"):
        for row in table(kind):
            s = sel[row["mpn"]]
            sid = s["series_id"]
            if sid in ("sunlord-swpa", "cjiang-fnr"):
                assert s["size"] in row["mpn"], row["mpn"]
            elif sid in ("sunlord-sdcl", "sunlord-sdfl", "sunlord-gz"):
                assert sizes[s["size"]] in row["mpn"], row["mpn"]
            else:
                continue
            n += 1
    assert n > 1300, n


def test_power_inductors_have_saturation_and_rated_current():
    sel = {r["mpn"]: r for r in selection()}
    n = 0
    for row in table("inductor"):
        if sel[row["mpn"]]["series_id"] not in ("sunlord-swpa", "cjiang-fnr"):
            continue
        for f in ("isat", "current", "dcr", "inductance"):
            assert row["fields"][f]["value"] is not None, (row["mpn"], f)
        n += 1
    assert n > 1000, n


def find(*args):
    r = subprocess.run([sys.executable, os.path.join(REPO, "bin", "cloud-parts"), "find"]
                       + list(args), capture_output=True, text=True)
    assert r.returncode in (0, 1), (args, r.stderr[-400:])
    m = re.search(r"^(\d+) part\(s\)", r.stdout, re.M)
    return int(m.group(1)) if m else 0


def test_ordinary_values_find_a_part():
    assert find("capacitor", "capacitance = 100n and package = '0402' and voltage >= 16") > 0
    assert find("capacitor", "capacitance = 10u and dielectric = 'X5R'") > 0
    assert find("capacitor", "dielectric = 'tantalum' and capacitance = 10u and voltage >= 16") > 0
    assert find("capacitor", "dielectric = 'aluminum' and capacitance >= 100u") > 0
    assert find("capacitor", "dielectric = 'polymer'") > 0
    assert find("inductor", "inductance = 4u7 and isat >= 1") > 0
    assert find("inductor", "inductance = 10u and shielded = true") > 0
    assert find("ferrite_bead", "impedance >= 600 and current >= 0.5") > 0
    assert find("resistor", "resistance = 10m and package = '2512'") > 0
