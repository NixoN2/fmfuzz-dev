import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts/solvers/bitwuzla/gen_test_manifest.py"
FIXTURE = REPO_ROOT / "tests/fixtures/bitwuzla" / "test" / "regress"


def run_script(test_dir: Path) -> list:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(test_dir)],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def test_discovers_all_entries():
    entries = run_script(FIXTURE)
    # 6 original + 1 incremental (backtrack/push1.smt2); .btor.smt2 filtered out
    assert len(entries) == 7


def test_no_flags_entry():
    entries = run_script(FIXTURE)
    no_flag = [e for e in entries if e["file"] == "solver/bv/add.smt2"]
    assert len(no_flag) == 1
    assert no_flag[0]["flags"] == []


def test_flag_variants_same_file():
    entries = run_script(FIXTURE)
    mul_entries = [e for e in entries if e["file"] == "solver/bv/mul.smt2"]
    assert len(mul_entries) == 2
    flags_set = {tuple(e["flags"]) for e in mul_entries}
    assert ("--bv-solver=prop",) in flags_set
    assert ("--bv-solver=preprop",) in flags_set


def test_btor_files_excluded():
    entries = run_script(FIXTURE)
    files = [e["file"] for e in entries]
    assert not any(f.endswith(".btor.smt2") for f in files)


def test_incremental_file_gets_dash_i():
    entries = run_script(FIXTURE)
    push = [e for e in entries if e["file"] == "backtrack/push1.smt2"]
    assert len(push) == 1
    assert "-i" in push[0]["flags"]


def test_non_incremental_file_no_dash_i():
    entries = run_script(FIXTURE)
    add = [e for e in entries if e["file"] == "solver/bv/add.smt2"]
    assert len(add) == 1
    assert "-i" not in add[0]["flags"]


def test_paths_relative_to_test_regress():
    entries = run_script(FIXTURE)
    for e in entries:
        assert not Path(e["file"]).is_absolute()
        assert not e["file"].startswith("test/"), f"Got unexpected prefix: {e['file']}"


def test_schema():
    entries = run_script(FIXTURE)
    for e in entries:
        assert set(e.keys()) == {"file", "flags"}
        assert isinstance(e["file"], str)
        assert isinstance(e["flags"], list)
