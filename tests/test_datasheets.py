"""The datasheet PDFs are not kept in this repository and are optional. These tests use a
temporary folder and one tiny fake PDF, never the real set, so they pass with or without it."""
import hashlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import cloud_parts  # noqa: E402
import check_install  # noqa: E402

PDF = b"%PDF-1.4\n% a fake datasheet for the tests\n%%EOF\n"
REL = "datasheets/test_kind/FAKE-PART.pdf"


def layout(tmp, where):
    """tmp/cloud-parts (the repo) and the fake PDF under `where`: 'sibling', 'env', 'inside' or None."""
    repo = os.path.join(tmp, "cloud-parts")
    os.makedirs(repo)
    env = {}
    if where:
        root = {"sibling": os.path.join(tmp, "cloud-datasheets"), "env": os.path.join(tmp, "elsewhere"),
                "inside": repo}[where]
        os.makedirs(os.path.join(root, os.path.dirname(REL)), exist_ok=True)
        with open(os.path.join(root, REL), "wb") as f:
            f.write(PDF)
        if where == "env":
            env["CLOUD_DATASHEETS"] = root
    rows = [{"datasheet_path": REL, "sha256": hashlib.sha256(PDF).hexdigest()}]
    return repo, env, rows


def status(tmp, where):
    repo, env, rows = layout(str(tmp), where)
    return check_install.datasheet_status(rows, lambda p: cloud_parts.find_datasheet(p, env=env, repo=repo))


def test_present_beside_through_the_variable_or_inside():
    for i, where in enumerate(("sibling", "env", "inside")):
        n, good, missing, changed = status(os.path.join(str(TMP[0]), str(i)), where)
        assert (n, len(good), missing, changed) == (1, 1, [], []), where


def test_absent_is_reported_not_raised():
    n, good, missing, changed = status(os.path.join(str(TMP[0]), "none"), None)
    assert (n, good, missing, changed) == (1, [], [REL], [])


def test_a_changed_file_is_caught():
    tmp = os.path.join(str(TMP[0]), "changed")
    repo, env, rows = layout(tmp, "sibling")
    with open(os.path.join(tmp, "cloud-datasheets", REL), "ab") as f:
        f.write(b"x")
    find = lambda p: cloud_parts.find_datasheet(p, env=env, repo=repo)  # noqa: E731
    assert check_install.datasheet_status(rows, find)[3] == [REL]


def test_lookup_order():
    tmp = os.path.join(str(TMP[0]), "order")
    repo = os.path.join(tmp, "cloud-parts")
    roots = cloud_parts.datasheet_roots(env={"CLOUD_DATASHEETS": "/x"}, repo=repo)
    assert roots == ["/x", os.path.join(tmp, "cloud-datasheets"), repo]


def test_show_names_the_pdf_only_where_it_is_installed():
    """With no PDFs, show and find print nothing about them; with one, show prints its path."""
    import subprocess
    cli = os.path.join(REPO, "bin", "cloud-parts")
    run = lambda env, *a: subprocess.run([sys.executable, cli] + list(a), env=env, capture_output=True, text=True).stdout
    root = os.path.join(str(TMP[0]), "pdfs")
    rel = "datasheets/regulator/LM317DCYR.pdf"
    os.makedirs(os.path.join(root, os.path.dirname(rel)))
    with open(os.path.join(root, rel), "wb") as f:
        f.write(PDF)
    out = run(dict(os.environ, CLOUD_DATASHEETS=root), "show", "LM317DCYR")
    assert "  datasheet      %s" % os.path.join(root, rel) in out
    if os.path.isdir(os.path.join(os.path.dirname(REPO), cloud_parts.DATASHEETS_REPO)):
        return                                   # the real PDFs are beside this one: the sibling still answers
    env = {k: v for k, v in os.environ.items() if k != "CLOUD_DATASHEETS"}
    for out in (run(env, "show", "LM317DCYR"), run(env, "find", "LM317DCYR")):
        assert "LM317DCYR" in out
        assert not [l for l in out.splitlines() if l.lstrip().startswith(("datasheet ", "datasheet:"))]
        assert "cloud-datasheets" not in out and "not present" not in out and ".pdf" not in out


TMP = [None]


def setup_module(module):
    import tempfile
    TMP[0] = tempfile.mkdtemp(prefix="cloud-parts-test-")


def teardown_module(module):
    import shutil
    shutil.rmtree(TMP[0], ignore_errors=True)
