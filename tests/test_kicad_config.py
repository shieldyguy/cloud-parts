"""scripts/kicad_config.py: which folders it changes, and what it writes there. Temp folders only."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "kicad_config.py")


def run(tmp_path, *dirs, base=None):
    env = dict(os.environ, HOME=str(tmp_path / "home"), KICAD_CONFIG_HOME=str(base or tmp_path / "none"))
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("APPDATA", None)
    return subprocess.run([sys.executable, SCRIPT, ROOT] + [str(d) for d in dirs],
                          env=env, capture_output=True, text=True).stdout


def libs(path):
    return open(path).read().count("(lib ")


def test_settings_folders_of_kicad_10_or_later_and_no_older(tmp_path):
    base = tmp_path / "kicad"
    for v in ("9.0", "10.0", "10.99"):
        (base / v).mkdir(parents=True)
    (base / "10.0" / "sym-lib-table").write_text(
        '(sym_lib_table\n\t(version 7)\n\t(lib (name "Device")(type "KiCad")(uri "/own/Device.kicad_sym")(options "")(descr ""))\n)\n')
    (base / "10.0" / "kicad_common.json").write_text('{"environment": {"vars": {"X": "1"}}, "other": 2}')
    out = run(tmp_path, base=base)
    assert "KiCad 9.0: left alone" in out and not os.listdir(base / "9.0")
    ours = libs(os.path.join(ROOT, "kicad", "sym-lib-table"))
    text = (base / "10.0" / "sym-lib-table").read_text()
    assert '(uri "/own/Device.kicad_sym")' in text and text.count('(name "Device")') == 1   # theirs kept
    assert libs(base / "10.0" / "sym-lib-table") == ours and libs(base / "10.99" / "sym-lib-table") == ours
    assert "(1 names kept as they were)" in out
    cfg = json.loads((base / "10.0" / "kicad_common.json").read_text())
    assert cfg["environment"]["vars"] == {"X": "1", "CLOUD_PARTS": ROOT} and cfg["other"] == 2
    assert json.loads((base / "10.99" / "kicad_common.json").read_text()) == {"environment": {"vars": {"CLOUD_PARTS": ROOT}}}
    again = run(tmp_path, base=base)
    assert again.count("symbol libraries +0") == 2 and text == (base / "10.0" / "sym-lib-table").read_text()


def test_a_named_folder_is_a_project_or_a_settings_folder(tmp_path):
    proj, cfg = tmp_path / "proj", tmp_path / "elsewhere"
    proj.mkdir(); cfg.mkdir()
    (proj / "board.kicad_pro").write_text("{}")
    out = run(tmp_path, proj)
    assert out.startswith("  project:") and "from its environment" in out
    assert libs(proj / "sym-lib-table") and libs(proj / "fp-lib-table") and not (proj / "kicad_common.json").exists()
    out = run(tmp_path, cfg)
    assert "CLOUD_PARTS set" in out and json.loads((cfg / "kicad_common.json").read_text())["environment"]["vars"]["CLOUD_PARTS"] == ROOT


def test_nothing_found_says_what_to_do(tmp_path):
    out = run(tmp_path)
    assert "nothing changed" in out and "--kicad DIR" in out and os.path.join(ROOT, "kicad", "sym-lib-table") in out
