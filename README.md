# cloud-parts

12,077 electronic parts for offline use: each part's datasheet values with
their page numbers, its KiCad symbol, footprint and 3D model, a price, and a
search tool.

## Setup

With `cloud-sheets` (the parts' datasheets) cloned beside this folder:

    ./install.sh        # offline, Python 3.8+ only; writes env.sh, adds the libraries to a KiCad found here, writes the sheets' pages, checks itself
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

`show` gives a part's datasheet twice: the sheet (JSON) and the same as a page
(HTML, one file that loads nothing else). `cloud-sheets/index.html` lists every
page. Each part of a page has an id, to link to or lift out:
`LM317DCYR.html#absolute-maximum-ratings`, `#circuit-adjustable`.

## KiCad

Any KiCad 10 or later; none is needed for the search or the sheets. `kicad/` is
a subset of KiCad's own libraries under the same library and part names, in
KiCad 10 file format (KiCad 9 cannot read it).

- By path, nothing to set up: `show` gives a part's symbol file and footprint
  file, and `kicad-cli` and scripts take those paths.
- A KiCad installed here: `./install.sh` adds the libraries to each settings
  folder of KiCad 10 or later it finds (macOS, Linux, flatpak, Windows, or
  under `$KICAD_CONFIG_HOME`) and sets its path variable `CLOUD_PARTS`. A
  library name already in a table stays as it is, so in a KiCad with its own
  libraries those names remain KiCad's. `--no-kicad` leaves every KiCad alone.
- A settings folder somewhere else (another build, a container):
  `./install.sh --kicad DIR`, DIR being the folder that holds that KiCad's
  `sym-lib-table`, such as `~/.config/kicad/10.0`.
- One project: `./install.sh --kicad PROJECT_DIR` adds the libraries to the
  project's own tables, which come before KiCad's, so the project uses this
  folder's files. KiCad then needs `CLOUD_PARTS`: set by either line above, or
  in its environment (`. ./env.sh`).
- By hand: set the path variable `CLOUD_PARTS` to this folder and add the
  entries of `kicad/sym-lib-table` and `kicad/fp-lib-table` to KiCad's tables.

## Layout

- `table/<kind>.jsonl` one part per line; each field is `{value, page, condition}` or `{value: null, null_reason}`
- `schema/parts_params.json` the kinds, their fields and units
- `selection.csv` one row per part: kind, KiCad symbol and footprint, datasheet path, sha256, pages, revision, and JLCPCB's assembly class and LCSC number as of 15 November 2026
- `prices.csv` USD per piece when buying 100, with the day each was read
- `kicad/` symbols, footprints, 3D models and library tables
- `bin/cloud-parts`, `lib/` the search tool and its Python module
- `scripts/`, `tests/` the install check and the tests (pytest)

## Sibling repository

- `cloud-sheets`, beside this folder or at `$CLOUD_SHEETS`: each datasheet as a data file and a page, with drawn circuits

## Licence

Code Apache-2.0 (`LICENSE`), data CC0-1.0 (`LICENSES/CC0-1.0.txt`), `kicad/`
under KiCad's library licence (`LICENSES/KiCad-libraries.md`).
