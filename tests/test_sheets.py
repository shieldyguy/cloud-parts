"""Reference sheets live in the separate cloud-sheets repository and are optional here. These tests use a
temporary folder and one tiny fake sheet, never the real set, so they pass with or without it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import cloud_parts  # noqa: E402

INDEX = ("mpns,manufacturer,kind,sheet,source_revision,source_pages,tables,curves,circuits\n"
         "FAKE-1;FAKE-2,Nobody,test_kind,datasheets/test_kind/FAKE-1.json,"
         "rev A,4,3,2,astable monostable\n")


def layout(tmp, where):
    """tmp/cloud-parts (the repo) and a fake sheet library under `where`: 'sibling', 'env' or None."""
    repo = os.path.join(tmp, "cloud-parts")
    os.makedirs(repo)
    env = {}
    if where:
        root = {"sibling": os.path.join(tmp, "cloud-sheets"), "env": os.path.join(tmp, "elsewhere")}[where]
        os.makedirs(os.path.join(root, "datasheets", "test_kind"))
        with open(os.path.join(root, "index.csv"), "w", encoding="utf-8") as f:
            f.write(INDEX)
        with open(os.path.join(root, "datasheets", "test_kind", "FAKE-1.json"), "w", encoding="utf-8") as f:
            json.dump({"part": "FAKE-1"}, f)
        if where == "env":
            env["CLOUD_SHEETS"] = root
    return repo, env


def test_found_beside_or_through_the_variable():
    for i, where in enumerate(("sibling", "env")):
        repo, env = layout(os.path.join(TMP[0], str(i)), where)
        s = cloud_parts.find_sheet("FAKE-2", env=env, repo=repo)
        assert list(s) == ["path", "render", "tables", "curves", "circuits"], where
        assert s["path"].endswith(os.path.join("test_kind", "FAKE-1.json")) and os.path.isabs(s["path"])
        assert (s["tables"], s["curves"]) == (3, 2)
        assert s["circuits"] == ["astable", "monostable"]


def test_the_page_is_written_on_retrieval():
    tmp = os.path.join(TMP[0], "render")
    repo, env = layout(tmp, "sibling")
    # a stand-in for cloud-sheets' render.py: writes the page beside the sheet
    with open(os.path.join(tmp, "cloud-sheets", "render.py"), "w", encoding="utf-8") as f:
        f.write("import sys\nopen(sys.argv[1][:-5] + '.html', 'w').write('<p>page</p>')\n")
    s = cloud_parts.find_sheet("FAKE-1", env=env, repo=repo)
    assert s["render"].endswith("FAKE-1.json") and os.path.join("cloud-sheets", "render.py") in s["render"]
    page = cloud_parts.render_sheet(s)
    assert page == s["path"][:-5] + ".html" and open(page).read() == "<p>page</p>"


def test_found_by_the_datasheet_name_when_the_index_lacks_the_part_number():
    repo, env = layout(os.path.join(TMP[0], "stem"), "sibling")
    assert cloud_parts.find_sheet("FAKE-9", "datasheets/test_kind/FAKE-1.pdf", env=env, repo=repo)
    assert cloud_parts.find_sheet("FAKE-9", "datasheets/test_kind/OTHER.pdf", env=env, repo=repo) is None


def test_absent_is_none_not_an_error():
    repo, env = layout(os.path.join(TMP[0], "none"), None)
    assert cloud_parts.find_sheet("FAKE-1", env=env, repo=repo) is None
    assert cloud_parts.sheets_roots(env={"CLOUD_SHEETS": "/x"}, repo=repo) == [
        "/x", os.path.join(TMP[0], "none", "cloud-sheets")]


def test_show_without_the_sheet_library_has_no_sheet_lines():
    env = dict(os.environ, CLOUD_SHEETS=os.path.join(TMP[0], "nowhere"))
    cli = os.path.join(REPO, "bin", "cloud-parts")
    if os.path.isdir(os.path.join(os.path.dirname(REPO), cloud_parts.SHEETS_REPO)):
        return                                   # the real library is beside this one: the sibling still answers
    out = subprocess.run([sys.executable, cli, "show", "LM317DCYR"], env=env, capture_output=True, text=True).stdout
    assert "  sheet " not in out
    shown = json.loads(subprocess.run([sys.executable, cli, "show", "LM317DCYR", "--json"], env=env,
                                      capture_output=True, text=True).stdout)
    assert shown["parts"][0]["sheet"] is None


TMP = [None]


def setup_module(module):
    TMP[0] = tempfile.mkdtemp(prefix="cloud-parts-test-")


def teardown_module(module):
    shutil.rmtree(TMP[0], ignore_errors=True)
