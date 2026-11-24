"""The kinds this library's schema carries for parts that would otherwise be
filed under other (dac, jfet, thyristor, isolation_amplifier, transformer,
rf_amplifier, rf_filter): each loads with its fields, its kind words resolve,
and `cloud-parts find` runs on it."""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import cloud_parts as E  # noqa: E402

PP = E.PP
NEW = ["dac", "jfet", "thyristor", "isolation_amplifier", "transformer", "rf_amplifier", "rf_filter"]


def test_new_kinds_load():
    for k in NEW:
        assert k in PP.kinds()
        fs = PP.kinds()[k]["fields"]
        assert 4 <= len(fs) <= 8, k
        assert set(PP.fields_of(k)) >= set(fs)
        for spec in fs.values():
            assert spec["type"] in PP.schema()["types"]
            if spec["type"] in ("enum", "enums"):
                assert spec["values"]
        assert all(a in fs for a in PP.kinds()[k]["aliases"].values()), k


def test_kind_words():
    assert PP.kind_words("triac") == ["thyristor"]
    assert PP.kind_words("scr") == ["thyristor"]
    assert PP.kind_words("lna") == ["rf_amplifier"]
    assert PP.kind_words("isolated_amplifier") == ["isolation_amplifier"]


def find(*args):
    return subprocess.run([os.path.join(REPO, "bin", "cloud-parts"), "find"] + list(args), capture_output=True, text=True)


def test_find_dac_runs():
    assert find("dac").returncode == 0
    r = find("--help", "dac")
    assert r.returncode == 0 and "bits" in r.stdout and "settling" in r.stdout
    # a query on a new kind parses (exit 2 is a query error); 1 is "no part of that kind fits"
    for args in (["dac", "bits >= 12 and interface = 'spi'"], ["triac", "vdrm >= 600"],
                 ["rf_amplifier", "gain > 15"], ["jfet", "channel = 'n'"]):
        r = find(*args)
        assert r.returncode in (0, 1) and "Traceback" not in r.stderr, (args, r.stderr)
