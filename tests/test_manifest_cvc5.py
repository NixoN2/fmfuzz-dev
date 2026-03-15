import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts/solvers/cvc5/gen_test_manifest.py"
FIXTURE = REPO_ROOT / "tests/fixtures/cvc5" / "test" / "regress" / "cli"


def run_script(test_dir: Path) -> list:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(test_dir)],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def test_discovers_all_paths():
    entries = run_script(FIXTURE)
    files = [e["file"] for e in entries]
    # All 7 paths present (some produce multiple entries)
    assert "arith/add.smt2" in files
    assert "arith/mul.smt2" in files
    assert "bv/foo.smt2" in files
    assert "bv/bar.smt2" in files
    assert "strings/concat.smt2" in files
    # TPTP-style filenames with '=' and '+' must be captured correctly
    assert "tptp/ARI086=1.smt2" in files
    assert "tptp/KRS018+1.smt2" in files


def test_no_command_line_emits_empty_flags():
    entries = run_script(FIXTURE)
    # bv/foo.smt2 has no COMMAND-LINE directive → one entry with flags=[]
    foo = [e for e in entries if e["file"] == "bv/foo.smt2"]
    assert len(foo) == 1
    assert foo[0]["flags"] == []


def test_single_command_line_parsed():
    entries = run_script(FIXTURE)
    # arith/mul.smt2 has '; COMMAND-LINE: -q' → flags=["-q"]
    mul = [e for e in entries if e["file"] == "arith/mul.smt2"]
    assert len(mul) == 1
    assert mul[0]["flags"] == ["-q"]


def test_multiple_command_lines_produce_separate_entries():
    entries = run_script(FIXTURE)
    # bv/bar.smt2 has two COMMAND-LINE directives → two entries
    bar = [e for e in entries if e["file"] == "bv/bar.smt2"]
    assert len(bar) == 2
    flags_set = {tuple(e["flags"]) for e in bar}
    assert ("--bitblast=eager",) in flags_set
    assert ("--bitblast=eager", "--bv-solver=bitblast-internal") in flags_set


def test_empty_command_line_directive_treated_as_no_flags():
    entries = run_script(FIXTURE)
    # arith/add.smt2 has '; COMMAND-LINE:' (empty) → one entry with flags=[]
    add = [e for e in entries if e["file"] == "arith/add.smt2"]
    assert len(add) == 1
    assert add[0]["flags"] == []


def test_paths_relative_to_test_subdir():
    entries = run_script(FIXTURE)
    for e in entries:
        assert not Path(e["file"]).is_absolute()
        assert not e["file"].startswith("test/"), f"Expected bare relative path, got: {e['file']}"


def test_schema():
    entries = run_script(FIXTURE)
    assert isinstance(entries, list)
    for e in entries:
        assert set(e.keys()) == {"file", "flags"}
        assert isinstance(e["file"], str)
        assert isinstance(e["flags"], list)
