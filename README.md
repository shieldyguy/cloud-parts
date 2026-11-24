# cloud-parts

12,077 electronic parts for offline use: each part's datasheet values with
their page numbers, its KiCad symbol, footprint and 3D model, a price, and a
search tool.

## Setup

    ./install.sh        # offline, Python 3.8+ only; writes env.sh, registers the KiCad libraries, checks itself
    . ./env.sh          # sets CLOUD_PARTS, puts bin/ on PATH

`./install.sh --help` lists its options.

## Use

    cloud-parts find ldo
    cloud-parts find mosfet "channel = 'p' and rds_on < 60m" --why
    cloud-parts show LM317DCYR
    cloud-parts sheet LM317DCYR
    cloud-parts --help

`cloud-parts find --help [KIND]` is the search syntax and a kind's columns.
`find` and `show` take `--json`.

## Layout

- `table/<kind>.jsonl` one part per line; each field is `{value, page, condition}` or `{value: null, null_reason}`
- `schema/parts_params.json` the kinds, their fields and units
- `selection.csv` one row per part: kind, KiCad symbol and footprint, datasheet path, sha256, pages, revision, and JLCPCB's assembly class and LCSC number as of 15 November 2026
- `prices.csv` USD per piece when buying 100, with the day each was read
- `kicad/` symbols, footprints, 3D models and library tables (KiCad 10 or later)
- `bin/cloud-parts`, `lib/` the search tool and its Python module
- `scripts/`, `tests/` the install check and the tests (pytest)

## Sibling repositories (optional)

Placed beside this folder, or named by the variable:

- `cloud-datasheets` (`$CLOUD_DATASHEETS`) the datasheet PDFs the page numbers refer to
- `cloud-sheets` (`$CLOUD_SHEETS`) each datasheet as a data file with drawn circuits

## Licence

Code Apache-2.0 (`LICENSE`), data CC0-1.0 (`LICENSES/CC0-1.0.txt`), `kicad/`
under KiCad's library licence (`LICENSES/KiCad-libraries.md`).
