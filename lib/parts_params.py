"""A part's parameters, and searching them.

A part's `params` holds one kind and that kind's fields, typed and in
fixed units, as schema/parts_params.json says (the schema: the one list of
kinds, fields, units and what each means). A value is one the schema allows.
Nothing here reads distributor text into numbers: the schema is the one
vocabulary, with no parser translating vendor strings.

`cloud-parts find` reads `find()`: a word search over everything written about
a part, or `KIND "CONDITION"`, a SQL condition over a table of the kind
built on each call, where a value not filed is unknown and a part whose
condition is unknown is kept as "may fit", never dropped. `cloud-parts find
--help [KIND]` prints `help()`.
"""
import functools
import json
import os
import re
import sqlite3

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parts_params.json")
_schema = None
_kind_aliases = None                            # read from the schema with it; see _aliases_of


class QueryError(Exception):
    """A search that cannot be read: an unknown field, a value that is not
    the field's unit, a range the wrong way round."""


def schema():
    global _schema, _kind_aliases
    if _schema is None:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            _schema = json.load(f)
        _kind_aliases = None
    return _schema


def kinds():
    return schema()["kinds"]


def fields_of(kind):
    """{field: spec} a kind may carry: the common fields, then its own."""
    out = {k: v for k, v in schema()["common"].items() if k != "kind"}
    out.update(kinds()[kind]["fields"])
    return out


def resolve_field(kind, name):
    """The kind's field a typed name means (its aliases, then its fields),
    or None."""
    name = name.lower()
    k = kinds()[kind]
    f = k.get("aliases", {}).get(name, name)
    return f if f in fields_of(kind) else None


def kind_words(word):
    """The kinds a search word names: a kind, a kind alias, or either with a
    trailing s. [] when it names none."""
    return kind_word_terms(word)[0]


def kind_word_terms(word):
    """(kinds, {field: value}) a search word means: `schottky` is the diode
    kind and type:schottky. ([], {}) when it names no kind."""
    w = word.lower()
    ka = schema().get("kind_aliases", {})
    for cand in (w, w[:-1] if w.endswith("s") else None, w[:-2] if w.endswith("es") else None):
        if not cand:
            continue
        if cand in kinds():
            return [cand], {}
        if cand in ka:
            a = ka[cand]
            return (list(a["kinds"]), dict(a.get("terms") or {})) if isinstance(a, dict) else (list(a), {})
    return [], {}


# -- validating what is filed -------------------------------------------

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(params):
    """Every way params breaks the schema, as sentences; [] when it is good."""
    errs = []
    if not isinstance(params, dict):
        return ["params must be an object"]
    kind = params.get("kind")
    if kind not in kinds():
        return ["kind %r is not one of the kinds (cloud-parts find --help lists them)" % (kind,)]
    allowed = fields_of(kind)
    for name, val in params.items():
        if name == "kind":
            continue
        spec = allowed.get(name)
        if spec is None:
            errs.append("%s has no field %r; its fields: %s" % (kind, name, ", ".join(allowed)))
            continue
        e = _check(spec, val)
        if e:
            errs.append("%s: %s" % (name, e))
    return errs


def _check(spec, val):
    t = spec["type"]
    if val is None:
        return "leave a field out rather than null"
    if t == "num":
        return None if _is_num(val) else "a number in %s" % spec.get("unit", "")
    if t == "int":
        return None if isinstance(val, int) and not isinstance(val, bool) else "a whole number"
    if t == "bool":
        return None if isinstance(val, bool) else "true or false"
    if t == "text":
        return None if isinstance(val, str) and val.strip() else "short text"
    if t == "enum":
        return None if val in spec["values"] else "one of %s" % ", ".join(spec["values"])
    if t == "enums":
        if not isinstance(val, list) or not val:
            return "a list of %s" % ", ".join(spec["values"])
        bad = [v for v in val if v not in spec["values"]]
        return ("%s not among %s" % (bad, ", ".join(spec["values"]))) if bad else None
    if t == "range":
        if not (isinstance(val, list) and len(val) == 2 and all(v is None or _is_num(v) for v in val)):
            return "[min, max] in %s, null for an end not stated" % spec.get("unit", "")
        if val[0] is None and val[1] is None:
            return "a range with neither end: leave it out"
        if val[0] is not None and val[1] is not None and val[0] > val[1]:
            return "min above max"
        return None
    if t == "cond":
        at = spec["at"]
        if not isinstance(val, list) or not val:
            return 'a list of {"value": n, "%s": n}' % at
        for e in val:
            if not isinstance(e, dict) or not _is_num(e.get("value")) or set(e) - {"value", at} \
                    or not (e.get(at) is None or _is_num(e.get(at))):
                return 'each entry {"value": number in %s, "%s": number in %s or null}' % (spec["unit"], at, spec["at_unit"])
        return None
    return "unknown type %s" % t


# -- the tables a search runs over ---------------------------------------
#
# `cloud-parts find KIND "CONDITION"` builds one SQLite table for the kind from
# every part.json, on each call (about 0.1 s), and keeps the parts for which
# the condition is not false. SQL's own three-valued logic does the rest: a
# value not filed is NULL, a comparison with NULL is unknown, and a part
# whose condition is unknown is kept as "may fit", never dropped
# (excluding a part for a value it lacks would hide parts that may fit). Text is always searchable
# beside the params: `text` holds everything written about the part,
# for a value that lives only in words.

META = (("part", "TEXT", "the part number as a name (/ and space as _)"),
        ("mpn", "TEXT", "maker's part number"),
        ("manufacturer", "TEXT", "the manufacturer's name"),
        ("package", "TEXT", "package name read from the KiCad footprint name. package = 'SOIC-8' matches the "
                            "family: SOP-8 and SO-8 too; SOT-23 matches SOT-23-3 and SOT-23-3L; SMA matches DO-214AC"),
        ("footprint", "TEXT", "the KiCad footprint, Library:Name"),
        ("jlc", "TEXT", "whether JLCPCB's assembly service offers the part: 'basic', 'preferred' or "
                        "'extended', and empty when it offers none. A frozen snapshot of 15 November 2026, "
                        "not live data: jlc = 'basic' needs no loading fee, "
                        "jlc != '' is any JLC part"),
        ("lcsc", "TEXT", "the LCSC catalogue number (C-number) of that JLCPCB listing, empty when "
                         "there is none"),
        ("price", "REAL", "USD per piece when buying 100: a frozen price snapshot (prices.csv gives each "
                          "day), JLCPCB's for a part with an LCSC number, Mouser's for the rest. Where "
                          "the seller's smallest break is above 100 it is that break's price, and a part "
                          "JLCPCB no longer lists has its last one-piece price; find prints which. A part "
                          "no seller quoted has a close relative's price (~). Roughly right, not exact. "
                          "price < 0.01, --sort price"),
        ("description", "TEXT", "a short description of the part, built from its own filed values"),
        ("text", "TEXT", "everything above, the KiCad symbol and the filed values, in one string: "
                         "text like '%logic level%' finds a word that is written nowhere else"))
META_NAMES = [m[0] for m in META]
META_UNITS = {"price": "USD"}                   # the META columns that hold a number, and its unit
ALLOWED_FUNCTIONS = {"abs", "min", "max", "round", "lower", "upper", "length", "like", "glob", "instr",
                     "substr", "trim", "replace", "has", "pkgeq", "wordis"}
_BAN = re.compile(r"\b(is|null|case|isnull|notnull|exists|select|from|join|coalesce|ifnull|iif|nullif|union|"
                  r"with|values|pragma|attach|over)\b", re.I)
_QUOTED = re.compile(r"('(?:[^']|'')*')")
_OPS = r"(?:<=|>=|==|=|!=|<>|<|>)"


class _Table:
    pass


def columns(kind):
    """[(column, field, role, spec)] of a kind's own columns, after META.
    A range field is two columns, FIELD_min and FIELD_max; a field rated
    at a condition is FIELD (the value) and FIELD_<at> (what it was
    measured at, e.g. rds_on_vgs: shown, never searched), one row per
    rating."""
    out = []
    for f, spec in fields_of(kind).items():
        t = spec["type"]
        if t == "range":
            out += [(f + "_min", f, "min", spec), (f + "_max", f, "max", spec)]
        elif t == "cond":
            out += [(f, f, "value", spec), ("%s_%s" % (f, spec["at"]), f, "at", spec)]
        else:
            out.append((f, f, "value", spec))
    return out


def _sql_type(spec):
    t = spec["type"]
    if t in ("text", "enum", "enums"):
        return "TEXT COLLATE NOCASE"
    return "INTEGER" if t == "bool" else "REAL"


def _term_matches(p, kind, field, want):
    """Does filed params p carry the implied term field=want ('a,b': any of)?"""
    f = resolve_field(kind, field)
    have = p.get(f)
    if have is None:
        return None
    opts = [w.strip().lower() for w in str(want).split(",")]
    if isinstance(have, bool):
        return have == (opts[0] in ("yes", "true", "1"))
    if isinstance(have, list):
        return any(str(h).lower() in opts for h in have)
    return str(have).lower() in opts


# kind words that name a sort of the kind, not the kind: never written into
# a part's text, or `shunt` would find every resistor and `encoder` every switch
SUBTYPE_WORDS = {"shunt", "encoder", "button", "reset", "timer", "gate", "oscillator", "jack", "terminal",
                 "esd", "bridge", "module", "transceiver", "mux", "soc", "lcd", "oled", "trimmer", "choke",
                 "amplifier", "rectifier", "converter", "dc-dc"}


def _aliases_of(kind):
    """[(alias, implied terms)] for the kind words that may name a part of
    this kind. The schema's kind_aliases do not change, so they are read
    through once and not once per part: a kind of 6,000 ladder parts would
    otherwise walk all seventy of them 6,000 times."""
    global _kind_aliases
    if _kind_aliases is None:
        out = {}
        for a in schema().get("kind_aliases", {}):
            ks, terms = kind_word_terms(a)
            if not terms and a in SUBTYPE_WORDS:
                continue
            for k in ks:
                out.setdefault(k, []).append((a, terms))
        _kind_aliases = out
    return _kind_aliases.get(kind, ())


def kind_names(kind, p):
    """The kind words that name this part: its kind, each plain alias that
    is another name for it (cap, fet, xtal), and each alias whose implied
    terms it carries (a schottky diode is also `schottky`)."""
    out = [kind]
    for a, terms in _aliases_of(kind):
        if all(_term_matches(p, kind, f, w) for f, w in terms.items()):
            out.append(a)
    return out


# -- packages: one family, many names --------------------------------------

_PKG_SAME = {"SO8": "SOIC8", "SOP8": "SOIC8", "SOIC8150MIL": "SOIC8",
             "SO14": "SOIC14", "SOP14": "SOIC14", "SO16": "SOIC16", "SOP16": "SOIC16",
             "SOT233": "SOT23", "SOT233L": "SOT23", "TO236": "SOT23", "SOT23L": "SOT23",
             "SOT25": "SOT235", "SOT255": "SOT235", "SOT235L": "SOT235", "SC745": "SOT235",
             "SOT26": "SOT236", "SOT236L": "SOT236", "SOT893": "SOT89", "SOT893L": "SOT89",
             "SOT2233": "SOT223", "SOT2234": "SOT223", "SC706": "SOT363", "SC88": "SOT363",
             "SC705": "SOT353", "SC88A": "SOT353", "SC703": "SOT323", "SC70": "SOT323",
             "DO214AC": "SMA", "DO214AA": "SMB", "DO214AB": "SMC", "TO2522": "TO252", "DPAK": "TO252",
             "D2PAK": "TO263", "TO2632": "TO263", "SOD123F": "SOD123FL", "MINISMA": "SMA",
             "VSSOP8": "MSOP8", "VSSOP10": "MSOP10"}


def pkg_names(name):
    """Every name of a package's families, as text a word search reads:
    SOP-8 also reads as SOIC8, SO8 and SOP8."""
    keys = pkg_keys(name)
    return sorted(keys | {a for a, k in _PKG_SAME.items() if k in keys})


def pkg_keys(name):
    """The family keys a package name belongs to: 'SOP-8' -> {'SOIC8'},
    'SMA(DO-214AC)' -> {'SMA'}, 'SOT-23-3L' -> {'SOT23'}. A name in
    brackets counts as another name for the same package."""
    if not name:
        return set()
    out = set()
    pieces = re.split(r"[()]", str(name))
    for i, piece in enumerate(pieces):
        k = re.sub(r"[\s\-_.,]", "", piece.upper())
        if i and not re.search(r"[A-Z]{2}", re.sub(r"\d+(\.\d+)?X\d+(\.\d+)?|MM|MIL|EP", "", k)):
            continue                                   # a bracketed size (3x3), mm, mil: not a name
        k = re.sub(r"(\d)(150|175)MIL$", r"\1", k)    # the standard body widths; 208 and 300 mil differ
        if k.startswith("TSOT"):
            k = k[1:]
        if k:
            out.add(_PKG_SAME.get(k, k))
    return out


def _pkgeq(have, want):
    if have is None or want is None:
        return None
    return 1 if pkg_keys(have) & pkg_keys(want) else 0


# -- what a word search reads -------------------------------------------------

def _lower(s):
    s = str(s).lower()
    for a, b in (("µ", "u"), ("μ", "u"), ("ω", "ohm"), ("ohms", "ohm"), ("×", "x"), ("±", " "), ("℃", "c"), ("°", ""),
                 ("+/-", " ")):
        s = s.replace(a, b)
    return re.sub(r"(\d)\.(\d*?)0+(?![\d])", lambda m: m.group(1) + ("." + m.group(2) if m.group(2) else ""), s)


_TOKEN_SPLIT = r"[^a-z0-9.%²]+"


def fold(s):
    """A search word as it is compared, split as text is (tokens) and run
    together: case, hyphens, spaces, punctuation gone, µ as u, Ω as ohm,
    ° dropped, 2.0 as 2. 'Right-Angle' and 'right angle' fold alike; so
    do '10µF' and '10uf', and '(sot-23)' and 'sot23'."""
    return "".join(t.strip(".") or t for t in re.split(_TOKEN_SPLIT, _lower(s)) if t.strip("."))


def tokens(s):
    """(joined, starts): the text's words run together, and where each word
    starts. A search word matches when it begins at a word start, and may
    run on across words: 'rightangle' matches 'Right Angle', '10k' matches
    '10kΩ' but not '110k', 'adc' does not match 'load capacitance'."""
    toks = [t.strip(".") for t in re.split(_TOKEN_SPLIT, _lower(s)) if t.strip(".")]
    joined, starts, walls, n = [], set(), [], 0
    for t in toks:
        if joined and joined[-1][-1].isdigit() and t[0].isdigit():
            walls.append(n)                             # 12 | 100: never read as one number
        starts.add(n)
        joined.append(t)
        n += len(t)
    return "".join(joined), starts, walls


def holds(tok, word):
    """Does the text hold the word, starting at a word start and running on
    across words, but never across two numbers side by side?"""
    joined, starts, walls = tok
    i = joined.find(word)
    while i != -1:
        if i in starts and not any(i < w < i + len(word) for w in walls):
            return True
        i = joined.find(word, i + 1)
    return False


def _forms(x, unit):
    """A filed number written the ways an engineer might type it: 100nF
    0.1uF 100n; 4.7kΩ 4k7; 3.3V 3V3."""
    out = set()
    if not _is_num(x) or x == 0 or unit in NO_PREFIX:
        return out
    for scale, p in _SHOW:
        v = x / scale
        if 0.0999 <= abs(v) < 1000:
            n = "%g" % round(v, 4)
            out |= {n + p + (unit or ""), n + p}
            m = re.fullmatch(r"(\d+)\.(\d+)", n)
            if m:
                out.add(m.group(1) + (p or ("R" if unit == "Ω" else (unit or ""))) + m.group(2))
            elif re.fullmatch(r"\d+", n) and unit == "Ω":
                out |= {n + (p or "R"), n + (p or "R") + "0"}           # 22R, 1K0
    return out


def text_of(d):
    """Everything written about a part, as one string `text` and the word
    search read: part number, manufacturer, description, package and its other
    names, footprint, each spec line as 'Key: value' (here the KiCad
    symbol), the kind and its words, each filed value as a person reads it
    and as it is often typed, the LCSC catalogue number where the part has
    one, and AEC-Q for an automotive part."""
    bits = [d.get(k) for k in ("mpn", "manufacturer", "description", "jlc_package", "package", "value", "lcsc")]
    bits += pkg_names(d.get("jlc_package"))
    specs = d.get("specs") or {}
    if isinstance(specs, dict):
        bits += ["%s: %s" % (k, v) for k, v in specs.items() if v not in (None, "", "-")]
    p = d.get("params") or {}
    k = p.get("kind")
    if k in kinds():
        bits += kind_names(k, p)
        fs = fields_of(k)
        for f, v in p.items():
            if f not in fs:
                continue
            if fs[f]["type"] == "bool":                 # a yes-no field is written only when yes
                if v is True:
                    bits.append(f.replace("_", " "))
                continue
            bits.append("%s: %s" % (f, show(fs[f], v)))
            if fs[f]["type"] == "num":
                bits += sorted(_forms(v, fs[f].get("unit")))
        if p.get("automotive") is True:
            bits.append("AEC-Q100 AEC-Q101 AEC-Q200 automotive")
    return _text_form(" | ".join(str(b) for b in bits if b not in (None, "")))


def _text_form(s):
    """The `text` column's form, and a `text like` pattern's: µ as u, Ω as
    ohm, hyphens as spaces, one space at a time, so '%right angle%' finds
    Right-Angle and '%10uF%' finds 10µF."""
    s = str(s)
    for a, b in (("µ", "u"), ("μ", "u"), ("Ω", "ohm"), ("ω", "ohm"), ("℃", "°C"), ("‐", "-"), ("–", "-")):
        s = s.replace(a, b)
    return re.sub(r" {2,}", " ", s.replace("-", " "))


def _rows(folder, d, kind, cols, text=True):
    p = d.get("params") or {}
    meta = [os.path.basename(folder), d.get("mpn"), d.get("manufacturer"),
            d.get("jlc_package"), d.get("package"), d.get("jlc") or "", d.get("lcsc") or "",
            d.get("price"), d.get("description"), text_of(d) if text else None]
    base, conds = {}, {}
    if meta[4] in ("-", ""):
        meta[4] = None                                  # JLC's '-' is no package name
    for col, f, role, spec in cols:
        v = p.get(f)
        if f == "also" and v is None:
            v = []                                      # no other topology filed: it has none
        if v is None:
            base[col] = None
        elif role in ("min", "max"):
            e = v[0] if role == "min" else v[1]
            base[col] = None if e is None else _clean(e)
        elif spec["type"] == "cond":
            conds.setdefault(f, (spec, v))
        elif spec["type"] == "enums":
            base[col] = "," + ",".join(v) + "," if v else ","
        elif spec["type"] == "bool":
            base[col] = 1 if v else 0
        else:
            base[col] = _clean(v) if _is_num(v) else v
    rows = [dict(base)]
    for f, (spec, ratings) in conds.items():          # one row per rating; a part fits when any row does
        at = "%s_%s" % (f, spec["at"])
        rows = [dict(r, **{f: _clean(e["value"]), at: None if e.get(spec["at"]) is None else _clean(e[spec["at"]])})
                for r in rows for e in ratings]
    return [meta + [r.get(c) for c, _f, _r, _s in cols] for r in rows]


WORD_SAME = {"np0": "c0g", "liion": "li_ion", "lipo": "li_po"}


def _word(val):
    """A word as the schema writes it: lower case, - and space as _, and
    the other names (NP0 is C0G)."""
    v = re.sub(r"[\s\-]+", "_", str(val).strip().lower())
    return WORD_SAME.get(v.replace("_", ""), v)


def _word_fits(have, want):
    """A filed word fits an asked one when it is it, or when the schema's
    word_implies says it answers it too (in_out answers out, canfd answers
    can). Nothing else: usb_pd is not usb, reverse_log is not log, ab is
    not a."""
    have = str(have).lower()
    return have == want or want in schema().get("word_implies", {}).get(have, ())


def _has(col, val):
    """Does a list column hold a word that fits (_word_fits)?"""
    if col is None or val is None:
        return None
    v = _word(val)
    return 1 if any(_word_fits(w, v) for w in str(col).lower().strip(",").split(",")) else 0


def _wordis(col, val):
    if col is None or val is None:
        return None
    return 1 if _word_fits(col, _word(val)) else 0


def build(kind, parts, text=True):
    """A Table: an in-memory SQLite table `t` of every part of the kind.
    `text=False` leaves the `text` column empty. That column is everything
    written about a part, written out again for each one, and it costs more
    to fill than all the other columns together; only a condition that names
    `text` reads it, and find() fills it only for those."""
    cols = columns(kind)
    db = sqlite3.connect(":memory:")
    db.create_function("has", 2, _has, deterministic=True)
    db.create_function("pkgeq", 2, _pkgeq, deterministic=True)
    db.create_function("wordis", 2, _wordis, deterministic=True)
    db.execute("CREATE TABLE t (%s)" % ", ".join(
        ['"%s" %s' % (c, ty + (" COLLATE NOCASE" if ty == "TEXT" else "")) for c, ty, _d in META] + ['"%s" %s' % (c, _sql_type(s)) for c, _f, _r, s in cols]))
    tb = _Table()
    tb.kind, tb.db, tb.cols, tb.by_part, tb.refs = kind, db, cols, {}, []
    mine = [(folder, d) for folder, d in parts if (d.get("params") or {}).get("kind") == kind]
    for folder, d in mine:
        tb.by_part[os.path.basename(folder)] = (folder, d)
    db.executemany("INSERT INTO t VALUES (%s)" % ",".join("?" * (len(META) + len(cols))),
                   (r for folder, d in mine for r in _rows(folder, d, kind, cols, text)))
    tb.names = META_NAMES + [c for c, _f, _r, _s in cols]
    tb.spec = {c: s for c, _f, _r, s in cols}
    tb.role = {c: r for c, _f, r, _s in cols}
    tb.field = {c: f for c, f, _r, _s in cols}
    return tb


def _col_unit(tb, col):
    """The unit a column's numbers are in, or None for a text column."""
    if col in META_NAMES:
        return META_UNITS.get(col)
    s = tb.spec[col]
    if s["type"] in ("text", "enum", "enums"):
        return None
    if tb.role[col] == "at":
        return s.get("at_unit") or ""
    return s.get("unit") or ""


# -- reading a condition ---------------------------------------------------

PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
          "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_EXP = {"p": -12, "n": -9, "u": -6, "µ": -6, "μ": -6, "m": -3, "k": 3, "K": 3, "M": 6, "G": 9}
NO_PREFIX = {"USD", "%", "ppm", "ppm/°C", "°C", "mm", "in", "gf", "mcd", "nm", "AWG", "h", "dB", "J", "K", "A²s"}
# what may follow a number for each column unit, lower-cased (the unit's own name is always allowed)
SYNONYMS = {"Ω": ("ω", "ohm", "ohms", "r"), "°C": ("°c", "c", "degc", "℃"), "%": ("%", "pct"),
            "ppm/°C": ("ppm", "ppm/°c", "ppm/c", "ppm/k", "ppm/℃"), "V/√Hz": ("v/√hz", "v/rthz", "v/sqrthz"),
            "bps": ("bps", "b/s", "bit/s"), "sps": ("sps", "s/s")}
_ANY_UNIT = {"ohm", "ohms", "r", "c", "℃", "degc", "°c", "v", "a", "w", "f", "h", "hz", "s", "%", "ppm", "mm",
             "db", "b", "bit", "bps", "sps", "lm", "mcd", "cd", "nm", "j", "ah", "wh", "ω"}
_LIT = re.compile(r"(?<![\w.])(\d+\.?\d*|\.\d+)((?:[eE][-+]?\d+)?)([A-Za-zΩµμ°℃%][A-Za-zΩµμ°℃%/√0-9]*)?")


def _clean(x):
    """A float at 12 significant digits, so 100n and a filed 1e-07 are equal."""
    return float("%.12g" % x)


def _times(num, exp):
    return _clean(float("%se%d" % (num, exp)) if "e" not in num.lower() else float(num) * 10 ** exp)


def _units():
    out = set(_ANY_UNIT)
    for k in kinds():
        for s in fields_of(k).values():
            for u in (s.get("unit"), s.get("at_unit")):
                if u:
                    out.add(u.lower())
    return out


def literal(num, suffix, unit=None):
    """A number with a prefix, in the column's own unit: ('100', 'n') ->
    1e-07, ('15', 'm') -> 0.015, ('4', 'k7') -> 4700, ('4', 'R7') -> 4.7,
    ('16', 'M') -> 1.6e7, ('64', 'Ki') -> 65536, ('1', '%') -> 1. A unit
    after the number (100nF, 16MHz, 3.3V) is an error, never a guess:
    which of mF and MHz a letter means, or whether K is kilo or kelvin,
    is exactly where a guess goes wrong."""
    x = float(num)
    if not suffix or suffix == "%":
        return x
    s = suffix
    m = re.fullmatch(r"([RrKkMmunpµμG])(\d+)", s)                   # RKM: 4R7 4k7 2u2 1M5
    if m and x == int(x):
        letter = m.group(1)
        return _times("%d.%s" % (int(x), m.group(2)), 0 if letter in "Rr" else _EXP.get(letter, _EXP.get(letter.lower())))
    if s in PREFIX:
        return _times(num, _EXP[s])
    if s.lower() == "meg":
        return _times(num, 6)
    if s in ("Ki", "Mi", "Gi"):
        return _clean(x * 1024 ** ("KMG".index(s[0]) + 1))
    m = re.fullmatch(r"V/([munµμ])s", s)                             # slew: 1V/us
    if m:
        return _times(num, -_EXP[m.group(1)])
    raise QueryError("%s%s: a number takes a prefix only, in the column's unit (cloud-parts find --help KIND): "
                     "100n for 100nF, 16M for 16MHz, 3.3 for 3.3V, 4k7, 1meg, 64Ki (1024s), 1V/us" % (num, suffix))


def _column_beside(s, start, end, tb):
    """The column a literal at s[start:end] is compared with, if any:
    `col OP lit`, `lit OP col`, `col between lit and lit`, `col in (lit, ...)`."""
    before, after = s[:start], s[end:]
    for pat in (r"\b([A-Za-z_]\w*)\s*%s\s*$" % _OPS,
                r"\b([A-Za-z_]\w*)\s+(?:not\s+)?between\s*$",
                r"\b([A-Za-z_]\w*)\s+(?:not\s+)?between\s+\S+\s+and\s*$",
                r"\b([A-Za-z_]\w*)\s+(?:not\s+)?in\s*\([^()]*$"):
        m = re.search(pat, before, re.I)
        if m and m.group(1).lower() in tb.lower:
            return tb.lower[m.group(1).lower()]
    m = re.match(r"\s*%s\s*([A-Za-z_]\w*)\b" % _OPS, after)
    if m and m.group(1).lower() in tb.lower:
        return tb.lower[m.group(1).lower()]
    return None


def _typed(cond, tb):
    """Numbers and quoted words checked against the columns they meet:
    a unit on a number must be its column's; a number never meets a text
    column (package = 0603 is a mistake: '0603'); a quoted word never
    meets a number column; yes/no/true/false on a yes-no column are 1/0.
    Every number becomes a plain float, so 1/4 is 0.25."""
    pieces = _QUOTED.split(cond)
    flat = "".join(p if i % 2 == 0 else "'" + "x" * (len(p) - 2) + "'" for i, p in enumerate(pieces))
    pos = 0
    out = []
    for i, p in enumerate(pieces):
        if i % 2:                                       # a quoted word
            word = p[1:-1].replace("''", "'")
            if word.lower() in tb.lower:
                raise QueryError("'%s' is quoted, so it is text, not the column: write %s unquoted" % (word, word))
            if re.search(r"%s\s*[-+.\d]" % _OPS, flat[pos + len(p):pos + len(p) + 12]) or \
                    re.search(r"[\d.]\s*%s\s*$" % _OPS, flat[max(0, pos - 12):pos]):
                raise QueryError("%s compares quoted text with a number; a column goes unquoted, text in quotes" % p)
            col = _column_beside(flat, pos, pos + len(p), tb)
            if col is not None:
                unit = _col_unit(tb, col)
                word = p[1:-1].replace("''", "'")
                if col in tb.spec and tb.spec[col]["type"] == "bool":
                    if word.lower() in ("yes", "true", "1", "y"):
                        p = "1"
                    elif word.lower() in ("no", "false", "0", "n"):
                        p = "0"
                    else:
                        raise QueryError("%s is yes or no: %s = 1, or just %s" % (col, col, col))
                elif unit is not None:
                    raise QueryError("%s is a number in %s: write it unquoted, e.g. %s >= 100n"
                                     % (col, unit or "plain numbers", col))
            out.append(p)
        else:
            def one(m, base=pos):
                col = _column_beside(flat, base + m.start(), base + m.end(), tb)
                if col is not None and _col_unit(tb, col) is None:
                    raise QueryError("%s is text: quote it, e.g. %s = '%s'" % (col, col, m.group(0)))
                unit, suf = (_col_unit(tb, col) if col else None), m.group(3) or ""
                if unit in ("B", "bit") and suf in ("k", "K", "M", "G"):
                    raise QueryError("%s%s on %s: memory counts in 1024s; write %s%si (or the plain number)"
                                     % (m.group(1), suf, col, m.group(1), suf.upper()))
                if unit in ("Hz", "B", "bit") and suf == "m":
                    raise QueryError("%sm on %s is milli; for mega write %sM" % (m.group(1), col, m.group(1)))
                return repr(literal(m.group(1) + m.group(2), m.group(3) or "") if m.group(3)
                            else float(m.group(1) + m.group(2)))
            out.append(_LIT.sub(one, p))
        pos += len(pieces[i])
    return "".join(out)


def _unquoted(cond):
    return _QUOTED.sub("''", cond)


def _refuse(cond):
    bare = _unquoted(cond)
    if re.search(r"(?<![A-Za-z_\d])\d+_\d", bare):
        raise QueryError("write numbers without _ (10000, or 10k)")
    if '"' in bare:
        raise QueryError("text goes in single quotes: package = '0603', text like '%audio%'")
    for bad in (";", "--", "/*"):
        if bad in bare:
            raise QueryError("%r has no place in a condition" % bad)
    m = _BAN.search(bare)
    if m:
        raise QueryError("%r is not allowed. A value not filed is unknown, and a part whose condition "
                         "is unknown is kept and marked 'may fit' by the tool itself; write only what "
                         "you want, e.g. rds_on < 15m and vds >= 30" % m.group(1))
    if re.search(r"\bin\s*\(\s*\)", bare, re.I):
        raise QueryError("in () with nothing in it")


def _words_check(cond, tb):
    """Quoted words compared with a word column are checked against its
    words; a list column (color, peripherals) is read through has(), and
    `col = 'x'` / `col in (...)` are rewritten to it; package = 'x' is a
    package-family match."""
    def words_of(col):
        s = tb.spec.get(col)
        return [v.lower() for v in s["values"]] if s and s["type"] in ("enum", "enums") else None

    def check(col, val):
        ws = words_of(col)
        if ws is None:
            return val
        v = _word(val)
        if v not in ws:
            raise QueryError("%s is never %r; its words: %s" % (col, val, ", ".join(tb.spec[col]["values"])))
        return v

    def eq(m):
        col, op, val = m.group(1), m.group(2), m.group(3)
        col = tb.lower[col.lower()]
        if col == "package":
            return ("pkgeq(package, '%s')" if op in ("=", "==") else "not pkgeq(package, '%s')") % val
        val = check(col, val)
        neg = op not in ("=", "==")
        if tb.spec[col]["type"] == "enums":
            return ("not " if neg else "") + "has(%s, '%s')" % (col, val)
        also = " or has(also, '%s')" % val if col == "topology" and "also" in tb.spec else ""
        return ("not " if neg else "") + "(wordis(%s, '%s')%s)" % (col, val, also)

    def inlist(m):
        col, neg, body = tb.lower[m.group(1).lower()], m.group(2), m.group(3)
        vals = re.findall(r"'((?:[^']|'')*)'", body)
        if re.sub(r"'(?:[^']|'')*'|[\s,]", "", body):
            raise QueryError("%s in (...): quote every item: %s in ('a', 'b')" % (col, col))
        if col == "package":
            return "%s(%s)" % ("not " if neg else "", " or ".join("pkgeq(package, '%s')" % v for v in vals))
        vals = [check(col, v) for v in vals]
        fn = "has" if tb.spec[col]["type"] == "enums" else "wordis"
        also = col == "topology" and "also" in tb.spec
        return "%s(%s)" % ("not " if neg else "", " or ".join(
            "%s(%s, '%s')" % (fn, col, v) + (" or has(also, '%s')" % v if also else "") for v in vals))

    wordcols = [c for c in tb.spec if words_of(c) is not None] + ["package"]
    alt = "|".join(re.escape(c) for c in sorted(wordcols, key=len, reverse=True))
    cond = re.sub(r"(?<!['\w])(%s)\s*(==|=|!=|<>)\s*'((?:[^']|'')*)'" % alt, eq, cond, flags=re.I)
    cond = re.sub(r"(?<!['\w])(%s)\s+(not\s+)?in\s*\(([^)]*)\)" % alt, inlist, cond, flags=re.I)
    cond = re.sub(r"\bhas\(\s*(%s)\s*,\s*'((?:[^']|'')*)'\s*\)" % alt,
                  lambda m: "has(%s, '%s')" % (m.group(1), check(tb.lower[m.group(1).lower()], m.group(2))), cond, flags=re.I)
    bare = _unquoted(re.sub(r"\b(has|wordis)\(\s*\w+\s*,", r"\1(", cond))
    for c in tb.spec:
        if tb.spec[c]["type"] == "enums" and re.search(r"\b%s\b" % re.escape(c), bare, re.I):
            raise QueryError("%s is a list: has(%s, '%s'), or %s = '%s'" % (
                c, c, tb.spec[c]["values"][0], c, tb.spec[c]["values"][0]))
    for c, r in tb.role.items():
        cof = tb.spec[c].get("condition_of")
        if (r == "at" or cof) and re.search(r"\b%s\b" % re.escape(c), bare, re.I):
            f = cof or tb.field[c]
            raise QueryError("%s is the condition %s was rated at: shown with each rating, never searched. "
                             "%s < X fits a part when any of its ratings does" % (c, f, f))
    return cond


_SQL_WORDS = {"and", "or", "not", "in", "like", "glob", "between", "escape", "true", "false"} | ALLOWED_FUNCTIONS


def _aliases(cond, tb):
    """A column written by another of its names (rdson, vth_max, polarity)
    read as the column, outside quotes."""
    al = {a.lower(): f for a, f in (kinds()[tb.kind].get("aliases") or {}).items()}
    names = set(tb.lower)

    def one(m):
        w = m.group(0)
        low = w.lower()
        if low in names or low in _SQL_WORDS or m.string[m.end():].lstrip().startswith("("):
            return w
        for suffix in ("", "_min", "_max"):
            base = low[:len(low) - len(suffix)] if suffix else low
            if suffix and not low.endswith(suffix):
                continue
            if base in al and (al[base] + suffix) in names:
                return al[base] + suffix
        return w
    parts = _QUOTED.split(cond)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\b[A-Za-z_]\w*\b", one, parts[i])
    return "".join(parts)


def referenced(cond, tb):
    """The columns a condition names, in the order the table has them."""
    bare = _unquoted(cond).lower()
    return [c for c in tb.names if re.search(r"\b%s\b" % re.escape(c.lower()), bare)]


def _authorize(action, a1, a2, _db, _src):
    if action == sqlite3.SQLITE_FUNCTION and (a2 or "").lower() not in ALLOWED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _sql_error(exc, tb, cond):
    msg = str(exc)
    m = re.search(r"no such column: (\S+)", msg)
    if m:
        import difflib
        name = m.group(1).lower()
        for c, sp in tb.spec.items():
            if sp["type"] in ("enum", "enums") and name in [v.lower() for v in sp["values"]]:
                return QueryError("%s is a word of %s: quote it, %s = '%s'" % (name, c, c, name))
        al = {a.lower(): f for a, f in (kinds()[tb.kind].get("aliases") or {}).items()}
        f = al.get(name, name)
        if f + "_min" in tb.names:
            return QueryError("%s is a range: %s_min and %s_max (works at 5 V: %s_min <= 5 and %s_max >= 5)"
                              % (f, f, f, f, f))
        near = difflib.get_close_matches(name, tb.names, n=3, cutoff=0.5)
        return QueryError("%s has no column %r%s. Its columns: %s (cloud-parts find --help %s)" % (
            tb.kind, m.group(1), "; did you mean %s?" % " or ".join(near) if near else "",
            ", ".join(c for c in tb.names if tb.role.get(c) != "at"), tb.kind))
    if "not authorized" in msg or "no such function" in msg:
        return QueryError("%r: only these functions: %s" % (cond, ", ".join(sorted(ALLOWED_FUNCTIONS - {"pkgeq"}))))
    if "misuse of aggregate" in msg:
        return QueryError("%r: min() and max() take two or more values here, e.g. min(a, b)" % cond)
    if "syntax error" in msg:
        return QueryError("%r: %s. Numbers sit against their prefix (48M, not 48 MHz); text goes in "
                          "single quotes; cloud-parts find --help has examples" % (cond, msg))
    return QueryError("%r: %s" % (cond, msg))


def prepare(cond, tb):
    """The agent's condition as SQL the table can run. QueryError when it
    cannot be read."""
    cond = (cond or "").strip() or "1"
    _refuse(cond)
    tb.lower = {n.lower(): n for n in tb.names}
    cond = re.sub(r"\b(text\s+(?:not\s+)?(?:like|glob)\s+)'((?:[^']|'')*)'",
                  lambda m: m.group(1) + "'" + _text_form(m.group(2)) + "'", cond, flags=re.I)
    cond = _typed(_words_check(_aliases(cond, tb), tb), tb)
    tb.db.set_authorizer(_authorize)
    try:
        tb.db.execute("SELECT (%s) FROM t LIMIT 0" % cond)
    except sqlite3.Error as exc:
        raise _sql_error(exc, tb, cond)
    return cond


# -- a search --------------------------------------------------------------

def _plainly_condition(text):
    """Text that can only be meant as a condition: an operator, a quote,
    like/glob/between, or has(."""
    return bool(re.search(r"[=<>!']|\b(like|glob|between)\b|\b(has|in)\s*\(", text, re.I))


def split_query(words):
    """(kinds, implied condition, condition) when the search is KIND
    "CONDITION"; None when it is words. It is a condition when it has an
    operator or a quote, or when every word in it is a yes-no column of
    the kind or and/or/not (led "addressable"). Anything else after a
    kind word is words, searched over every part like grep: `cloud-parts find
    logic level mosfet` is three words, never the logic kind."""
    toks = []
    for i, w in enumerate(words):
        if i == 0 and " " in w.strip():
            first, rest = w.strip().split(None, 1)
            toks += [first, rest]
        else:
            toks.append(w)
    if not toks:
        return None
    ks, implied = kind_word_terms(toks[0])
    if not ks:
        return None
    rest = " ".join(toks[1:]).strip()
    if not rest:
        return None                                     # a kind word alone is a word: grep finds every one
    if not _plainly_condition(rest):
        toks = [w.lower() for w in re.findall(r"[^\s()]+", rest)]
        cols = {c for k in ks for c, _f, _r, _s in columns(k)} | set(META_NAMES)
        every_bool = set.intersection(*[{c for c, _f, _r, s in columns(k) if s["type"] == "bool"}
                                        for k in ks])
        sqlish = re.search(r";|\b(is|null|coalesce|ifnull|select)\b", rest, re.I) and any(t in cols for t in toks)
        only_bools = all(t in every_bool | {"and", "or", "not"} for t in toks) and any(t in every_bool for t in toks)
        if not (sqlish or only_bools):
            return None                                 # words: `fuse resettable 1206`, `tantalum case b`
    terms = []
    for f, v in implied.items():
        spec = fields_of(ks[0]).get(resolve_field(ks[0], f)) or {}
        if spec.get("type") == "bool":
            terms.append("%s = %d" % (f, 1 if str(v).lower() in ("yes", "true", "1") else 0))
        else:
            terms.append("%s in (%s)" % (f, ", ".join("'%s'" % x.strip() for x in str(v).split(","))))
    return ks, " and ".join(terms), rest


_TEXT_COL = re.compile(r"\btext\b", re.I)


def names_text(*written):
    """Could what was written name the `text` column? A plain word match
    over the condition and the sort as the agent wrote them, quotes and
    all: it says yes more readily than a query really needs, and never no
    when the query does need it. build() fills that column only then."""
    return any(_TEXT_COL.search(s or "") for s in written)


def query_kinds(words):
    """The kinds a search reads: those a parametric search names, or None
    for a word search, which reads what is written about every part.
    `cloud-parts find` asks this before it reads any part, so that a search
    over one kind opens one kind's table and no other."""
    q = split_query(words)
    return q[0] if q else None


def run(tb, cond, sort=None, desc=False, lenient_sort=False):
    """[(folder, d, fits, sort key, rows, unknown columns)], fits 'yes' or
    'maybe', for every part of the table whose condition is not false. A
    sort the kind cannot read (no such column) leaves every key unknown,
    never drops a part."""
    cond = prepare(cond, tb)
    key = "NULL"
    tb.sort_ok = False
    if sort:
        try:
            key = prepare(sort, tb)
            tb.sort_ok = True
        except QueryError as exc:
            exc.from_sort = True
            if not (lenient_sort and "has no column" in str(exc)):
                raise                                   # a misspelled sort is an error, never ignored
            key = "NULL"                                # another kind of the search has it
    agg = "MAX" if desc else "MIN"
    q = ("SELECT part, MAX((%s) IS TRUE), %s(CASE WHEN (%s) IS NOT FALSE THEN (%s) END) "
         "FROM t WHERE (%s) IS NOT FALSE GROUP BY part" % (cond, agg, cond, key, cond))
    try:
        found = tb.db.execute(q).fetchall()
    except sqlite3.Error as exc:
        raise _sql_error(exc, tb, cond)
    tb.refs = referenced(cond + " " + key, tb)
    refs = [c for c in tb.refs if c != "text"]
    out = []
    for part, yes, k in found:
        cur = tb.db.execute("SELECT * FROM t WHERE part = ?", (part,))
        rows = [dict(zip([x[0] for x in cur.description], r)) for r in cur.fetchall()]
        unknown = [] if yes else [c for c in refs if any(r.get(c) is None for r in rows)]
        folder, d = tb.by_part[part]
        out.append((folder, d, "yes" if yes else "maybe", k, rows, unknown))
    return out


JLC_RANK = {"basic": 0, "preferred": 1, "extended": 2}


def jlc_rank(d):
    """How early a part comes when nothing else separates two: a JLC basic
    part first (no loading fee), then preferred, then extended, then a part
    JLC's assembly does not offer. Only ever a tie-break: --sort still
    decides, and so does the condition."""
    return JLC_RANK.get((d or {}).get("jlc") or "", 3)


def order(results, desc=False):
    """Fits first, then may-fit; within each, by sort key (reversed with
    desc), unknown keys last, then JLC class (basic first), then part
    number."""
    def by_part(r):
        return (jlc_rank(r[2]), r[2].get("mpn") or "")
    def keyed(rs):
        known = [r for r in rs if r[4] is not None]
        unknown = [r for r in rs if r[4] is None]
        known.sort(key=by_part)
        known.sort(key=lambda r: (isinstance(r[4], str), r[4].lower() if isinstance(r[4], str) else r[4]), reverse=desc)
        unknown.sort(key=by_part)
        return known + unknown
    return keyed([r for r in results if r[3] == "yes"]) + keyed([r for r in results if r[3] != "yes"])


def find(parts, words, sort=None, desc=False):
    """A search over [(folder, part.json dict)]. KIND "CONDITION":
    {"mode": "kind", "kinds", "condition", "results": [(kind, folder, d,
    fits, key, rows, unknown)], "refs", "skipped": {kind: why}}.
    Otherwise words: {"mode": "words", "results": [(folder, d)],
    "counts": {word: parts holding it}}: every word, folded (case,
    hyphens, spaces, µ/u, Ω/ohm), somewhere in what is written about the
    part (text_of)."""
    q = split_query(words)
    if q is None:
        joined = " ".join(words)
        if re.search(r"[=<>]", joined):
            raise QueryError("a condition needs a kind first: cloud-parts find KIND \"CONDITION\" (cloud-parts find --help "
                             "lists the kinds)")
        if sort:
            raise QueryError("--sort needs a kind: cloud-parts find KIND \"CONDITION\" --sort COLUMN")
        def singular(x):
            ks, _t = kind_word_terms(x)
            low = x.lower()
            if ks and low not in kinds() and low not in schema().get("kind_aliases", {}):
                return low[:-2] if low.endswith("es") and kind_word_terms(low[:-2])[0] else low[:-1]
            return x
        ws = [fold(singular(x)) for w in words for x in w.split() if fold(x)]   # a quoted phrase is its words
        phrases = [fold(w) for w in words if len(w.split()) > 1]
        if not ws:
            raise QueryError("nothing to search for")
        texts = [(f, d, tokens(text_of(d)), fold(d.get("mpn") or "")) for f, d in parts]
        has = lambda t, num, w: holds(t, w) or (len(w) >= 4 and w in num)   # inside a part number: 1117, 7002
        hits = [(f, d, t, num) for f, d, t, num in texts if all(has(t, num, w) for w in ws)]
        hits.sort(key=lambda h: jlc_rank(h[1]))                         # a JLC basic part first
        hits.sort(key=lambda h: -sum(holds(h[2], p) for p in phrases))  # the phrase as written first
        return {"mode": "words", "results": [(f, d) for f, d, _t, _n in hits],
                "counts": {w: sum(1 for _f, _d, t, num in texts if has(t, num, w)) for w in ws}}
    ks, implied, rest = q
    cond = " and ".join(x for x in (implied, "(%s)" % rest if rest else "") if x) or "1"
    results, refs, skipped, first_err, sorted_by = [], [], {}, None, 0
    text = names_text(cond, sort)
    for k in ks:
        tb = build(k, parts, text)
        try:
            got = run(tb, cond, sort, desc, lenient_sort=len(ks) > 1)
        except QueryError as exc:
            if len(ks) == 1 or getattr(exc, "from_sort", False):
                raise
            skipped[k], first_err = str(exc), first_err or exc
            continue
        sorted_by += tb.sort_ok
        results += [(k,) + g for g in got]
        refs += [c for c in tb.refs if c not in refs]
    if len(skipped) == len(ks):
        raise first_err
    if sort and not sorted_by:
        raise QueryError("--sort %s: no column of %s by that name (cloud-parts find --help KIND)" % (sort, "/".join(ks)))
    return {"mode": "kind", "kinds": ks, "condition": cond, "results": order(results, desc), "refs": refs,
            "skipped": skipped}


def known(parts, kind, cols):
    """What a kind's parts hold in each column: {col: (n known, n parts,
    summary)}, the summary a span of numbers or the words seen. Printed
    when a search finds nothing, so the agent can see why."""
    tb = build(kind, parts, text=False)         # `text` has no summary: the loop below skips it
    n = len(tb.by_part)
    out = {}
    for c in cols:
        if c not in tb.names or c == "text":
            continue
        got = tb.db.execute('SELECT COUNT(DISTINCT part), MIN("%s"), MAX("%s") FROM t WHERE "%s" IS NOT NULL'
                            % (c, c, c)).fetchone()
        spec = tb.spec.get(c)
        if (spec and spec["type"] in ("enum", "enums", "text")) or (c in META_NAMES and c not in META_UNITS):
            vals = sorted({str(v).strip(",") for (v,) in tb.db.execute('SELECT DISTINCT "%s" FROM t WHERE "%s" IS NOT NULL' % (c, c))})
            summ = ", ".join(vals[:60]) + (" ..." if len(vals) > 60 else "")
        elif got[1] is None:
            summ = "nothing filed"
        else:
            u = (META_UNITS[c] if c in META_UNITS else
                 spec.get("at_unit") if tb.role.get(c) == "at" else spec.get("unit")) or ""
            summ = "%s .. %s" % (_eng(got[1], u), _eng(got[2], u))
        out[c] = (got[0], n, summ)
    return out


# JLC spec-table key words for fields whose names JLC does not use
HINT_KEYS = {"tempco": ("temperature coefficient", "tcr"), "cct": ("color temperature",),
             "vbr": ("breakdown",), "vrwm": ("reverse stand-off", "standoff", "working"),
             "iq": ("quiescent", "current - supply", "supply current"), "matrix": ("display",),
             "orientation": ("mounting type", "type", "orientation"), "gender": ("type", "gender"),
             "technology": ("type", "technology"), "q": ("q @", "q factor"), "vgs_th": ("threshold",),
             "rds_on": ("rds",), "vout": ("output voltage", "voltage - output"),
             "supply": ("voltage - supply", "supply voltage", "operating voltage", "dual supply", "single supply"),
             "temp": ("operating temp",), "vgs_max": ("gate source voltage", "vgss", "gate-source voltage"),
             "trip_current": ("trip current",), "current": ("current rating", "hold current", "rated current"),
             "ir_voltage": ("reverse leakage",), "vds": ("drain source voltage", "vdss"),
             "id": ("continuous drain current",), "capacitance": ("capacitance",), "voltage": ("voltage rating", "rated voltage"),
             "tolerance": ("tolerance",), "resistance": ("resistance",), "power": ("power",),
             "inductance": ("inductance",), "frequency": ("frequency",), "vf": ("forward voltage",), "if": ("forward current", "rectified"),
             "ir": ("reverse leakage",), "vr": ("reverse voltage", "dc reverse"), "ipp": ("peak pulse",),
             "clamp": ("clamping",), "wavelength": ("wavelength",), "intensity": ("luminous intensity",),
             "i2t": ("i²t", "i2t", "melting"), "trip_time": ("trip time", "time to trip"),
             "dropout": ("dropout",), "output_current": ("output current",), "qg": ("gate charge",),
             "ciss": ("input capacitance",), "esr": ("esr", "equivalent series"), "dcr": ("dc resistance",),
             "isat": ("saturation",), "gbw": ("gain bandwidth",), "slew": ("slew",), "vos": ("offset voltage",)}


def hints(d, cols, kind):
    """JLC spec lines that look like these columns' fields, best first: the
    value a filer missed is often sitting there in words. A yes-no field
    brings the Features line; package brings the footprint name."""
    specs = d.get("specs") or {}
    if not isinstance(specs, dict):
        specs = {}
    norm = lambda s: " " + re.sub(r"[^a-z0-9²]+", " ", s.lower()).strip() + " "
    scored = {}
    extra = []
    tb_cols = {c: (f, s) for c, f, _r, s in columns(kind)}
    for c in cols:
        if c == "package":
            extra.append("footprint: %s" % d.get("package"))
            continue
        f, s = tb_cols.get(c, (c, {}))
        phrases = list(HINT_KEYS.get(f, ()))
        generic = {"max", "min", "temp", "vgs", "current", "voltage", "supply", "time", "type", "count"}
        words = [] if phrases else [t for t in f.lower().split("_") if len(t) >= 3 and t not in generic]
        for k, v in specs.items():
            if v in (None, "", "-"):
                continue
            nk = norm(k)
            score = 0
            if any(norm(p).strip() in nk for p in phrases):
                score = 2
            elif any(" %s " % w in nk or nk.strip().startswith(w) for w in words if len(w) >= 3) \
                    or any("(%s" % w in k.lower() for w in words):
                score = 1
            if s.get("type") == "bool" and k.lower().startswith("feature"):
                score = max(score, 1)
            if score:
                scored[k] = max(scored.get(k, 0), score)
    best = sorted(scored, key=lambda k: -scored[k])
    return extra + ["%s: %s" % (k, specs[k]) for k in best]


def table_rows(parts, kind):
    """Every part of a kind with every filed value shown: --table."""
    fs = fields_of(kind)
    out = []
    for folder, d in parts:
        p = d.get("params") or {}
        if p.get("kind") != kind:
            continue
        out.append((kind, d.get("mpn") or os.path.basename(folder), d.get("jlc_package") or "",
                    "  ".join("%s=%s" % (f, show(fs[f], v)) for f, v in p.items() if f in fs)))
    return out


# -- telling an agent what there is -------------------------------------------

SYNTAX = """cloud-parts find WORDS...                 a word search, like grep
cloud-parts find KIND "CONDITION"          a parametric search over one kind
           [--sort EXPR [--desc]] [--json]
cloud-parts find --help [KIND]             the kinds; one kind's columns
cloud-parts find --table KIND              every part of a kind, every value

WORDS: each word must start a word somewhere in what is
  written about the part: part number, manufacturer, kind, description,
  package and its other names (SOP-8 is also SOIC-8), KiCad symbol and
  footprint, and the filed values, each also as often typed (100nF 0.1uF 100n; 4.7kΩ 4k7; 22Ω 22R). Case,
  hyphens and spaces do not matter, nor µ/u or Ω/ohm: right-angle =
  "right angle", low-noise = "low noise". A word matches at the start of
  a word, so 10k does not find 110k and 1uF does not find 0.1uF; inside
  a part number, any 4 or more characters match (1117 finds AMS1117). A kind word in a word search
  is only a word. A quoted phrase is its words, the parts holding the
  phrase as written listed first. When nothing holds every word, it says how many parts
  hold each one, so you can drop the one that sank it.
    cloud-parts find red led 0603          cloud-parts find ams1117
    cloud-parts find "right angle" usb     cloud-parts find aec-q101

KIND "CONDITION": the condition is a SQL expression over the kind's
  columns (cloud-parts find --help KIND lists them, with units). It must have
  an operator or a quote; KIND followed by plain words is a word search.
    cloud-parts find mosfet "channel = 'p' and rds_on < 15m"
    cloud-parts find opamp "channels = 4 and supply_max >= 12"
    cloud-parts find capacitor "capacitance = 100n and package = '0402' and voltage >= 25"
    cloud-parts find led "color = 'red' and package = '0603'"
    cloud-parts find connector "pitch = 2.54 and text like '%right angle%'"
    cloud-parts find mosfet "channel = 'n' and vgs_th_max <= 2" --sort rds_on
    cloud-parts find led "addressable"     (a yes-no column alone)
  Numbers are in the column's unit (rds_on in Ω, capacitance in F,
  frequency in Hz, flash in bytes) and take a prefix only: 15m 100n
  4.7k 4k7 4R7 16M 1meg, 64Ki (1024s), 1V/us, 1%. A unit written after
  a number (100nF, 16MHz, 3.3V) is an error, never a guess.
  Text in single quotes; = on text ignores case; like '%x%' is a substring.
  package = 'SOIC-8' matches the family (SOP-8, SO-8); SOT-23 matches
  SOT-23-3L; SMA matches DO-214AC. package like '%QFN%' is exact text.
  A range field is two columns, FIELD_min and FIELD_max (supply_min,
  temp_max). "Works at 3.3 V": supply_min <= 3.3 and supply_max >= 3.3.
  An adjustable regulator has vout_range, not vout: "3.3 V out" is
  vout = 3.3 or (vout_range_min <= 3.3 and vout_range_max >= 3.3).
  A field rated at a condition (rds_on at each vgs) fits when any of its
  ratings does: rds_on < 15m finds a FET rated 12m at 10 V, and each
  rating is shown with its condition, to choose from. The condition
  columns (rds_on_vgs, vf_if ...) are shown, never searched.
  A list field (color, peripherals, interface): color = 'red' or
  has(color, 'red'). A word asked finds the words that start with it or
  hold it as a part, in lists and one-word columns alike: can finds
  canfd, usb finds usb_host, rail_to_rail = 'out' finds in_out, channel
  = 'n' finds n_p. NP0 is C0G; li-ion is li_ion. topology also reads a
  regulator's `also` (a buck that can be wired as a boost).
  A misspelled word or column is an error, never an empty answer.
  The `text` column holds everything the word search reads, so a value
  that lives only in a name or a description is still found:
  "vgs_th_max <= 2.5 or text like '%logic level%'".
  A function word (logic level, audio, USB ESD, low noise) is often not
  written anywhere: say it in columns (logic level FET: vgs_th_max <= 2.5;
  USB ESD: esd "vrwm <= 6 and cj <= 5p"; audio op amp: noise, thd).
  Allowed: = != < <= > >= and or not between in like glob ( ) + - * /
  and abs min max round lower upper length instr substr trim replace has.

  NOTHING IS DROPPED FOR A VALUE NOT FILED. A part whose condition cannot
  be decided because a column it reads is empty is listed after the fits
  as "may fit", with the empty columns and the table's reason each is
  empty, so you can read the value in the datasheet yourself. Do not
  test for empty values (IS NULL, NOT NULL, COALESCE ... are refused):
  the tool does it. When nothing fits, it prints what the kind's parts
  hold in each column you used.

  --sort EXPR [--desc]: order by a column or expression (rds_on, qg);
  parts with no value for it come last.
  --json: {"mode", "kinds", "condition", "results": [{mpn, kind, manufacturer,
  package, footprint, symbol, datasheet, description, params, fits:
  yes|maybe, sort_key, values, not_stated: {field: reason}}],
  "skipped_kinds"}; tests/test_find_cli.py has every key.
"""


def _type_word(spec):
    t, u = spec["type"], spec.get("unit")
    if t == "cond":
        return "%s @ %s %s" % (u, spec["at"], spec["at_unit"])
    if t == "range":
        return "[min,max] %s" % u
    if t in ("enum", "enums"):
        return ("one of " if t == "enum" else "any of ") + "|".join(spec["values"])
    return (u or t) if t in ("num",) else t




def help(kind=None, parts=None):
    """The syntax and every kind (kind=None), or one kind's columns in full,
    with how many parts have each filed when `parts` is given. Given, and
    empty, is a kind with no parts filed yet, and says so (0 of 0)."""
    if kind:
        ks = kind_words(kind)
        if not ks:
            raise QueryError("no kind %r; the kinds: %s" % (kind, ", ".join(kinds())))
        out = []
        for k in ks:
            spec = kinds()[k]
            tb = build(k, parts or [], text=False)   # count(c) leaves `text` out
            n = len(tb.by_part)
            def count(c):
                if parts is None:
                    return ""
                got = tb.db.execute('SELECT COUNT(DISTINCT part) FROM t WHERE "%s" IS NOT NULL' % c).fetchone()[0]
                return "%d/%d" % (got, n)
            out.append("%s: %s%s" % (k, spec["desc"], "  (%d parts)" % n if parts is not None else ""))
            out.append("  %-20s %-9s %s" % ("column", "filed", "type -- meaning"))
            for c, _t, desc in META:
                out.append("  %-20s %-9s %s" % (c, count(c) if c != "text" else "", desc))
            for c, f, role, s in columns(k):
                if role == "at":
                    ty = "%s, the %s that %s was rated at: shown with each rating, never searched" % (s["at_unit"], s["at"], f)
                elif role in ("min", "max"):
                    ty = "%s, the %s of %s" % (s.get("unit") or "", role, f)
                else:
                    ty = _type_word(s)
                d = s.get("desc") if role in ("value", "min") else ""
                out.append("  %-20s %-9s %s%s" % (c, count(c), ty, ("  -- " + d) if d else ""))
            al = spec.get("aliases") or {}
            if al:
                out.append("  other names a condition may use: " +
                           ", ".join("%s=%s" % kv for kv in al.items()))
            out.append("")
        return "\n".join(out).rstrip() + "\n"
    out = [SYNTAX, "KINDS (cloud-parts find --help KIND for the columns)"]
    for k, spec in kinds().items():
        out.append("  %-20s %s" % (k, spec["desc"]))
    out.append("  every kind also: " + ", ".join(f for f in schema()["common"] if f != "kind"))
    out.append("\nKIND WORDS (any of these may start a search): " + ", ".join(
        "%s=%s" % (a, "|".join(kind_word_terms(a)[0]) + ("(%s)" % ",".join("%s=%s" % t for t in kind_word_terms(a)[1].items())
                                                       if kind_word_terms(a)[1] else ""))
        for a in schema().get("kind_aliases", {})))
    return "\n".join(out) + "\n"


# -- showing a value -----------------------------------------------------------

_SHOW = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f"))


@functools.lru_cache(maxsize=1 << 15)
def _eng(x, unit):
    """A number in the unit's own words. It is a pure reading of (x, unit)
    and a value ladder asks for the same few thousand over and over -- every
    0402 is 50V, every E96 decade the same ninety-six resistances -- so the
    answers are kept."""
    if unit == "V/s":
        return "%gV/us" % round(x / 1e6, 4)
    if unit in ("B", "bit") and x >= 1024:
        for scale, p in ((1024 ** 3, "G"), (1024 ** 2, "M"), (1024, "K")):
            if x >= scale:
                return "%g%s%s" % (round(x / scale, 3), p, unit)
    if unit in NO_PREFIX or unit in ("B", "bit") or x == 0:
        return "%g%s" % (x, unit)
    for scale, p in _SHOW:
        if abs(x) >= scale * 0.9999:
            return "%g%s%s" % (round(x / scale, 4), p, unit)
    return "%g%s" % (x, unit)


def show(spec, val):
    """A filed value as a person reads it: 1e-07 F -> '100nF',
    rds_on -> '9.5mΩ@10V, 14.7mΩ@4.5V', a range -> '2.7V~5.5V'."""
    t, u = spec["type"], spec.get("unit") or ""
    if t == "num":
        return _eng(val, u)
    if t == "range":
        return "%s~%s" % tuple("?" if v is None else _eng(v, u) for v in val)
    if t == "cond":
        return ", ".join(_eng(e["value"], u) + ("" if e.get(spec["at"]) is None else "@" + _eng(e[spec["at"]], spec["at_unit"]))
                         for e in val)
    if t == "bool":
        return "yes" if val else "no"
    if t == "enums":
        return ",".join(val)
    return str(val)
