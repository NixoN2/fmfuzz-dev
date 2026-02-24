# Extensible Solver and Fuzzer Configuration System

## Problem

Adding a new solver or fuzzer to fmfuzz-dev requires changes scattered across multiple files: hardcoded `choices=['z3', 'cvc5']` in 4 scheduling scripts, duplicated 930-line commit fuzzer files per solver, hardcoded binary paths and CLI flags, and no way to swap the mutation engine.

## Goals

- Adding a new solver: create one directory with `solver.json` + `build.sh`. No changes to core code.
- Adding a new fuzzer: create one directory with `fuzzer.json` + `fuzzer.py` + `setup.sh`. No changes to core code.
- Eliminate the duplicated `simple_commit_fuzzer.py` (z3 and cvc5 versions differ by ~30 lines out of 930).
- Keep existing CLI invocations working (`python3 scripts/scheduling/manager.py cvc5 ...`).

## Non-goals

- No workflow YAML logic changes (only path updates from directory moves).
- No changes to `oracle.py`, coverage scripts, or evaluation scripts.
- No abstract base classes or factory patterns.
- No solver.json for solvers without commit fuzzers (bitwuzla, opensmt, q3b, smtrat, stp) -- added when needed.

## Directory Structure

```
scripts/
  solvers/
    cvc5/
      solver.json
      build.sh
      collect_build_artifacts.sh
      extract_build_artifacts.sh
      run_regression_tests.sh
      commit_fuzzer/
        prepare_commit_fuzzer.py
        run_commit_fuzzer.sh
        run_simple_fuzzer.sh
        run_prepare_commit_fuzzer.sh
        commit_fuzzer.py
    z3/
      solver.json
      build.sh
      collect_build_artifacts.sh
      extract_build_artifacts.sh
      commit_fuzzer/
        prepare_commit_fuzzer.py
        run_prepare_commit_fuzzer.sh
    bitwuzla/
      build.sh
    opensmt/
      build.sh
    q3b/
      build.sh
    smtrat/
      build.sh
    stp/
      build.sh
  fuzzers/
    typefuzz/
      fuzzer.json
      setup.sh
      fuzzer.py
  commit_fuzzer/
    simple_commit_fuzzer.py    # unified, replaces per-solver copies
  scheduling/
    config.py                  # discovery + loading
    builder.py
    fuzzer.py
    manager.py
    s3_state.py
  coverage/
  evaluation/
  oracle.py
```

## Solver Configuration

Each solver with commit fuzzer support gets `solver.json`:

```json
// scripts/solvers/cvc5/solver.json
{
  "name": "cvc5",
  "repo_url": "https://github.com/cvc5/cvc5",
  "binary_path": "build/bin/cvc5",
  "solver_flags": "--check-models --check-proofs --strings-exp",
  "test_dir": "test/regress/cli",
  "default_oracle": "z3",
  "default_fuzzer": "typefuzz",
  "has_commit_fuzzer": true,
  "has_coverage_mapping": true
}
```

```json
// scripts/solvers/z3/solver.json
{
  "name": "z3",
  "repo_url": "https://github.com/Z3Prover/z3",
  "binary_path": "build/z3",
  "solver_flags": "smt.threads=1 memory_max_size=2048 model_validate=true",
  "test_dir": "src/test",
  "default_oracle": "cvc5",
  "default_fuzzer": "typefuzz",
  "has_commit_fuzzer": true,
  "has_coverage_mapping": true
}
```

New solvers default to cvc5 as oracle if `default_oracle` is not set.

## Fuzzer Plugin System

Each fuzzer is a directory under `scripts/fuzzers/` with three files:

### fuzzer.json

```json
// scripts/fuzzers/typefuzz/fuzzer.json
{
  "name": "typefuzz",
  "description": "Type-aware SMT formula mutation",
  "default_iterations": 250,
  "default_modulo": 2,
  "default_timeout": 120
}
```

### setup.sh

Script to install the fuzzer tool. Called during CI setup or local development.

### fuzzer.py

Two functions conforming to the plugin contract:

```python
def build_command(seed_path, solver_clis, bugs_dir, scratch_dir, log_dir,
                  iterations, modulo, timeout):
    """Build the CLI command to execute.

    Args:
        seed_path: path to the seed SMT file
        solver_clis: semicolon-separated solver CLI strings
        bugs_dir: directory to write bug files
        scratch_dir: temporary working directory
        log_dir: directory for fuzzer logs
        iterations: mutation iterations per seed
        modulo: modulo parameter
        timeout: per-solver timeout in seconds

    Returns:
        list[str]: command and arguments for subprocess.run
    """

def parse_result(exit_code, bugs_dir):
    """Interpret the fuzzer's exit code.

    Args:
        exit_code: process exit code
        bugs_dir: directory where bug files were written

    Returns:
        tuple[bool, str]: (bug_found, exit_action)
        exit_action is one of: 'requeue', 'remove', 'continue'
    """
```

The commit fuzzer loads the plugin via `importlib.import_module(f"fuzzers.{fuzzer_name}.fuzzer")`.

## Discovery and Config Loading

`scripts/scheduling/config.py`:

```python
def discover_solvers() -> list[str]:
    """Glob scripts/solvers/*/solver.json, return solver names."""

def discover_fuzzers() -> list[str]:
    """Glob scripts/fuzzers/*/fuzzer.json, return fuzzer names."""

def get_solver_config(name: str) -> dict:
    """Load scripts/solvers/{name}/solver.json."""

def get_fuzzer_config(name: str) -> dict:
    """Load scripts/fuzzers/{name}/fuzzer.json."""
```

Path resolution uses the script's own location (`Path(__file__).parent.parent`) to find `scripts/solvers/` and `scripts/fuzzers/`, so it works regardless of working directory.

## Scheduling Script Changes

All four scheduling scripts (`builder.py`, `fuzzer.py`, `manager.py`, `s3_state.py`) replace:

```python
parser.add_argument('solver', choices=['z3', 'cvc5'], help='Solver name')
```

With:

```python
from config import discover_solvers
parser.add_argument('solver', choices=discover_solvers(), help='Solver name')
```

Existing invocations (`python3 scripts/scheduling/manager.py cvc5 ...`) continue to work unchanged. The solver name is still a positional arg, just validated against the discovered list instead of a hardcoded one.

## Unified Commit Fuzzer

`scripts/commit_fuzzer/simple_commit_fuzzer.py` replaces both per-solver copies. Changes to `SimpleCommitFuzzer`:

- `__init__` takes `solver_name: str` and `oracle_name: Optional[str]` instead of `z3_path`/`cvc5_path`
- Loads binary paths and flags from solver configs via `get_solver_config()`
- Loads fuzzer defaults from fuzzer config via `get_fuzzer_config()`
- `_get_solver_clis()` builds CLI strings from config fields
- `_validate_solvers()` validates paths from config
- `_run_typefuzz()` renamed to `_run_fuzzer()`, uses loaded fuzzer module's `build_command()` and `parse_result()`

CLI:

```bash
# Uses defaults from config (oracle=z3, fuzzer=typefuzz)
python3 scripts/commit_fuzzer/simple_commit_fuzzer.py \
  --solver cvc5 --tests-json '[...]'

# Explicit overrides
python3 scripts/commit_fuzzer/simple_commit_fuzzer.py \
  --solver cvc5 --oracle z3 --fuzzer typefuzz --tests-json '[...]'
```

Everything else in the class (resource monitoring, worker management, bug collection, shutdown handling, signal handlers) is unchanged.

## Data Flow

```
Workflow YAML
  |
  +-- manager.py cvc5 https://github.com/cvc5/cvc5.git
  |     \-- config.discover_solvers() validates "cvc5" exists
  |     \-- config.get_solver_config("cvc5") for repo_url
  |
  +-- builder.py cvc5
  |     \-- same discovery
  |
  +-- fuzzer.py cvc5 select
  |     \-- same discovery
  |
  \-- simple_commit_fuzzer.py --solver cvc5 --tests-json '[...]'
        |
        +-- config.get_solver_config("cvc5")
        |     binary_path: "build/bin/cvc5"
        |     solver_flags: "--check-models --check-proofs --strings-exp"
        |     default_oracle: "z3"
        |     default_fuzzer: "typefuzz"
        |
        +-- config.get_solver_config("z3")  # oracle
        |     binary_path: "build/z3"
        |     solver_flags: "smt.threads=1 memory_max_size=2048 ..."
        |
        +-- config.get_fuzzer_config("typefuzz")
        |     default_iterations, default_modulo, default_timeout
        |
        +-- importlib.import_module("fuzzers.typefuzz.fuzzer")
        |     build_command(), parse_result()
        |
        \-- Worker loop:
              1. Pick seed from queue
              2. fuzzer.build_command(seed, solver_clis, ...) -> cmd
              3. subprocess.run(cmd) -> exit_code
              4. fuzzer.parse_result(exit_code, bugs_dir) -> (bug_found, action)
              5. Collect bug files, requeue/remove based on action
```

## File Changes Summary

New files:
- `scripts/solvers/cvc5/solver.json`
- `scripts/solvers/z3/solver.json`
- `scripts/fuzzers/typefuzz/fuzzer.json`
- `scripts/fuzzers/typefuzz/setup.sh`
- `scripts/fuzzers/typefuzz/fuzzer.py`
- `scripts/scheduling/config.py`
- `scripts/commit_fuzzer/simple_commit_fuzzer.py`

Moved (directory rename, content unchanged):
- `scripts/cvc5/*` -> `scripts/solvers/cvc5/*`
- `scripts/z3/*` -> `scripts/solvers/z3/*`
- `scripts/bitwuzla/*` -> `scripts/solvers/bitwuzla/*`
- `scripts/opensmt/*` -> `scripts/solvers/opensmt/*`
- `scripts/q3b/*` -> `scripts/solvers/q3b/*`
- `scripts/smtrat/*` -> `scripts/solvers/smtrat/*`
- `scripts/stp/*` -> `scripts/solvers/stp/*`

Modified (choices removal):
- `scripts/scheduling/builder.py`
- `scripts/scheduling/fuzzer.py`
- `scripts/scheduling/manager.py`
- `scripts/scheduling/s3_state.py`

Modified (path updates only):
- `.github/workflows/*.yml` -- `scripts/cvc5/` -> `scripts/solvers/cvc5/`, etc.

Deleted:
- `scripts/solvers/cvc5/commit_fuzzer/simple_commit_fuzzer.py` (replaced by unified)
- `scripts/solvers/z3/commit_fuzzer/simple_commit_fuzzer.py` (replaced by unified)

Untouched:
- `scripts/oracle.py`
- `scripts/coverage/`
- `scripts/evaluation/`
- All `build.sh`, `collect_build_artifacts.sh`, `extract_build_artifacts.sh` (content unchanged)
- `prepare_commit_fuzzer.py` (stays per-solver)
