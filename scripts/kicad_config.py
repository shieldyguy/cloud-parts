"""Make a KiCad install on this machine see cloud-parts' libraries (called by install.sh).

    python3 scripts/kicad_config.py ROOT

Looks for KiCad's user config folders ($KICAD_CONFIG_HOME, then ~/Library/Preferences/kicad/<ver>
on macOS, ${XDG_CONFIG_HOME:-~/.config}/kicad/<ver> on Linux). In each one found:
  - kicad_common.json: sets the path variable CLOUD_PARTS to ROOT (only when the file exists);
  - sym-lib-table and fp-lib-table: adds an entry for each cloud-parts library whose name is not
    there yet; a name already there (KiCad's own library of that name, usually) is left as it is,
    and counted. A table that does not exist is written with the cloud-parts entries only.
Nothing found: prints the two table paths to add by hand, and the variable to set. Run again,
it changes nothing. Standard library only."""
import glob
import json
import os
import re
import sys


def config_dirs():
    home = os.path.expanduser("~")
    roots = []
    if os.environ.get("KICAD_CONFIG_HOME"):
        return [os.environ["KICAD_CONFIG_HOME"]] if os.path.isdir(os.environ["KICAD_CONFIG_HOME"]) else []
    roots.append(os.path.join(home, "Library", "Preferences", "kicad"))
    roots.append(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "kicad"))
    out = []
    for r in roots:
        for d in sorted(glob.glob(os.path.join(r, "[0-9]*"))):
            if os.path.isdir(d) and re.fullmatch(r"\d+\.\d+", os.path.basename(d)):
                out.append(d)
    return out


def merge_table(path, ours, head):
    """Add our (lib ...) lines whose names are absent. -> (added, kept_theirs)."""
    entries = re.findall(r'^\s*(\(lib \(name "([^"]+)"\).*\))\s*$', open(ours).read(), re.M)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("(%s\n\t(version 7)\n%s)\n" % (head, "".join("\t%s\n" % e for e, _n in entries)))
        return len(entries), 0
    text = open(path).read()
    have = {}
    for l in text.splitlines():
        m = re.search(r'\(name "([^"]+)"\)', l)
        if m:
            have[m.group(1)] = "${CLOUD_PARTS}" in l
    add = [e for e, n in entries if n not in have]
    kept = sum(1 for _e, n in entries if n in have and not have[n])
    if add:
        i = text.rstrip().rfind(")")
        text = text[:i].rstrip("\n") + "\n" + "".join("\t%s\n" % e for e in add) + ")\n"
        with open(path, "w") as f:
            f.write(text)
    return len(add), kept


def main(argv):
    root = os.path.abspath(argv[0]) if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sym, fp = os.path.join(root, "kicad", "sym-lib-table"), os.path.join(root, "kicad", "fp-lib-table")
    dirs = config_dirs()
    if not dirs:
        print("  KiCad: no config folder found, nothing changed. To use the libraries in KiCad, set the")
        print("  path variable CLOUD_PARTS=%s and add the entries of" % root)
        print("    %s\n    %s" % (sym, fp))
        print("  to KiCad's global (or a project's) symbol and footprint library tables.")
        return 0
    for d in dirs:
        a1, k1 = merge_table(os.path.join(d, "sym-lib-table"), sym, "sym_lib_table")
        a2, k2 = merge_table(os.path.join(d, "fp-lib-table"), fp, "fp_lib_table")
        common = os.path.join(d, "kicad_common.json")
        var = "not set (no kicad_common.json yet: start KiCad once, then run install.sh again)"
        if os.path.exists(common):
            try:
                cfg = json.load(open(common))
                env = cfg.get("environment") or {}
                cfg["environment"] = env
                vars_ = env.get("vars") or {}
                env["vars"] = vars_
                if vars_.get("CLOUD_PARTS") != root:
                    vars_["CLOUD_PARTS"] = root
                    with open(common, "w") as f:
                        json.dump(cfg, f, indent=2)
                var = "CLOUD_PARTS set"
            except (ValueError, KeyError, TypeError) as e:
                var = "not set (kicad_common.json unreadable: %s)" % e
        print("  KiCad %s: symbol libraries +%d (%d names kept as they were), footprint libraries +%d (%d kept); %s"
              % (os.path.basename(d), a1, k1, a2, k2, var))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
