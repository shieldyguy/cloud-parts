"""The install self-check: is everything an offline agent needs here, and does the search run?

    python3 scripts/check_install.py [--require-datasheets]

Prints one table: parts, table rows, datasheets (optional: they live in the separate
cloud-datasheets repository; found under $CLOUD_DATASHEETS, ../cloud-datasheets or ./datasheets;
each one's sha256 checked against selection.csv; --require-datasheets makes their absence a failure),
the price snapshot (prices.csv: a row naming a part not in the library is an error),
symbols resolving, footprints resolving, the four symbol/footprint checks of lib/kicad_pins.py,
models present, the KiCad library tables, and
whether `cloud-parts find ldo` runs. Exit 1 with the reasons when anything is missing. A footprint whose
model KiCad's 3D library never had (model_present = no in selection.csv) is counted, not failed.
Standard library only."""
import csv
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def datasheet_status(rows, find):
    """(distinct datasheets, [present and right], [not found], [found but changed]) for selection rows;
    find(datasheet_path) -> absolute path or None. A datasheet is right when its sha256 is the one
    selection.csv records for it."""
    sheets = {}
    for r in rows:
        sheets.setdefault(r["datasheet_path"], r)
    good, missing, changed = [], [], []
    for p, r in sheets.items():
        full = find(p)
        if not full:
            missing.append(p)
        elif sha256_of(full) == r["sha256"]:
            good.append(p)
        else:
            changed.append(p)
    return len(sheets), good, missing, changed


def main(argv):
    require = "--require-datasheets" in argv
    rows_out, problems = [], []

    def line(what, got, of=None, ok=True, note=""):
        state = ok if isinstance(ok, str) else ("ok" if ok else "MISSING")
        rows_out.append((what, "%s" % got if of is None else "%s of %s" % (got, of), state, note))

    sel_path = os.path.join(ROOT, "selection.csv")
    if not os.path.isfile(sel_path):
        print("check: selection.csv not found in %s" % ROOT)
        return 1
    with open(sel_path, encoding="utf-8") as f:
        sel = list(csv.DictReader(f))
    line("parts", len(sel))

    table_mpns = set()
    tdir = os.path.join(ROOT, "table")
    for name in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        if name.endswith(".jsonl"):
            with open(os.path.join(tdir, name), encoding="utf-8") as f:
                for l in f:
                    if l.strip():
                        table_mpns.add(json.loads(l)["mpn"])
    n_in = sum(1 for r in sel if r["mpn"] in table_mpns)
    line("table rows", n_in, len(sel), n_in == len(sel))
    if n_in != len(sel):
        problems.append("%d part(s) have no table row" % (len(sel) - n_in))

    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import cloud_parts

    # The JLC availability mark: a frozen snapshot, every class one of the four
    # the snapshot records, and a class and an LCSC number always together.
    classes = {"", "basic", "preferred", "extended"}
    if "jlc" not in sel[0] or "lcsc" not in sel[0]:
        line("JLC mark", "selection.csv has no jlc/lcsc column", ok=False)
        problems.append("selection.csv is missing the jlc and lcsc columns")
    else:
        bad = [r["mpn"] for r in sel
               if (r["jlc"] or "") not in classes or bool(r["jlc"]) != bool(r["lcsc"])
               or (r["lcsc"] and not r["lcsc"].startswith("C"))]
        marked = sum(1 for r in sel if r["jlc"])
        counts = {c: sum(1 for r in sel if (r["jlc"] or "") == c) for c in ("basic", "preferred", "extended")}
        line("JLC mark", marked, len(sel), not bad,
             "basic %d, preferred %d, extended %d; a frozen snapshot of %s"
             % (counts["basic"], counts["preferred"], counts["extended"], cloud_parts.JLC_SNAPSHOT))
        if bad:
            problems.append("%d part(s) have a JLC mark that is not a class with an LCSC number: %s"
                            % (len(bad), ", ".join(bad[:5])))

    # The price snapshot: every row names a part of the library, once, with a
    # source, basis, quantity, price, currency and date it can have.
    if not os.path.isfile(cloud_parts.PRICES):
        line("prices", "prices.csv not found", ok=False)
        problems.append("prices.csv is missing")
    else:
        with open(cloud_parts.PRICES, encoding="utf-8", newline="") as f:
            prices = list(csv.DictReader(f))
        kind_of = {r["mpn"]: r["kind"] for r in sel}
        unknown = [r["mpn"] for r in prices if r["mpn"] not in kind_of]
        seen, twice, bad = set(), [], []
        for r in prices:
            if r["mpn"] in seen:
                twice.append(r["mpn"])
            seen.add(r["mpn"])
            try:
                ok_row = (r["mpn"] in kind_of and r["kind"] == kind_of[r["mpn"]]
                          and r["source"] in cloud_parts.PRICE_SOURCES and r["basis"] in cloud_parts.PRICE_BASES
                          and int(r["quantity"]) > 0 and float(r["unit_price"]) > 0
                          and re.fullmatch(r"[A-Z]{3}", r["currency"] or "")
                          and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"] or "")
                          and (r["basis"] != "at_100" or int(r["quantity"]) <= cloud_parts.PRICE_QUANTITY)
                          and (r["basis"] != "min_order_above_100" or int(r["quantity"]) > cloud_parts.PRICE_QUANTITY))
            except (TypeError, ValueError, KeyError):
                ok_row = False
            if r["mpn"] in kind_of and not ok_row:
                bad.append(r["mpn"])
        by_src = {s: sum(1 for r in prices if r["source"] == s and r["mpn"] in kind_of)
                  for s in cloud_parts.PRICE_SOURCES}
        line("prices", len(seen - set(unknown)), len(sel), not (unknown or twice or bad),
             "%s; at %d pieces, a snapshot of %s; %d in a currency other than %s; %d without a price"
             % (", ".join("%s %d" % kv for kv in by_src.items()), cloud_parts.PRICE_QUANTITY,
                cloud_parts.PRICE_SNAPSHOT,
                sum(1 for r in prices if r["currency"] != cloud_parts.PRICE_CURRENCY and r["mpn"] in kind_of),
                cloud_parts.PRICE_CURRENCY, len(sel) - len(seen - set(unknown))))
        if unknown:
            problems.append("prices.csv: %d row(s) name a part not in the library: %s"
                            % (len(unknown), ", ".join(unknown[:5])))
        if twice:
            problems.append("prices.csv: %d part(s) priced twice: %s" % (len(twice), ", ".join(twice[:5])))
        if bad:
            problems.append("prices.csv: %d row(s) with a kind, source, basis, quantity, price, currency or "
                            "date that cannot be: %s" % (len(bad), ", ".join(bad[:5])))

    n, good, missing, changed = datasheet_status(sel, cloud_parts.find_datasheet)
    how = "sha256 checked"
    if len(missing) == n:
        if require:             # the PDFs are optional: with none installed and none required, no row
            line("datasheets present", 0, n, ok="MISSING")
            problems.append("no datasheets found (--require-datasheets): place cloud-datasheets beside this "
                            "folder or set CLOUD_DATASHEETS")
    elif missing:
        line("datasheets present", len(good), n, ok="MISSING" if require else "partial",
             note="%s; %d not present (optional)" % (how, len(missing)))
        if require:
            problems.append("%d datasheet(s) not found, e.g. %s" % (len(missing), missing[0]))
    else:
        line("datasheets present", len(good), n, not changed, how)
    if changed:
        problems.append("%d datasheet(s) differ from the sha256 selection.csv records, e.g. %s"
                        % (len(changed), changed[0]))

    found = [s for s in (cloud_parts.find_sheet(r["mpn"], r["datasheet_path"]) for r in sel) if s]
    have = len(found)
    if have:
        line("reference sheets", have, len(sel), ok="ok", note="parts with a sheet in cloud-sheets (optional)")
        pages = {s["path"]: s["page"] for s in found}
        made = sum(1 for p in pages.values() if p)
        line("sheet pages", made, len(pages), ok="ok" if made == len(pages) else "partial",
             note="each sheet as an HTML page; index.html in cloud-sheets lists them" if made == len(pages)
             else "run ./install.sh to write them")
    else:
        line("reference sheets", "not installed (optional): place cloud-sheets beside this folder", ok="ok",
             note="or set CLOUD_SHEETS")

    symdir = os.path.join(ROOT, "kicad", "symbols")
    names = {}
    for fn in os.listdir(symdir) if os.path.isdir(symdir) else []:
        if fn.endswith(".kicad_sym"):
            text = open(os.path.join(symdir, fn), encoding="utf-8").read()
            names[fn[:-len(".kicad_sym")]] = set(re.findall(r'^\t\(symbol "((?:[^"\\]|\\.)*)"', text, re.M))
    ok_s = 0
    for r in sel:
        lib, _, n = r["kicad_symbol"].partition(":")
        ok_s += n in names.get(lib, ())
    line("symbols resolving", ok_s, len(sel), ok_s == len(sel), "parts whose Lib:Name is in kicad/symbols")
    if ok_s != len(sel):
        problems.append("%d part(s) whose symbol is not in kicad/symbols" % (len(sel) - ok_s))

    ok_f = 0
    for r in sel:
        lib, _, n = r["footprint"].partition(":")
        ok_f += os.path.isfile(os.path.join(ROOT, "kicad", "footprints", lib + ".pretty", n + ".kicad_mod"))
    line("footprints resolving", ok_f, len(sel), ok_f == len(sel), "parts whose footprint file is there")
    if ok_f != len(sel):
        problems.append("%d part(s) whose footprint is not in kicad/footprints" % (len(sel) - ok_f))

    sys.path.insert(0, os.path.join(ROOT, "lib"))
    import kicad_pins
    notes = {"pins have pads": "every symbol pin is a pad on its footprint",
             "pads have pins": "every pad is a symbol pin, a mounting pad, or listed in pairing-exceptions.csv",
             "footprint fits symbol": "the footprint is the symbol's own or one its filters name, or listed",
             "size code": "a passive's EIA size code is its footprint's"}
    for check, (bad, stale) in kicad_pins.report(sel, kicad_pins.Library(os.path.join(ROOT, "kicad"))).items():
        line(check, len(sel) - len(bad), len(sel), "ok" if not bad and not stale else "FAIL",
             notes[check] + ("; %d stale exception(s)" % len(stale) if stale else ""))
        if bad:
            problems.append("%s: %d part(s), e.g. %s %s" % (check, len(bad), bad[0][0], bad[0][1]))
        if stale:
            problems.append("%s: %d exception(s) in pairing-exceptions.csv no longer match, e.g. %s %s"
                            % (check, len(stale), stale[0][0], stale[0][1]))

    with_model = [r for r in sel if r.get("model_present") == "yes"]
    have_m = sum(1 for r in with_model if os.path.isfile(os.path.join(ROOT, r["model_path"])))
    none_up = sum(1 for r in sel if r.get("model_present") == "no")
    line("models present", have_m, len(with_model), have_m == len(with_model),
         "%d part(s) have no model in KiCad's 3D library" % none_up)
    if have_m != len(with_model):
        problems.append("%d STEP model(s) missing from kicad/3dmodels" % (len(with_model) - have_m))

    tables = [os.path.join(ROOT, "kicad", t) for t in ("sym-lib-table", "fp-lib-table")]
    tok = all(os.path.isfile(t) for t in tables)
    line("KiCad library tables", "kicad/sym-lib-table, kicad/fp-lib-table" if tok else "not found", ok=tok)
    if not tok:
        problems.append("kicad/sym-lib-table or kicad/fp-lib-table missing")

    r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "cloud-parts"), "find", "ldo"],
                       capture_output=True, text=True)
    m = re.search(r"^(\d+) part\(s\)", r.stdout, re.M)
    n_ldo = sum(1 for x in sel if x["kind"] == "ldo")
    sok = bool(m) and int(m.group(1)) == n_ldo
    line("search works", "cloud-parts find ldo: %s" % (m.group(1) if m else "failed"), n_ldo, sok)
    if not sok:
        problems.append("`cloud-parts find ldo` did not list the %d ldo parts: %s" % (n_ldo, (r.stderr or r.stdout)[-300:]))

    w = max(len(x[0]) for x in rows_out)
    v = max(len(x[1]) for x in rows_out)
    for what, got, state, note in rows_out:
        print("  %-*s  %-*s  %-7s  %s" % (w, what, v, got, state, note))
    if problems:
        print("\nnot ready:")
        for p in problems:
            print("  - " + p)
        return 1
    print("\nready: %s" % ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
