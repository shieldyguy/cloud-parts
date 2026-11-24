"""bin/cloud-parts find over the real table: its verb and flags,
its help, a value read as
null kept as may fit with the table's reason, and --why giving the page."""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import cloud_parts as E  # noqa: E402


def run(*args):
    r = subprocess.run([os.path.join(REPO, "bin", "cloud-parts"), "find"] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def test_help_is_the_modules_help():
    assert run("--help")[1] == E.PP.help()
    assert run("--help", "mosfet")[1] == E.PP.help("mosfet", E.parts())


def test_every_table_row_is_a_part_with_its_kind():
    rows = sum(1 for f in os.listdir(E.TABLE) if f.endswith(".jsonl")
               for line in open(os.path.join(E.TABLE, f), encoding="utf-8") if line.strip())
    ps = E.parts()
    assert len(ps) == rows
    assert all(d["params"]["kind"] in E.PP.kinds() for _f, d in ps)
    assert all(v is not None for _f, d in ps for v in d["params"].values())


def test_a_null_value_is_may_fit_with_its_reason():
    nulls = [(d, f) for _f, d in E.parts() if d["params"]["kind"] == "mosfet"
             for f, c in d["_table"]["fields"].items() if c.get("value") is None and f == "qg"]
    if not nulls:
        return
    d, _f = nulls[0]
    code, out, _e = run("mosfet", "qg < 1u and mpn = '%s'" % d["mpn"], "--json")
    got = json.loads(out)["results"]
    assert code == 0 and [r["fits"] for r in got] == ["maybe"]
    assert list(got[0]["not_stated"]) == ["qg"] and got[0]["not_stated"]["qg"]


def test_why_shows_the_page():
    code, out, _e = run("opamp", "channels = 4", "--why")
    assert code == 0 and "channels: p." in out


def test_an_unknown_column_names_the_nearest_one():
    code, _o, err = run("mosfet", "rds_onn < 15m")
    assert code == 2 and "did you mean rds_on" in err


def test_package_from_the_kicad_footprint():
    assert E.package_of("Resistor_SMD:R_0603_1608Metric") == "0603"
    assert E.package_of("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm") == "SOIC-8"
    assert E.package_of("Diode_SMD:D_SMA") == "SMA"
    assert E.package_of("Package_TO_SOT_SMD:SOT-223-3_TabPin2") == "SOT-223-3"
    assert E.package_of("Package_DFN_QFN:QFN-28-1EP_5x5mm_P0.5mm_EP2.7x2.7mm") == "QFN-28-EP"
    assert E.package_of("Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical") == "JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical"
    assert E.package_of("Resistor_SMD:R_Array_Convex_4x0603") == "Array_Convex_4x0603"
    assert not E.PP.pkg_keys(E.package_of("Resistor_SMD:R_Array_Convex_4x0603")) & E.PP.pkg_keys("0603")
    assert E.package_of(None) is None


def show(*args):
    r = subprocess.run([os.path.join(REPO, "bin", "cloud-parts"), "show"] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout


def test_show_gives_every_field_and_every_file():
    code, out = show("LM317DCYR", "--json")
    assert code == 0
    p = json.loads(out)["parts"][0]
    assert p["kind"] == "ldo" and p["kicad_symbol"] == "Regulator_Linear:LM317_SOT-223"
    assert [f["field"] for f in p["fields"]] == list(E.PP.fields_of("ldo"))
    vin = next(f for f in p["fields"] if f["field"] == "vin")
    assert vin["value"] and vin["page"]
    assert all(f["value"] is not None or f["null_reason"] for f in p["fields"])
    assert p["files"]["symbol_file"]["path"] == "kicad/symbols/Regulator_Linear.kicad_sym"
    assert p["files"]["footprint_file"]["path"].endswith("Package_TO_SOT_SMD.pretty/SOT-223-3_TabPin2.kicad_mod")


def test_show_is_case_blind_and_names_a_miss():
    assert show("lm317dcyr")[0] == 0
    code, out = show("NO-SUCH-PART-1")
    assert code == 1 and "no part NO-SUCH-PART-1" in out


# The --json shapes other toolchains parse: changing a key is a breaking change.
PART = ["mpn", "kind", "manufacturer", "package", "footprint", "symbol", "description",
        "jlc", "lcsc", "price", "params"]
FIT = ["fits", "sort_key", "values", "not_stated"]


def test_json_shapes_are_pinned():
    words = json.loads(run("schottky", "sma", "--json")[1])
    assert list(words) == ["mode", "words", "total", "shown", "results"] and words["mode"] == "words"
    assert words["total"] >= words["shown"] == len(words["results"])
    assert all(list(r) == PART for r in words["results"])
    cond = json.loads(run("ldo", "vin_max >= 20 and iout >= 1 and dropout < 0.5", "--json", "--why")[1])
    assert list(cond) == ["mode", "kinds", "condition", "total", "shown", "results",
                          "skipped_kinds"] and cond["mode"] == "condition"
    assert all(list(r) == PART + FIT + ["why"] for r in cond["results"])
    assert {r["fits"] for r in cond["results"]} == {"yes", "maybe"}
    for r in cond["results"]:
        for cell in r["why"].values():
            assert set(cell) in ({"value", "page", "condition"}, {"value", "null_reason"})
    plain = json.loads(run("ldo", "iout >= 1", "--json")[1])
    assert all(list(r) == PART + FIT for r in plain["results"])
    assert list(json.loads(run("ldo", "nonsense ==", "--json")[1])) == ["error"]
    shown = json.loads(show("LM317DCYR", "NO-SUCH-PART-1", "--json")[1])
    assert list(shown) == ["parts", "not_found"] and shown["not_found"] == ["NO-SUCH-PART-1"]
    p = shown["parts"][0]
    assert list(p) == ["mpn", "manufacturer", "kind", "series", "unit", "kicad_symbol", "footprint",
                       "jlc", "lcsc", "price", "files",
                       "datasheet_revision", "datasheet_pages",
                       "sheet", "fields"]
    assert p["sheet"] is None or list(p["sheet"]) == ["path", "page", "render", "tables", "curves", "circuits"]
    assert list(p["files"]) == ["symbol_file", "footprint_file", "model_file"]
    assert all(list(v) == ["path", "present"] for v in p["files"].values())
    assert isinstance(p["datasheet_pages"], int)
    assert all(list(f) == ["field", "value", "shown", "page", "condition", "null_reason"] for f in p["fields"])


def test_every_name_find_prints_is_accepted_by_show():
    """An agent copies the part number find printed into show: every kind, text and --json."""
    import re
    names = set()
    for kind in sorted(E.PP.kinds()):
        code, out, _e = run("--table", kind)
        if code != 0 or not out.strip():
            continue
        rows = [l.split("\t")[0] for l in out.splitlines() if l.strip()]
        names |= set(rows)
        js = json.loads(run(kind, "channels >= 0 or channels < 0", "--json")[1] or "{}").get("results", [])
        names |= {r["mpn"] for r in js}
        text = run(kind, "channels >= 0 or channels < 0")[1]
        starts = {l.strip().split("  ")[0] for l in text.splitlines() if re.match(r"  \S", l)}
        assert {r["mpn"] for r in js} <= starts, kind      # the text output leads with the same part numbers
    sel = {d["mpn"] for _f, d in E.parts()}
    assert names <= sel, sorted(names - sel)[:5]
    code, out = show(*sorted(names), "--json")
    got = json.loads(out)
    assert code == 0 and got["not_found"] == [] and len(got["parts"]) == len(names)


def test_show_takes_the_file_safe_form_and_spaces():
    with_space = [d["mpn"] for _f, d in E.parts() if " " in d["mpn"]]
    assert len(with_space) >= 6
    for m in with_space + ["DAC7513N/3K"]:
        for form in (m, E.folder_name(m), m.lower()):
            code, out = show(form, "--json")
            assert code == 0 and json.loads(out)["parts"][0]["mpn"] == m, form


# -- a query reads what it asks for and no more --------------------------------

WATCHER = """
import runpy, sys
seen = []
sys.addaudithook(lambda event, args: event == "open" and seen.append(str(args[0])))
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit:
    pass
sys.stderr.write("\\nTABLES " + " ".join(sorted({p for p in seen if p.endswith(".jsonl")})))
"""


def tables_opened(*args):
    """(exit code, the table files one run of the CLI opened, by name). An
    audit hook sees every open the run makes, the table/ files among them."""
    r = subprocess.run([sys.executable, "-c", WATCHER, os.path.join(REPO, "bin", "cloud-parts")] + list(args),
                       capture_output=True, text=True)
    said = [l for l in r.stderr.splitlines() if l.startswith("TABLES ")][-1]
    return r.returncode, sorted(os.path.basename(p) for p in said[len("TABLES "):].split())


def test_a_search_over_one_kind_opens_that_kinds_table_and_no_other():
    """What the query names is what is read: `find ldo ...` opens
    table/ldo.jsonl, not the 15 MB the other fifty kinds hold."""
    code, opened = tables_opened("find", "ldo", "vout = 3.3 and iout >= 500m")
    assert code == 0 and opened == ["ldo.jsonl"]
    code, opened = tables_opened("find", "capacitor", "capacitance = 100n and package = '0402'")
    assert code == 0 and opened == ["capacitor.jsonl"]
    # a kind word standing for several kinds reads each of them, and still no more
    code, opened = tables_opened("find", "fet", "vds >= 30")
    assert code == 0 and opened == sorted("%s.jsonl" % k for k in E.PP.kind_words("fet"))
    # show reads the named part's kind; a word search reads every kind, as it must
    assert tables_opened("show", "AMS1117-3.3")[1] == ["ldo.jsonl"]
    assert len(tables_opened("find", "ams1117")[1]) == len(E.table_kinds()) > 40


def test_stacked_pin_numbers_expand():
    import kicad_pins
    assert kicad_pins.expand("[1,15,38,39]") == {"1", "15", "38", "39"}
    assert kicad_pins.expand("[4-6]") == {"4", "5", "6"}
    assert kicad_pins.expand("A12") == {"A12"}


# -- the JLC availability mark: a frozen snapshot carried as two more columns --

def test_jlc_mark_is_a_searchable_column():
    """`jlc` and `lcsc` read like any other column, and the values are the
    four the snapshot records: basic, preferred, extended, or empty."""
    code, out, _ = run("resistor", "jlc = 'basic' and package = '0805'", "--json", "--limit", "0")
    got = json.loads(out)
    assert code == 0 and got["total"] == len(got["results"]) >= 1
    assert {r["jlc"] for r in got["results"]} == {"basic"}
    assert all(r["lcsc"] and r["lcsc"].startswith("C") for r in got["results"])
    seen = {(d.get("jlc") or "") for _f, d in E.parts()}
    assert seen <= {"", "basic", "preferred", "extended"}, sorted(seen)


def test_every_marked_part_has_an_lcsc_number_and_the_rest_have_neither():
    import csv
    with open(E.SELECTION, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {"jlc", "lcsc"} <= set(rows[0]), "selection.csv carries the two JLC columns"
    for r in rows:
        assert bool(r["jlc"]) == bool(r["lcsc"]), r["mpn"]
        assert not r["lcsc"] or r["lcsc"].startswith("C"), r["mpn"]


def test_a_jlc_basic_part_comes_first():
    """Where a value exists as both a JLC basic part and a backbone part, the
    basic one is listed first; --sort still decides when it is given."""
    code, out, _ = run("resistor", "resistance = 10k and tolerance = 1 and package = '0603'",
                       "--json", "--limit", "0")
    got = json.loads(out)
    marks = [r["jlc"] for r in got["results"] if r["fits"] == "yes"]
    assert code == 0 and "basic" in marks and marks[0] == "basic", marks
    assert marks == sorted(marks, key=lambda m: E.PP.JLC_RANK.get(m or "", 3))


def test_limit_holds_rows_back_but_never_the_count():
    code, out, _ = run("resistor", "tolerance = 1 and package = '0402'")
    total = int(out.strip().splitlines()[-1].split()[0])
    assert code == 0 and total > 50
    assert "showing the first 50" in out
    assert sum(1 for line in out.splitlines() if line.startswith("  ")) == 50
    code, out2, _ = run("resistor", "tolerance = 1 and package = '0402'", "--limit", "0")
    assert code == 0 and "showing the first" not in out2
    assert sum(1 for line in out2.splitlines() if line.startswith("  ")) == total
    j = json.loads(run("resistor", "tolerance = 1 and package = '0402'", "--json")[1])
    assert j["total"] == total and j["shown"] == 50 == len(j["results"])


def test_lcsc_number_finds_its_part():
    code, out, _ = run("C25804", "--json")
    got = json.loads(out)
    assert code == 0 and any(r["lcsc"] == "C25804" for r in got["results"])
