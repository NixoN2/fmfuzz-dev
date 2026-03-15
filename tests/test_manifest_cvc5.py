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


def test_discovers_all_tests():
    entries = run_script(FIXTURE)
    files = [e["file"] for e in entries]
    assert "arith/add.smt2" in files
    assert "arith/mul.smt2" in files
    assert "bv/foo.smt2" in files
    assert "bv/bar.smt2" in files
    assert "strings/concat.smt2" in files
    assert len(files) == 5


def test_paths_relative_to_test_subdir():
    entries = run_script(FIXTURE)
    for e in entries:
        assert not Path(e["file"]).is_absolute()
        # Paths should NOT contain 'test/regress/cli/' prefix
        assert not e["file"].startswith("test/"), f"Expected bare relative path, got: {e['file']}"


def test_no_flags():
    entries = run_script(FIXTURE)
    for e in entries:
        assert e["flags"] == []


def test_schema():
    entries = run_script(FIXTURE)
    assert isinstance(entries, list)
    for e in entries:
        assert set(e.keys()) == {"file", "flags"}
