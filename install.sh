#!/bin/sh
# cloud-parts install: offline, nothing downloaded, nothing installed with pip.
#   ./install.sh                  write env.sh, add the libraries to each KiCad (10 or later) found here,
#                                 write the sheets' pages, self-check
#   ./install.sh --kicad DIR      as the first, but add the libraries to DIR only: a KiCad settings
#                                 folder somewhere else, or a project folder
#   ./install.sh --no-kicad       as the first, but leave every KiCad alone
#   ./install.sh --check          the self-check table only
# KiCad is not needed: the search, the sheets and the library files work without it.
# The reference sheets are a separate repository, cloud-sheets: place it beside this folder (or set
# CLOUD_SHEETS) before installing. install.sh writes each sheet as an HTML page beside its data file,
# and cloud-sheets/index.html, which lists them (a few seconds; run again after the sheets change).
# Then: . ./env.sh   (puts `cloud-parts` on PATH and sets CLOUD_PARTS). Safe to run again.
set -u
HERE=$(pwd)
cd "$(dirname "$0")" || exit 1
ROOT=$(pwd -P)

CHECK_ONLY=0; KICAD=1; KDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --no-kicad) KICAD=0 ;;
    --kicad) [ $# -gt 1 ] || { echo "install.sh: --kicad needs a folder"; exit 2; }
             KDIR=$(cd "$HERE" && cd "$2" 2>/dev/null && pwd -P) || { echo "install.sh: --kicad: $2 is not a folder"; exit 2; }
             shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1 (--kicad DIR, --no-kicad, --check)"; exit 2 ;;
  esac
  shift
done

PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
  echo "install.sh: python3 not found. cloud-parts needs Python 3.8 or later (standard library only)."
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "install.sh: $("$PY" --version 2>&1) is too old; cloud-parts needs Python 3.8 or later."
  exit 1
fi

if [ "$CHECK_ONLY" = 1 ]; then
  exec "$PY" "$ROOT/scripts/check_install.py"
fi

chmod +x "$ROOT/bin/cloud-parts" 2>/dev/null
SH=""
if [ -n "${CLOUD_SHEETS:-}" ] && [ -f "$CLOUD_SHEETS/render.py" ]; then
  SH=$(cd "$CLOUD_SHEETS" && pwd -P)
elif [ -f "$ROOT/../cloud-sheets/render.py" ]; then
  SH=$(cd "$ROOT/../cloud-sheets" && pwd -P)
fi
cat > "$ROOT/env.sh" <<EOF
# cloud-parts: source this file (. $ROOT/env.sh)
export CLOUD_PARTS="$ROOT"
case ":\$PATH:" in
  *":$ROOT/bin:"*) ;;
  *) export PATH="$ROOT/bin:\$PATH" ;;
esac
EOF
if [ -n "$SH" ]; then
  echo "export CLOUD_SHEETS=\"$SH\"" >> "$ROOT/env.sh"
fi
echo "cloud-parts at $ROOT"
echo "  wrote env.sh (CLOUD_PARTS, and bin/ on PATH)"

if [ "$KICAD" = 1 ]; then
  if [ -n "$KDIR" ]; then
    "$PY" "$ROOT/scripts/kicad_config.py" "$ROOT" "$KDIR"
  else
    "$PY" "$ROOT/scripts/kicad_config.py" "$ROOT"
  fi || echo "  KiCad: nothing changed (see above)"
fi

if [ -n "$SH" ]; then
  if "$PY" "$SH/render.py" all > /dev/null 2>&1; then
    echo "  sheets: each one written as a page (HTML) beside its data file; $SH/index.html lists them"
  else
    echo "  sheets: the pages could not be written in $SH (try: python3 $SH/render.py all)"
  fi
else
  echo "  sheets: place the cloud-sheets repository beside this folder and run install.sh again"
fi

echo
CLOUD_SHEETS="$SH" "$PY" "$ROOT/scripts/check_install.py"
status=$?
echo
if [ $status -eq 0 ]; then
  echo "done. Next: . $ROOT/env.sh   then: cloud-parts find --help, cloud-parts show MPN"
else
  echo "install.sh: the self-check failed (reasons above)."
fi
exit $status
