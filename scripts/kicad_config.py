"""Make a KiCad on this machine see cloud-parts' libraries (called by install.sh).

    python3 scripts/kicad_config.py ROOT [DIR]

With no DIR: every settings folder of KiCad 10 or later, <base>/<version>, where <base> is
$KICAD_CONFIG_HOME, else ~/Library/Preferences/kicad (macOS), ${XDG_CONFIG_HOME:-~/.config}/kicad
(Linux), ~/.var/app/org.kicad.KiCad/config/kicad (flatpak) and %APPDATA%/kicad (Windows). An older
KiCad's folder is left alone: the library files are in KiCad 10 format.
With DIR: that folder only, as it is. A folder holding a .kicad_pro is a project; any other is a
settings folder.
In each folder:
  - sym-lib-table and fp-lib-table: adds an entry for each cloud-parts library whose name is not
    there yet; a name already there (KiCad's own library of that name, usually) is left as it is,
    and counted. A table that does not exist is written with the cloud-parts entries only.
  - kicad_common.json, in a settings folder: sets the path variable CLOUD_PARTS to ROOT (written
    with that alone when there is none). A project has no such file: there KiCad takes CLOUD_PARTS
    from its environment (. env.sh).
Nothing found: prints what to do by hand. Run again, it changes nothing. Standard library only."""
import glob
import json
import os
import re
import sys


def config_dirs():
    """-> (settings folders of KiCad 10 or later, older ones)."""
    home = os.path.expanduser("~")
    if os.environ.get("KICAD_CONFIG_HOME"):
        bases = [os.environ["KICAD_CONFIG_HOME"]]
    else:
        bases = [os.path.join(home, "Library", "Preferences", "kicad"),
                 os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "kicad"),
                 os.path.join(home, ".var", "app", "org.kicad.KiCad", "config", "kicad")]
        if os.environ.get("APPDATA"):
            bases.append(os.path.join(os.environ["APPDATA"], "kicad"))
    new, old = [], []
    for b in bases:
        for d in sorted(glob.glob(os.path.join(b, "[0-9]*"))):
            m = re.fullmatch(r"(\d+)\.(\d+)", os.path.basename(d))
            if m and os.path.isdir(d) and os.path.realpath(d) not in [os.path.realpath(x) for x in new + old]:
                (new if int(m.group(1)) >= 10 else old).append(d)
    return new, old


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


def set_variable(d, root):
    """CLOUD_PARTS in a settings folder's kicad_common.json. -> what to say."""
    common = os.path.join(d, "kicad_common.json")
    try:
        cfg = json.load(open(common)) if os.path.exists(common) else {}
        env = cfg.get("environment") or {}
        cfg["environment"] = env
        vars_ = env.get("vars") or {}
        env["vars"] = vars_
        if vars_.get("CLOUD_PARTS") != root:
            vars_["CLOUD_PARTS"] = root
            with open(common, "w") as f:
                json.dump(cfg, f, indent=2)
        return "CLOUD_PARTS set"
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return "CLOUD_PARTS not set (kicad_common.json unreadable: %s)" % e


def main(argv):
    root = os.path.abspath(argv[0]) if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sym, fp = os.path.join(root, "kicad", "sym-lib-table"), os.path.join(root, "kicad", "fp-lib-table")
    if len(argv) > 1:
        if not os.path.isdir(argv[1]):
            print("  KiCad: %s is not a folder, nothing changed" % argv[1])
            return 1
        dirs, old = [os.path.abspath(argv[1])], []
    else:
        dirs, old = config_dirs()
    for d in old:
        print("  KiCad %s: left alone (the library files are in KiCad 10 format)  %s" % (os.path.basename(d), d))
    if not dirs:
        print("  KiCad: no settings folder of KiCad 10 or later found, nothing changed. KiCad 10 or later")
        print("  reads the library files by their paths as they are. For its library lists:")
        print("  ./install.sh --kicad DIR (a settings folder, or a project folder), or by hand set the path variable")
        print("  CLOUD_PARTS=%s and add the entries of" % root)
        print("    %s\n    %s" % (sym, fp))
        print("  to KiCad's symbol and footprint library tables.")
        return 0
    for d in dirs:
        project = bool(glob.glob(os.path.join(d, "*.kicad_pro")))
        a1, k1 = merge_table(os.path.join(d, "sym-lib-table"), sym, "sym_lib_table")
        a2, k2 = merge_table(os.path.join(d, "fp-lib-table"), fp, "fp_lib_table")
        var = "KiCad takes CLOUD_PARTS from its environment (. env.sh)" if project else set_variable(d, root)
        print("  %s: symbol libraries +%d (%d names kept as they were), footprint libraries +%d (%d kept); %s  %s"
              % ("project" if project else "KiCad " + os.path.basename(d), a1, k1, a2, k2, var, d))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
