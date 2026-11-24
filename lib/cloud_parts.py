"""The parameter table, presented to the parts search (lib/parts_params.py)
in the shape that search reads: one
(folder, part dict) per part, with `params` holding the kind and its
fields as plain typed values in the schema's units.

What this library has written about a part, and nothing else, is what the
word search reads: the MPN, the manufacturer, the kind, the KiCad symbol
and footprint names, a package name taken from the KiCad footprint name,
and a short description built here from the part's own filed values. No
distributor text, and no text of the manufacturer's.

Two columns say whether JLCPCB's assembly service offers the part and under
which catalogue number (`jlc`, `lcsc`, from selection.csv). They are a frozen
snapshot taken on one date, JLC_SNAPSHOT below, not live data: no stock, and
nothing is fetched to read them.

A third, `price`, is a frozen price snapshot (prices.csv, PRICE_SNAPSHOT
below): what one piece costs when buying 100, from JLCPCB's catalogue for a
part with an LCSC number and from Mouser for the rest, in USD, each row with
its price break, basis, source and date. A part neither seller priced has
the price of a close relative (basis family). The prices are roughly right,
not exact. The searchable `price` is that unit price.

Each value's page and condition stay in table/<kind>.jsonl and are carried
beside the part as `_table` (shown by `cloud-parts find --why`). The page
is the datasheet's own page: the PDFs are the separate cloud-datasheets
repository, and that is where the cited pages are. A field
whose value is null is left out of params, so the search treats it as not filed:
unknown, and a part whose condition reads it is kept as "may fit"; its
null_reason is what `--why` and the may-fit lines show.

Import parts_params through this module (`from cloud_parts import PP`):
the one seam is SCHEMA_PATH, pointed at schema/parts_params.json (or
$CLOUD_PARTS_SCHEMA) before the schema is first read.
"""
import csv
import glob
import json
import os
import re
import sys

LIB = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(LIB)
if LIB not in sys.path:
    sys.path.insert(0, LIB)

import parts_params as PP  # noqa: E402

PP.SCHEMA_PATH = os.environ.get("CLOUD_PARTS_SCHEMA") or os.path.join(REPO, "schema", "parts_params.json")
TABLE = os.environ.get("CLOUD_PARTS_TABLE") or os.path.join(REPO, "table")
SELECTION = os.path.join(REPO, "selection.csv")
PRICES = os.path.join(REPO, "prices.csv")

# The day selection.csv's `jlc` and `lcsc` columns hold, from JLCPCB's
# catalogue. One date for the whole library, never per row: the columns are a
# snapshot, and this is its date.
JLC_SNAPSHOT = "2026-11-15"

# The day prices.csv was taken. Each row also carries the day its own price
# was observed (`date`), which can be a few days earlier.
PRICE_SNAPSHOT = "2026-11-23"
PRICE_QUANTITY = 100                            # every price is quoted for buying this many
PRICE_CURRENCY = "USD"                          # the searchable `price` column's currency
PRICE_SOURCES = {"jlcpcb": "JLCPCB", "mouser": "Mouser"}
PRICE_BASES = ("at_100", "min_order_above_100", "one_piece_last_seen", "family")


# -- where the datasheets are -------------------------------------------------
# The PDFs live in the separate cloud-datasheets repository. A datasheet_path
# from selection.csv (datasheets/<kind>/<file>.pdf) is looked for, in order,
# under $CLOUD_DATASHEETS, under a sibling folder ../cloud-datasheets, and
# under this repository (a user may drop them in here).

DATASHEETS_REPO = "cloud-datasheets"


def datasheet_roots(env=None, repo=None):
    """The folders a datasheet_path is looked for under, in order."""
    env = os.environ if env is None else env
    repo = repo or REPO
    roots = []
    if env.get("CLOUD_DATASHEETS"):
        roots.append(env["CLOUD_DATASHEETS"])
    roots.append(os.path.join(os.path.dirname(os.path.abspath(repo)), DATASHEETS_REPO))
    roots.append(repo)
    return roots


def find_datasheet(rel, env=None, repo=None):
    """The absolute path of datasheet_path `rel` where it is, or None."""
    if not rel:
        return None
    for root in datasheet_roots(env, repo):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


# -- the reference sheets ------------------------------------------------------
# A part's reference sheet (its datasheet's facts as a data file, with drawn
# circuits) is in the separate cloud-sheets repository, optional here like the
# PDFs: under $CLOUD_SHEETS, or a sibling folder ../cloud-sheets. Its index.csv
# has one row per sheet: the part numbers that use it, the sheet's path and
# its circuits.
SHEETS_REPO = "cloud-sheets"
_sheet_index = {}


def sheets_roots(env=None, repo=None):
    env = os.environ if env is None else env
    repo = REPO if repo is None else repo
    roots = []
    if env.get("CLOUD_SHEETS"):
        roots.append(env["CLOUD_SHEETS"])
    roots.append(os.path.join(os.path.dirname(os.path.abspath(repo)), SHEETS_REPO))
    return roots


def _sheets_of(root):
    """(by part number, by sheet file stem) for the index.csv under `root`; empty when there is none."""
    if root not in _sheet_index:
        by_mpn, by_stem = {}, {}
        path = os.path.join(root, "index.csv")
        if os.path.isfile(path):
            with open(path, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    for m in (r.get("mpns") or "").split(";"):
                        if m.strip():
                            by_mpn.setdefault(m.strip(), r)
                    by_stem.setdefault(os.path.splitext(os.path.basename(r.get("sheet") or ""))[0], r)
        _sheet_index[root] = (by_mpn, by_stem)
    return _sheet_index[root]


def render_sheet(sheet):
    """Write a found sheet (find_sheet's result) as a page with cloud-sheets' own render.py; the page's path.

    The page is one self-contained HTML file beside the sheet's data file; writing it takes a moment and needs
    nothing but Python, so it is written afresh each time."""
    import subprocess
    script = sheet["render"].split(" ")[1]
    subprocess.run([sys.executable, script, sheet["path"]], check=True, stdout=subprocess.DEVNULL)
    return os.path.splitext(sheet["path"])[0] + ".html"


def find_sheet(mpn, datasheet_rel=None, env=None, repo=None):
    """The reference sheet of part `mpn` where the cloud-sheets repository is, or None.

    Found by the part number in the sheets' index, else by the datasheet's file name (a sheet is named after its
    datasheet). Returns the sheet's data file, the command that writes it as a page (HTML), its counts and its
    circuits."""
    stem = os.path.splitext(os.path.basename(datasheet_rel))[0] if datasheet_rel else None
    for root in sheets_roots(env, repo):
        by_mpn, by_stem = _sheets_of(root)
        r = by_mpn.get(mpn) or (by_stem.get(stem) if stem else None)
        if not r:
            continue
        sheet = os.path.abspath(os.path.join(root, r["sheet"]))
        if not os.path.isfile(sheet):
            continue
        num = lambda k: int(r[k]) if (r.get(k) or "").isdigit() else None
        return {"path": sheet,
                "render": "python3 %s %s" % (os.path.join(os.path.abspath(root), "render.py"), sheet),
                "tables": num("tables"), "curves": num("curves"),
                "circuits": (r.get("circuits") or "").split()}
    return None


# -- the price snapshot --------------------------------------------------------
# prices.csv: one row per priced part, mpn,kind,source,basis,quantity,
# unit_price,currency,date. A part with no price has no row.

_prices = None


def price_rows():
    """{mpn: price row} of prices.csv, read once: quantity an int,
    unit_price a float, the rest as written. {} when there is no file."""
    global _prices
    if _prices is None:
        _prices = {}
        if os.path.isfile(PRICES):
            with open(PRICES, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    r["quantity"] = int(r["quantity"])
                    r["unit_price"] = float(r["unit_price"])
                    _prices[r["mpn"]] = r
    return _prices


def price_of(mpn):
    """The part's price as --json gives it: {unit_price, currency, quantity,
    basis, source, date}, or None when the snapshot has no price for it."""
    r = price_rows().get(mpn)
    if not r:
        return None
    return {k: r[k] for k in ("unit_price", "currency", "quantity", "basis", "source", "date")}


def price_search(q):
    """The searchable `price`: the unit price when it is in USD, else None."""
    return q["unit_price"] if q and q["currency"] == PRICE_CURRENCY else None


def price_short(q):
    """A price in a few characters, for a find line: 0.0062USD@100,
    0.561USD@3000+ (no break at or below 100), 6.44USD@1 gone (the last
    one-piece price of a part JLCPCB no longer lists), ~0.0023USD@100 (a
    relative's price, basis family)."""
    if not q:
        return None
    s = "%g%s" % (q["unit_price"], q["currency"])
    if q["basis"] == "family":
        return "~%s@%d" % (s, PRICE_QUANTITY)
    if q["basis"] == "min_order_above_100":
        return "%s@%d+" % (s, q["quantity"])
    if q["basis"] == "one_piece_last_seen":
        return "%s@1 gone" % s
    return "%s@%d" % (s, PRICE_QUANTITY)


def price_text(q):
    """A price in a sentence, for `cloud-parts show`."""
    if not q:
        return None
    who = PRICE_SOURCES.get(q["source"], q["source"])
    s = "%g %s each" % (q["unit_price"], q["currency"])
    if q["basis"] == "at_100":
        how = "at %d pieces (%s's %d+ break), observed %s" % (PRICE_QUANTITY, who, q["quantity"], q["date"])
    elif q["basis"] == "family":
        how = ("at %d pieces, roughly: no seller quote for this exact part, so the price of a close relative "
               "(same series and size where there is one)" % PRICE_QUANTITY)
    elif q["basis"] == "min_order_above_100":
        how = ("at %d pieces: %s sells no fewer, its smallest break is above %d; observed %s"
               % (q["quantity"], who, PRICE_QUANTITY, q["date"]))
    else:
        how = ("for one piece, the last price %s listed: the part was found gone from its catalogue on %s"
               % (who, q["date"]))
    if q["currency"] != PRICE_CURRENCY:
        how += "; in %s, so the %s `price` column of find leaves it out" % (q["currency"], PRICE_CURRENCY)
    return "%s %s" % (s, how)


def price_reason(d):
    """Why the searchable `price` is empty for a part: what a may-fit line
    and `show` say."""
    q = d.get("price_quote")
    if q and q["currency"] != PRICE_CURRENCY:
        return "priced only in %s: %s" % (q["currency"], price_text(q))
    if d.get("lcsc"):
        return "no price in the %s snapshot: JLCPCB's catalogue gave none for %s" % (PRICE_SNAPSHOT, d["lcsc"])
    return ("no price in the %s snapshot: it has no LCSC number, and Mouser gave no price for this exact "
            "part number" % PRICE_SNAPSHOT)


# -- a package name from the KiCad footprint name ---------------------------

_FP_PREFIX = re.compile(r"^(?:R|C|L|D|LED|Fuse|Texas|CP)_")
_PKG_TOKEN = re.compile(r"\d{4}|SM[ABC]|(?:[A-Z]{2,}[A-Za-z]*|TO)-?\d+[A-Za-z0-9.-]*")


def package_of(footprint):
    """The package a KiCad footprint name starts with, in the words a
    package is usually called by: 'Resistor_SMD:R_0603_1608Metric' ->
    '0603', 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm' -> 'SOIC-8',
    'Diode_SMD:D_SMA' -> 'SMA', 'Package_DFN_QFN:QFN-28-1EP_5x5mm...' ->
    'QFN-28-EP'. When the name starts with no package word (a pin header,
    a JST connector, a relay, a resistor array) the whole footprint name
    is the package: known, and never another family's, so package = '0603'
    is false for it, as it is in the parts library. None only when the
    part names no footprint."""
    if not footprint:
        return None
    name = _FP_PREFIX.sub("", footprint.split(":")[-1])
    tok = name.split("_")[0]
    if not _PKG_TOKEN.fullmatch(tok):
        return name
    return re.sub(r"-1EP$", "-EP", tok)


# -- what a part is, in this library's own words -----------------------------

def describe(params):
    """A short description built from the part's own filed values: the first
    few of its kind's own columns as the table shows them ('50V, 220mA,
    3.5Ω@10V, 6Ω@4.5V'), a word value spelled out with its column where the
    value alone would not say ('channel n'). The common columns every kind
    has are left out. Nothing here is the manufacturer's text: it is this
    library's own words, which the word search and the `description` column
    read."""
    kind = params.get("kind")
    if kind not in PP.kinds():
        return None
    out = []
    for f, spec in PP.kinds()[kind]["fields"].items():
        if len(out) >= 4:
            break
        v = params.get(f)
        if v is None or v is False:
            continue
        if v is True:
            out.append(f.replace("_", " "))
            continue
        shown = PP.show(spec, v)
        out.append(shown if (spec["type"] not in ("enum", "enums", "text") or len(shown) > 2)
                   else "%s %s" % (f.replace("_", " "), shown))
    return ", ".join(out) or None


# -- the parts --------------------------------------------------------------

def folder_name(mpn):
    """A part's folder name as the parts library writes it: / and space as _."""
    return re.sub(r"[/ ]", "_", mpn)


def part_of(row, sel):
    """(folder, part.json dict) for one table row."""
    kind = row["kind"]
    params = {"kind": kind}
    for f, cell in (row.get("fields") or {}).items():
        if isinstance(cell, dict) and cell.get("value") is not None:
            params[f] = cell["value"]
    sel = sel or {}
    q = price_of(row["mpn"])
    fp = sel.get("footprint") or None
    sym = sel.get("kicad_symbol") or None
    d = {"mpn": row["mpn"], "manufacturer": sel.get("manufacturer") or None, "description": describe(params),
         "jlc_package": package_of(fp), "package": fp,
         "jlc": sel.get("jlc") or None, "lcsc": sel.get("lcsc") or None,
         "price": price_search(q), "price_quote": q,
         "specs": {"KiCad symbol": sym} if sym else {}, "params": params,
         "_table": {"fields": row.get("fields") or {}, "datasheet": sel.get("datasheet_path") or None,
                   "symbol": sym, "footprint": fp,
                   "series_id": row.get("series_id"), "unit": row.get("unit")}}
    return os.path.join(TABLE, kind, folder_name(row["mpn"])), d


_parts = None                                   # every kind, once something asks for all of them
_by_kind = {}                                   # kind -> its parts, each kind read at most once


def table_kinds():
    """Every kind table/ holds, named by its file, in the order the files
    are read."""
    return [os.path.basename(p)[:-len(".jsonl")]
            for p in sorted(glob.glob(os.path.join(TABLE, "*.jsonl")))]


def selection_rows(kinds):
    """{mpn: selection.csv row} for the named kinds, in one pass of the
    file. `kind` is its first column, so a line of another kind is passed
    over without being read as CSV at all: a search over one kind pays for
    that kind's rows and not for the twelve thousand others. Every line
    kept must hold the file's own columns, and if one does not -- a value
    written with a newline in it would do that -- the file is read whole,
    the plain way, and nothing is missed."""
    if not os.path.isfile(SELECTION):
        return {}
    want = set(kinds)
    starts = tuple(name + "," for name in want)
    with open(SELECTION, encoding="utf-8", newline="") as f:
        head = next(csv.reader(f), None)
        if not head:
            return {}
        rows = list(csv.reader(line for line in f if line.startswith(starts))) \
            if head[0] == "kind" else list(csv.reader(f))
    if any(len(row) != len(head) for row in rows):
        with open(SELECTION, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))[1:]
    k, m = head.index("kind"), head.index("mpn")
    return {row[m]: dict(zip(head, row)) for row in rows if row and row[k] in want}


def read_kinds(kinds):
    """Read the table file of each named kind not read yet, with that
    kind's selection rows, and keep it."""
    want = [k for k in kinds if k not in _by_kind]
    if not want:
        return
    sel = selection_rows(want)
    for kind in want:
        out = []
        path = os.path.join(TABLE, kind + ".jsonl")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        out.append(part_of(row, sel.get(row["mpn"])))
        out.sort(key=lambda p: os.path.basename(p[0]))
        _by_kind[kind] = out


def parts(kinds=None):
    """[(folder, part.json dict)] of every part in table/, or of the named
    kinds alone. Each kind is read once and kept. A parametric search names
    its kinds, so it opens those kinds' table files and reads those kinds'
    selection rows and nothing else; a word search, which reads what is
    written about every part, asks for them all."""
    global _parts
    if kinds is not None:
        kinds = list(kinds)
        read_kinds(kinds)
        if len(kinds) == 1:
            return _by_kind[kinds[0]]
        out = [p for k in kinds for p in _by_kind[k]]
        out.sort(key=lambda p: os.path.basename(p[0]))
        return out
    if _parts is None:
        ks = table_kinds()
        read_kinds(ks)
        out = [p for k in ks for p in _by_kind[k]]
        out.sort(key=lambda p: os.path.basename(p[0]))
        _parts = out
    return _parts


def why(d, field):
    """A field's cell: {value, page, condition} or {null_reason}. The
    page is the datasheet's own, in the separate cloud-datasheets repository;
    the datasheet's text is not kept here."""
    if field == "price":                        # not a datasheet value: the price snapshot's
        if d.get("price") is not None:
            return {"value": d["price"], "page": None, "condition": price_text(d.get("price_quote"))}
        return {"value": None, "null_reason": price_reason(d)}
    cell = (d.get("_table") or {}).get("fields", {}).get(field)
    if not isinstance(cell, dict):
        return None
    return {k: cell[k] for k in ("value", "page", "condition", "null_reason") if k in cell}
