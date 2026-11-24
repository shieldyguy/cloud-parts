"""The price snapshot (prices.csv): one row per priced part of the library, the
`price` column every kind has, searched in USD per piece when buying 100, shown
by find when the condition reads it and by show with its break, basis, source
and date; every part has one, a part no seller quoted from a close relative
(basis family)."""
import csv
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import cloud_parts as E  # noqa: E402

COLUMNS = ["mpn", "kind", "source", "basis", "quantity", "unit_price", "currency", "date"]


def cli(*args):
    r = subprocess.run([os.path.join(REPO, "bin", "cloud-parts")] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def rows():
    with open(E.PRICES, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_every_row_names_one_part_of_the_library_with_a_price_it_can_have():
    with open(E.SELECTION, encoding="utf-8", newline="") as f:
        kind_of = {r["mpn"]: r["kind"] for r in csv.DictReader(f)}
    with open(E.PRICES, encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == COLUMNS
    rs = rows()
    assert rs and len({r["mpn"] for r in rs}) == len(rs)
    for r in rs:
        assert kind_of.get(r["mpn"]) == r["kind"], r["mpn"]
        assert r["source"] in E.PRICE_SOURCES and r["basis"] in E.PRICE_BASES
        q, p = int(r["quantity"]), float(r["unit_price"])
        assert p > 0 and "e" not in r["unit_price"].lower()
        if r["basis"] == "at_100":
            assert 1 <= q <= E.PRICE_QUANTITY
        elif r["basis"] == "min_order_above_100":
            assert q > E.PRICE_QUANTITY
        elif r["basis"] == "one_piece_last_seen":
            assert q == 1 and r["source"] == "jlcpcb"
        assert r["currency"] == "USD"
    assert {r["mpn"] for r in rs} == set(kind_of)


def test_find_by_price_reads_usd_at_100_and_shows_it():
    code, out, _e = cli("find", "resistor", "resistance = 10k and price < 0.002", "--json", "--limit", "0")
    got = json.loads(out)["results"]
    assert code == 0
    yes = [r for r in got if r["fits"] == "yes"]
    assert yes
    for r in yes:
        assert r["price"]["currency"] == "USD" and r["price"]["unit_price"] < 0.002
        assert r["values"]["price"].startswith("%g" % r["price"]["unit_price"])
        assert r["params"]["resistance"] == 10000
    code, out, _e = cli("find", "resistor", "resistance = 10k and price < 0.002", "--sort", "price")
    assert code == 0 and "price=" in out and "USD@100" in out


def test_sort_by_price_puts_the_cheapest_first():
    got = json.loads(cli("find", "ldo", "price > 0", "--sort", "price", "--json", "--limit", "0")[1])["results"]
    ps = [r["price"]["unit_price"] for r in got if r["fits"] == "yes"]
    assert len(ps) > 10 and ps == sorted(ps)


def test_a_family_price_is_searchable_and_marked():
    fam = next(r for r in rows() if r["basis"] == "family" and r["kind"] == "resistor")
    code, out, _e = cli("find", "resistor", "price < 1000 and mpn = '%s'" % fam["mpn"], "--json")
    got = json.loads(out)["results"]
    assert code == 0 and [r["fits"] for r in got] == ["yes"]
    assert got[0]["price"]["basis"] == "family" and got[0]["values"]["price"].startswith("~")
    assert "roughly" in cli("show", fam["mpn"])[1]


def test_a_part_without_a_price_may_fit_with_the_reason():
    unpriced = next((d for _f, d in E.parts(["resistor"]) if d["price_quote"] is None), None)
    if unpriced is None:
        return
    code, out, _e = cli("find", "resistor", "price < 1 and mpn = '%s'" % unpriced["mpn"], "--json")
    got = json.loads(out)["results"]
    assert code == 0 and [r["fits"] for r in got] == ["maybe"]
    assert got[0]["price"] is None and "price" not in got[0]["values"]
    assert list(got[0]["not_stated"]) == ["price"] and "no price in the %s snapshot" % E.PRICE_SNAPSHOT \
        in got[0]["not_stated"]["price"]


def test_a_price_in_another_currency_is_shown_but_not_compared():
    other = next((r for r in rows() if r["currency"] != E.PRICE_CURRENCY), None)
    if other is None:
        return
    code, out, _e = cli("find", other["kind"], "price < 1000 and mpn = '%s'" % other["mpn"], "--json")
    r = json.loads(out)["results"][0]
    assert r["fits"] == "maybe" and r["price"]["currency"] == other["currency"]
    assert other["currency"] in r["values"]["price"] and other["currency"] in r["not_stated"]["price"]


def test_show_gives_the_price_with_its_break_source_and_date():
    p = json.loads(cli("show", "DMMT3904W-7-F", "--json")[1])["parts"][0]
    assert p["price"] == {"unit_price": 0.1452, "currency": "USD", "quantity": 50, "basis": "at_100",
                          "source": "jlcpcb", "date": "2026-11-23"}
    out = cli("show", "DMMT3904W-7-F")[1]
    assert "price          0.1452 USD each at 100 pieces (JLCPCB's 50+ break), observed 2026-11-23" in out


def test_show_says_plainly_when_a_part_has_no_price():
    unpriced = next((d for _f, d in E.parts(["resistor"]) if d["price_quote"] is None), None)
    if unpriced is None:
        return
    code, out, _e = cli("show", unpriced["mpn"], "--json")
    assert code == 0 and json.loads(out)["parts"][0]["price"] is None
    out = cli("show", unpriced["mpn"])[1]
    assert "price          none: no price in the %s snapshot" % E.PRICE_SNAPSHOT in out


def test_price_is_a_number_column():
    code, _o, err = cli("find", "resistor", "price < '0.01'")
    assert code == 2 and "price is a number" in err
    assert "price" in E.PP.help("resistor", E.parts(["resistor"]))
