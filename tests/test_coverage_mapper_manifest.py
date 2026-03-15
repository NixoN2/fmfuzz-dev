"""Unit tests for the manifest test_type in CoverageMapper."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts/scheduling"))
sys.path.insert(0, str(REPO_ROOT / "scripts/coverage"))

# psutil may not be installed in the test environment; stub it out before import
if "psutil" not in sys.modules:
    import types
    _psutil_stub = types.ModuleType("psutil")
    class _Process:
        def memory_info(self):
            class _mi:
                rss = 0
            return _mi()
    _psutil_stub.Process = _Process
    sys.modules["psutil"] = _psutil_stub

_bitwuzla_config = {
    "name": "bitwuzla",
    "binary_path": "build/src/main/bitwuzla",
    "solver_flags": "",
    "artifacts": {"binary_subpath": "src/main/bitwuzla"},
    "coverage": {
        "test_type": "manifest",
        "manifest_script": "scripts/solvers/bitwuzla/gen_test_manifest.py",
        "test_subdir": "test/regress",
        "source_include": ["src/"],
        "source_exclude": ["/subprojects/"],
        "target_jobs": 4,
    }
}


def make_mapper(build_dir_path: Path, test_dir: str = None):
    """Create a CoverageMapper with mocked config."""
    with patch("coverage_mapper.get_solver_config", return_value=_bitwuzla_config):
        import coverage_mapper
        mapper = coverage_mapper.CoverageMapper(
            "bitwuzla",
            build_dir=str(build_dir_path),
            test_dir=test_dir
        )
    return mapper


def test_manifest_entries_returned_as_3_tuples(tmp_path):
    """_get_manifest_tests returns (int, str, list) 3-tuples."""
    # Create a fake manifest script that outputs 2 entries
    fake_script = tmp_path / "gen_test_manifest.py"
    fake_script.write_text(
        'import json,sys; print(json.dumps(['
        '{"file":"solver/bv/add.smt2","flags":[]},'
        '{"file":"solver/bv/mul.smt2","flags":["--bv-solver=prop"]}'
        ']))'
    )
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    import coverage_mapper as cm
    original_repo_root = cm.REPO_ROOT

    mapper = make_mapper(build_dir)
    mapper.cov_config["manifest_script"] = str(fake_script.relative_to(tmp_path))

    cm.REPO_ROOT = tmp_path
    try:
        tests = mapper._get_manifest_tests()
    finally:
        cm.REPO_ROOT = original_repo_root

    assert len(tests) == 2
    assert tests[0] == (1, "solver/bv/add.smt2", [])
    assert tests[1] == (2, "solver/bv/mul.smt2", ["--bv-solver=prop"])


def test_get_tests_dispatches_manifest(tmp_path):
    """get_tests() calls _get_manifest_tests when test_type is manifest."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    mapper = make_mapper(build_dir)

    with patch.object(mapper, "_get_manifest_tests", return_value=[(1, "a.smt2", [])]) as mock:
        result = mapper.get_tests()

    mock.assert_called_once()
    assert result == [(1, "a.smt2", [])]


def test_process_tests_handles_3_tuple(tmp_path):
    """process_tests doesn't crash when given 3-tuple test_info."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    # Write a temp file to avoid the write_intermediate_mapping crash
    mapper = make_mapper(build_dir)

    with patch.object(mapper, "process_single_test", return_value=None) as mock:
        mapper.process_tests([(1, "solver/bv/add.smt2", [])], max_runtime_seconds=None)

    mock.assert_called_once()
    call_arg = mock.call_args[0][0]
    assert call_arg == (1, "solver/bv/add.smt2", [])
