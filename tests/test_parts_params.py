"""Tests of lib/parts_params.py with the schema in schema/:
Filed params and cloud-parts find over them. The
schema is the one vocabulary. `cloud-parts find KIND "COND"` is SQL over a table
of the kind, where a value not filed is unknown and a part whose condition
is unknown is kept as may fit, never dropped; `cloud-parts find WORDS` greps
everything written about a part, JLC's spec table included."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from cloud_parts import PP as P  # noqa: E402  (lib/parts_params.py, its schema at schema/parts_params.json)


def parts(*params, **extra):
    return [("/x/P%d" % i, dict({"mpn": "P%d" % i, "description": "", "package": "", "category": "",
                                 "params": p}, **extra.get("d%d" % i, {})))
            for i, p in enumerate(params)]


def fits(kind, cond, *params, **kw):
    """{mpn: 'yes'|'maybe'} for a kind search."""
    r = P.find(parts(*params, **kw.pop("extra", {})), [kind, cond], **kw)
    return {res[2]["mpn"]: res[3] for res in r["results"]}


def yes(kind, cond, *params, **kw):
    return sorted(m for m, f in fits(kind, cond, *params, **kw).items() if f == "yes")


NFET = {"kind": "mosfet", "channel": "n", "vds": 30, "rds_on": [{"value": 0.0052, "vgs": 10}, {"value": 0.008, "vgs": 4.5}],
        "vgs_th": [1.0, 2.5]}
PFET = {"kind": "mosfet", "channel": "p", "vds": 30, "rds_on": [{"value": 0.0095, "vgs": 10}]}


# the schema holds together

def test_every_alias_names_a_field_and_every_kind_word_a_kind():
    for k, spec in P.kinds().items():
        for a, f in spec.get("aliases", {}).items():
            assert f in P.fields_of(k), (k, a, f)
        for f, s in spec["fields"].items():
            assert s["type"] in P.schema()["types"], (k, f)
            if s["type"] in ("enum", "enums"):
                assert s["values"]
            if s["type"] == "cond":
                assert s.get("at") and s.get("at_unit")
    for a in P.schema()["kind_aliases"]:
        ks, terms = P.kind_word_terms(a)
        assert ks and all(k in P.kinds() for k in ks), a
        for f, v in terms.items():                  # an implied term must be words its field has
            for k in ks:
                spec = P.fields_of(k)[P.resolve_field(k, f)]
                if spec["type"] == "bool":
                    assert str(v) in ("yes", "no"), (a, f, v)
                else:
                    assert all(w.strip() in spec["values"] for w in str(v).split(",")), (a, f, v)


def test_a_kind_word_can_carry_its_term():
    sch = {"kind": "diode", "type": "schottky", "vr": 40}
    rect = {"kind": "diode", "type": "rectifier", "vr": 1000}
    assert yes("schottky", "vr >= 30", sch, rect) == ["P0"]
    assert yes("diode", "vr >= 30", sch, rect) == ["P0", "P1"]


def test_a_yes_no_kind_word():
    ptc = {"kind": "fuse", "resettable": True}
    glass = {"kind": "fuse", "resettable": False}
    assert fits("ptc", "current > 0", ptc, glass) == {"P0": "maybe"}      # the glass fuse is known not resettable


def test_no_column_name_collides():
    for k in P.kinds():
        names = [c for c, _t, _d in P.META] + [c for c, _f, _r, _s in P.columns(k)]
        assert len(names) == len(set(names)), k


# filing is refused unless the schema allows it

def test_validate_refuses_what_the_schema_does_not_allow():
    assert P.validate(NFET) == []
    assert P.validate({"kind": "fet"})
    assert any("channel" in e for e in P.validate({"kind": "mosfet", "channel": "N"}))
    assert any("vds" in e for e in P.validate({"kind": "mosfet", "vds": "30V"}))
    assert any("rds" in e for e in P.validate({"kind": "mosfet", "rds": 0.1}))
    assert P.validate({"kind": "ldo", "vin": [None, 15]}) == []
    assert P.validate({"kind": "ldo", "vin": [5, 2]})
    assert P.validate({"kind": "mosfet", "rds_on": [{"value": 0.01, "vg": 4.5}]})


# numbers as an engineer writes them

@pytest.mark.parametrize("num,suffix,want", [
    ("100", "n", 1e-7), ("0.1", "u", 1e-7), ("4", "R7", 4.7), ("1", "K5", 1500), ("4", "k7", 4700),
    ("2", "u2", 2.2e-6), ("2.2", "meg", 2.2e6), ("15", "m", 0.015), ("10", "k", 1e4), ("16", "M", 1.6e7),
    ("1", "V/us", 1e6), ("64", "Ki", 65536), ("1", "%", 1), ("1e-3", "", 0.001)])
def test_literals(num, suffix, want):
    assert P.literal(num, suffix) == pytest.approx(want)


@pytest.mark.parametrize("num,suffix", [("100", "nF"), ("16", "MHz"), ("3", "V3"), ("125", "C"), ("64", "KB"),
                                        ("5000", "K5x"), ("15", "mΩ"), ("25", "mF")])
def test_a_unit_after_a_number_is_an_error_never_a_guess(num, suffix):
    with pytest.raises(P.QueryError, match="prefix only"):
        P.literal(num, suffix)


def test_a_prefixed_value_equals_the_filed_float_exactly():
    cap = {"kind": "capacitor", "capacitance": 1e-07}
    assert yes("capacitor", "capacitance = 100n", cap) == ["P0"]
    assert yes("capacitor", "capacitance = 0.1u", cap) == ["P0"]
    with pytest.raises(P.QueryError):
        yes("capacitor", "capacitance = 100zz", cap)


# what the agent may and may not write

def test_n_channel_never_returns_p_channel():
    assert yes("mosfet", "channel = 'n'", NFET, PFET) == ["P0"]
    assert yes("fet", "channel = 'P'", NFET, PFET) == ["P1"]
    with pytest.raises(P.QueryError, match="never 'q'"):
        yes("fet", "channel = 'q'", NFET)


def test_a_misspelled_column_is_an_error_that_names_the_right_one():
    with pytest.raises(P.QueryError, match="rds_on"):
        yes("fet", "rds_onn < 15m", NFET)
    with pytest.raises(P.QueryError):
        yes("capacitor", "capacitence = 100n", NFET)


def test_another_name_for_a_column_reads_as_the_column():
    assert yes("fet", "rdson < 10m and polarity = 'n' and vth_max <= 3", NFET, PFET) == ["P0"]


def test_testing_for_empty_values_is_refused():
    for cond in ("rds_on is null", "coalesce(rds_on, 0) < 1", "case when 1 then 1 end > 0", "\"x\" = 1",
                 "1 = 1; drop table t", "load_extension('x')", "rds_on in (select 1)"):
        with pytest.raises(P.QueryError):
            yes("mosfet", cond, NFET)


def test_a_condition_without_a_kind_is_an_error():
    with pytest.raises(P.QueryError, match="needs a kind"):
        P.find(parts(NFET), ["rds_on < 10m"])


# nothing is dropped for a value not filed

def test_a_part_missing_a_value_may_fit_and_is_never_dropped():
    bare = {"kind": "mosfet", "channel": "n"}
    wrong = {"kind": "mosfet", "channel": "p"}
    got = fits("mosfet", "channel = 'n' and vds >= 20", NFET, bare, wrong)
    assert got == {"P0": "yes", "P1": "maybe"}


def test_not_and_or_keep_the_unknown_too():
    bare = {"kind": "mosfet", "channel": "n"}
    assert fits("mosfet", "not (vds < 20)", bare) == {"P0": "maybe"}
    assert fits("mosfet", "vds >= 20 or channel = 'n'", bare) == {"P0": "yes"}
    assert fits("mosfet", "vds between 20 and 40", bare, NFET) == {"P0": "maybe", "P1": "yes"}


def test_the_maybe_names_the_empty_column():
    bare = {"kind": "mosfet", "channel": "n"}
    r = P.find(parts(bare), ["mosfet", "vds >= 20"])
    assert r["results"][0][-1] == ["vds"]


def test_any_rating_counts_whatever_its_condition():
    only_at_10 = {"kind": "mosfet", "channel": "n", "rds_on": [{"value": 0.015, "vgs": 10}]}
    at_4 = {"kind": "mosfet", "channel": "n", "rds_on": [{"value": 0.018, "vgs": 4}]}
    assert yes("fet", "rds_on < 20m", only_at_10, at_4) == ["P0", "P1"]
    assert yes("fet", "rds_on <= 6m", NFET, PFET) == ["P0"]
    with pytest.raises(P.QueryError, match="never searched"):     # conditions are shown, never searched
        yes("fet", "rds_on < 10m and rds_on_vgs <= 4.5", NFET, only_at_10)


def test_a_range_is_two_columns():
    amp = {"kind": "opamp", "supply": [2.7, 5.5], "temp": [-40, 125]}
    ams = {"kind": "opamp", "supply": [None, 15]}
    assert fits("opamp", "supply_min <= 3.3 and supply_max >= 3.3", amp, ams) == {"P0": "yes", "P1": "maybe"}
    assert fits("opamp", "supply_max >= 12", amp, ams) == {"P1": "yes"}


def test_list_columns():
    mcu = {"kind": "mcu", "peripherals": ["usb", "can", "i2c"]}
    other = {"kind": "mcu", "peripherals": ["i2c"]}
    assert yes("mcu", "has(peripherals, 'usb') and has(peripherals, 'can')", mcu, other) == ["P0"]
    assert yes("mcu", "peripherals = 'usb'", mcu, other) == ["P0"]
    assert yes("mcu", "peripherals in ('usb', 'i2c')", mcu, other) == ["P0", "P1"]
    with pytest.raises(P.QueryError):
        yes("mcu", "has(peripherals, 'usbb')", mcu)


# text: a value that lives only in words

LL = {"kind": "mosfet", "channel": "n"}


def test_text_finds_what_only_the_description_says():
    extra = {"d0": {"description": "N-channel logic level FET"}, "d1": {"specs": {"Features": "Logic Level Gate"}}}
    assert yes("mosfet", "text like '%logic level%'", LL, dict(LL), NFET, extra=extra) == ["P0", "P1"]
    assert fits("mosfet", "vgs_th_max <= 2.5 or text like '%logic level%'", LL, NFET, extra=extra) == {
        "P0": "yes", "P1": "yes"}


def test_package_is_jlcs_name():
    led = {"kind": "led", "color": ["red"]}
    extra = {"d0": {"jlc_package": "0603", "package": "LED-SMD_L1.6-W0.8-R-RD"}}
    assert yes("led", "package = '0603' and color = 'red'", led, extra=extra) == ["P0"]
    words = P.find(parts(led, **extra), ["led", "red", "0603"])
    assert words["mode"] == "words" and [d["mpn"] for _f, d in words["results"]] == ["P0"]


def test_word_search_reads_everything_written():
    ps = [("/x/A", {"mpn": "BAT54", "description": "", "specs": {"Diode Configuration": "Series"},
                    "jlc_package": "SOT-23", "params": {"kind": "diode", "type": "schottky"}}),
          ("/x/B", {"mpn": "XYZ", "description": "no params", "params": None})]
    got = lambda *w: [d["mpn"] for _f, d in P.find(ps, list(w))["results"]]
    assert got("bat54") == ["BAT54"]
    assert got("series", "sot-23") == ["BAT54"]
    assert got("at54", "schottky") == ["BAT54"]   # inside a part number; a kind word that names it
    assert got("params") == ["XYZ"]
    assert got("bat54", "xyz") == []


def test_sort_puts_unknown_last():
    a = {"kind": "mosfet", "channel": "n", "qg": 2e-8}
    b = {"kind": "mosfet", "channel": "n"}
    c = {"kind": "mosfet", "channel": "n", "qg": 5e-9}
    r = P.find(parts(a, b, c), ["mosfet", "channel = 'n'"], sort="qg")
    assert [x[2]["mpn"] for x in r["results"]] == ["P2", "P0", "P1"]


def test_kind_word_then_plain_words_is_a_word_search():
    ps = [("/x/A", {"mpn": "SW1", "description": "tactile reset button", "params": {"kind": "switch"}}),
          ("/x/B", {"mpn": "SUP", "description": "voltage supervisor", "params": {"kind": "supervisor"}})]
    r = P.find(ps, ["reset", "button"])
    assert r["mode"] == "words" and [d["mpn"] for _f, d in r["results"]] == ["SW1"]
    for cond in ("rds_on is null", "rds_on in (select 1)", "rds_on < 1; drop table t"):
        with pytest.raises(P.QueryError):
            P.find(parts(NFET), ["mosfet", cond])
    assert P.find(parts(NFET), ["mosfet", "case b"])["mode"] == "words"   # a tantalum case is words


def test_words_fold_hyphens_units_and_values():
    ps = [("/x/A", {"mpn": "C1", "description": "Low-Noise Right Angle", "jlc_package": "SOP-8",
                    "params": {"kind": "capacitor", "capacitance": 1e-07, "automotive": True}})]
    got = lambda *w: len(P.find(ps, list(w))["results"])
    for w in (["low noise"], ["low-noise"], ["right-angle"], ["soic-8"], ["soic8"], ["0.1uf"], ["100n"],
              ["0.1µF"], ["aec-q200"], ["noise"]):
        assert got(*w) == 1, w


def test_package_family_and_typed_values():
    a = {"kind": "opamp", "channels": 2}
    ex = {"d0": {"jlc_package": "SO-8"}, "d1": {"jlc_package": "SOIC-8"}, "d2": {"jlc_package": "SOT-23-5"}}
    assert yes("opamp", "package = 'SOIC-8'", a, dict(a), dict(a), extra=ex) == ["P0", "P1"]
    with pytest.raises(P.QueryError, match="quote it"):
        yes("opamp", "package = 0603", a)
    with pytest.raises(P.QueryError, match="unquoted"):
        yes("opamp", "channels = '2'", a)
    with pytest.raises(P.QueryError, match="prefix only"):
        yes("mosfet", "rds_on < 15mV", NFET)
    with pytest.raises(P.QueryError, match="quote every item"):
        yes("opamp", "package in ('0402', 0603)", a)
    assert yes("opamp", "mpn = 'p0'", a) == ["P0"]                # text = ignores case on every column
    r = {"kind": "resistor", "power": 0.25}
    assert yes("resistor", "power >= 1/4", r, {"kind": "resistor", "power": 0.1}) == ["P0"]


def test_reversed_or_wrapped_list_columns_are_refused_not_empty():
    led = {"kind": "led", "color": ["red"]}
    assert yes("led", "color = 'red'", led) == ["P0"]
    for cond in ("'red' = color", "lower(color) = 'red'"):
        with pytest.raises(P.QueryError, match="list"):
            yes("led", cond, led)


def test_has_finds_longer_words_starting_with_it():
    mcu = {"kind": "mcu", "peripherals": ["canfd", "usb"]}
    assert yes("mcu", "has(peripherals, 'can')", mcu) == ["P0"]


def test_yes_no_columns_read_yes():
    led = {"kind": "led", "addressable": True}
    assert yes("led", "addressable = 'yes'", led, {"kind": "led", "addressable": False}) == ["P0"]
    assert yes("led", "addressable", led, {"kind": "led", "addressable": False}) == ["P0"]


def test_enum_words_fit_as_parts_and_other_names():
    rr = {"kind": "opamp", "rail_to_rail": "in_out"}
    assert yes("opamp", "rail_to_rail = 'out'", rr) == ["P0"]
    cap = {"kind": "capacitor", "dielectric": "C0G"}
    assert yes("capacitor", "dielectric = 'NP0'", cap) == ["P0"]
    sw = {"kind": "switching_regulator", "topology": "buck", "also": ["boost", "inverting"]}
    assert yes("switching_regulator", "topology = 'boost'", sw) == ["P0"]


def test_package_bracket_sizes_are_not_names():
    assert not P.pkg_keys("DFN-8(3x3)") & P.pkg_keys("QFN-16(3x3)")
    assert not P.pkg_keys("SOIC-8") & P.pkg_keys("SOIC-8-208mil")
    assert P.pkg_keys("SMA(DO-214AC)") & P.pkg_keys("DO-214AC")
    assert P.pkg_keys("TSOT-23-6") & P.pkg_keys("SOT-23-6")


def test_words_match_at_word_starts():
    t = P.tokens("CL10 0.1uF 110kΩ load capacitance Disposable fuses Right Angle unidirectional DO-214")
    assert not P.holds(t, "1uf") and not P.holds(t, "10k") and not P.holds(t, "adc")
    assert not P.holds(t, "efuse") and not P.holds(t, "ldo")
    assert P.holds(t, P.fold("right-angle")) and P.holds(t, "0.1uf") and P.holds(t, "do214")


def test_a_misspelled_sort_is_an_error():
    with pytest.raises(P.QueryError):
        P.find(parts(NFET), ["mosfet", "vds >= 0"], sort="rdsonn")


def test_memory_and_frequency_literals_on_their_columns():
    mem = {"kind": "memory", "size": 128 * 1024 ** 2}
    assert yes("memory", "size = 128Mi", mem) == ["P0"]
    mcu = {"kind": "mcu", "frequency": 72e6, "flash": 65536}
    assert yes("mcu", "(frequency) >= 48M and flash = 64Ki", mcu) == ["P0"]
    assert yes("mcu", "frequency >= 100M", mcu) == []


def test_sort_on_a_column_one_kind_lacks_keeps_every_kind():
    ldo = {"kind": "ldo", "vout": 3.3, "dropout": [{"value": 0.3, "iout": 0.1}]}
    buck = {"kind": "switching_regulator", "vout": 3.3}
    r = P.find(parts(ldo, buck), ["regulator", "vout = 3.3"], sort="dropout")
    assert sorted(x[2]["mpn"] for x in r["results"]) == ["P0", "P1"]


def test_hints_show_spec_lines_like_the_empty_field():
    d = {"specs": {"RDS(on)": "78mΩ@4.5V", "Package": "SOT-23"}}
    assert P.hints(d, ["rds_on"], "mosfet") == ["RDS(on): 78mΩ@4.5V"]


def test_show_reads_like_a_datasheet():
    f = P.fields_of("mosfet")
    assert P.show(f["rds_on"], NFET["rds_on"]) == "5.2mΩ@10V, 8mΩ@4.5V"
    assert P.show(P.fields_of("capacitor")["capacitance"], 1e-7) == "100nF"
    assert P.show(f["vgs_th"], [None, 2.5]) == "?~2.5V"




# round 3 of the review (11/9)

def test_memory_and_hz_prefixes_that_mislead_are_errors():
    mcu = {"kind": "mcu", "flash": 32768, "frequency": 72e6}
    for cond, msg in (("flash <= 32k", "1024s"), ("frequency >= 48m", "milli")):
        with pytest.raises(P.QueryError, match=msg):
            yes("mcu", cond, mcu)
    assert yes("mcu", "flash <= 32Ki and frequency >= 48M", mcu) == ["P0"]


def test_kind_word_with_implied_topology_reads_also():
    sw = {"kind": "switching_regulator", "topology": "buck", "also": ["boost"], "vin": [3, 40]}
    assert yes("boost", "vin_min <= 3", sw) == ["P0"]
    assert yes("switching_regulator", "topology in ('boost', 'inverting')", sw) == ["P0"]
    plain = {"kind": "switching_regulator", "topology": "buck"}
    assert fits("switching_regulator", "topology = 'boost'", plain) == {}     # no also filed: it has none


def test_quoted_columns_and_text_against_numbers_are_errors():
    for cond in ("'vds' >= 30", "'x' > 3", "3 < 'x'"):
        with pytest.raises(P.QueryError):
            yes("mosfet", cond, NFET)


def test_in_lists_are_conditions():
    cap = {"kind": "capacitor", "capacitance": 1e-07}
    assert yes("capacitor", "capacitance in (100n, 1u)", cap) == ["P0"]


def test_condition_fields_are_refused():
    with pytest.raises(P.QueryError, match="never searched"):
        yes("diode", "ir_voltage >= 20", {"kind": "diode", "ir": 1e-6, "ir_voltage": 75})


def test_enum_words_match_only_themselves_or_what_they_imply():
    assert yes("potentiometer", "taper = 'log'", {"kind": "potentiometer", "taper": "reverse_log"}) == []
    assert yes("mcu", "peripherals = 'usb'", {"kind": "mcu", "peripherals": ["usb_pd"]}) == []
    assert yes("audio", "class = 'a'", {"kind": "audio", "class": "ab"}) == []
    assert yes("bjt", "polarity = 'npn'", {"kind": "bjt", "polarity": "npn_pnp"}) == ["P0"]


def test_package_names_of_the_family_are_words():
    ps = [("/x/A", {"mpn": "U1", "jlc_package": "SOIC-8", "params": {"kind": "opamp"}})]
    for w in ("sop-8", "so-8", "soic8"):
        assert len(P.find(ps, [w])["results"]) == 1, w


def test_part_number_substring_reads_the_mpn_only():
    ps = [("/x/A", {"mpn": "0402WGF1001", "description": "C25120", "params": {"kind": "resistor"}})]
    assert P.find(ps, ["2512"])["results"] == []


def test_word_tokens_slashes_trailing_zeros_punctuation_plurals():
    ps = [("/x/A", {"mpn": "U1", "description": "Tx/Rx Enable/shutdown +125C", "params":
                    {"kind": "connector", "pitch": 2.0}}),
          ("/x/B", {"mpn": "D1", "description": "Q: 12@100MHz", "params": {"kind": "led"}})]
    got = lambda *w: [d["mpn"] for _f, d in P.find(ps, list(w))["results"]]
    for w in ("rx", "shutdown", "125c", "2.0mm", "(rx)", "rx,"):
        assert got(w) == ["U1"], w
    assert got("leds") == ["D1"]
    assert got("1210") == []                       # 12 and 100 are two numbers


def test_text_like_reads_hyphens_and_micro_as_written():
    led = {"kind": "connector"}
    ex = {"d0": {"description": "Right-Angle 10µF"}}
    assert yes("connector", "text like '%right angle%' and text like '%10uF%'", led, extra=ex) == ["P0"]
    assert yes("connector", "text like '%10µF%'", led, extra=ex) == ["P0"]


def test_digit_separators_and_unquoted_words_are_errors():
    with pytest.raises(P.QueryError, match="without _"):
        yes("resistor", "resistance >= 10_000", {"kind": "resistor"})
    with pytest.raises(P.QueryError, match="quote it"):
        yes("mosfet", "channel = n", NFET)
