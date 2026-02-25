# Contributing: Adding Solvers and Fuzzers

This guide covers how to integrate a new SMT solver or fuzzer into the system.

## Architecture Overview

```
scripts/
  solvers/{name}/
    solver.json       # solver configuration (required)
    build.sh          # build script (required)
  fuzzers/{name}/
    fuzzer.json       # fuzzer configuration (required)
    fuzzer.py         # fuzzer module — build_command() + parse_result() (required)
    requirements.txt  # pip dependencies (optional)
    __init__.py       # empty, makes it importable (required)
  scheduling/
    config.py         # reads solver.json and fuzzer.json, resolves parameters
  generate_workflows.py  # generates GitHub Actions dispatcher workflows
.github/workflows/
  build.yml              # reusable: build solver from source
  manager.yml            # reusable: manage build queue
  commit-fuzzer.yml      # reusable: run fuzzer on new commits
  coverage-mapper.yml    # reusable: build per-function coverage mapping
  coverage-daily-check.yml  # reusable: check if coverage needs rebuild
  {solver}.yml           # generated: dispatcher for build
  {solver}-manager.yml   # generated: dispatcher for manager
  ...                    # generated: one per reusable workflow per solver
```

---

## Adding a New Solver

### 1. Create `scripts/solvers/{name}/solver.json`

This is the central configuration file. Every script and workflow reads from it.

```json
{
  "name": "mysolver",
  "display_name": "MySolver",
  "repo_url": "https://github.com/org/mysolver",
  "binary_path": "build/bin/mysolver",
  "solver_flags": "--flag1 --flag2",
  "test_dir": "test/regress",
  "default_oracle": "cvc5",
  "default_fuzzer": "typefuzz",
  "has_commit_fuzzer": true,
  "has_coverage_mapping": true,
  "clang_config": {
    "cpp_standard": "c++17",
    "namespace_prefix": "mysolver::",
    "defines": ["-DMYSOLVER_DEBUG"],
    "includes": ["-I./include", "-I./src"],
    "extra_flags": []
  },
  "artifacts": {
    "binary_subpath": "bin/mysolver",
    "header_dirs": ["include", "src"],
    "deps_dirs": []
  },
  "coverage": {
    "test_type": "ctest",
    "target_jobs": 4
  },
  "ci": {
    "max_fuzzer_jobs": 9,
    "github_release_repo": "org/mysolver",
    "github_release_binary": "mysolver"
  }
}
```

**Required fields:**

| Field | Description |
|---|---|
| `name` | Solver identifier (matches directory name) |
| `display_name` | Human-readable name for workflow titles |
| `repo_url` | Git repository URL |
| `binary_path` | Path to solver binary relative to repo root |
| `solver_flags` | Default CLI flags for running the solver |
| `test_dir` | Directory containing regression tests |
| `default_oracle` | Which solver to use as differential testing oracle |
| `default_fuzzer` | Which fuzzer to use by default |

**Optional sections:**

**`coverage`** — controls how coverage mapping works:

| Field | Default | Description |
|---|---|---|
| `test_type` | `"ctest"` | `"ctest"` (discovers tests via ctest) or `"filesystem"` (walks a directory) |
| `test_timeout` | `120` | Per-test timeout in seconds |
| `test_subdir` | `"regressions"` | Subdirectory for filesystem test discovery |
| `test_glob` | `"*.smt*"` | Glob pattern for filesystem test discovery |
| `skip_tests` | `[]` | List of test names to skip |
| `target_jobs` | `4` | Number of parallel coverage mapping jobs |
| `source_include` | `["src/"]` | Path fragments that identify source files in coverage data |
| `source_exclude` | `["/deps/", "/build/", ...]` | Path fragments to exclude from coverage |

**`ci`** — controls CI workflow behavior:

| Field | Description |
|---|---|
| `max_fuzzer_jobs` | Max parallel fuzzer matrix jobs |
| `test_repo_url` | External test repository URL (if tests are in a separate repo) |
| `test_repo_dir` | Local directory for the test repo clone |
| `configure_command` | Command to run before building (e.g. `./configure.sh production`) |
| `oracle_fallback_pip` | pip package to install oracle if binary not available |
| `oracle_fallback_script` | Script to download oracle binary |
| `github_release_repo` | GitHub `org/repo` for downloading release binaries |
| `github_release_binary` | Binary name in GitHub releases |

**`fuzzer_overrides`** — override fuzzer defaults for this solver:

```json
{
  "fuzzer_overrides": {
    "iterations": 500,
    "timeout": 60
  }
}
```

These override the `default_*` values from `fuzzer.json`. See [Parameter Resolution](#parameter-resolution).

### 2. Create `scripts/solvers/{name}/build.sh`

Build script that clones and compiles the solver. Must support two flags:

```bash
#!/bin/bash
# Usage: ./build.sh [--coverage] [--static]
set -e

ENABLE_COVERAGE=false
ENABLE_STATIC=false
for arg in "$@"; do
    if [[ "$arg" == "--coverage" ]]; then ENABLE_COVERAGE=true; fi
    if [[ "$arg" == "--static" ]]; then ENABLE_STATIC=true; fi
done

# Install build dependencies
sudo apt-get update
sudo apt-get install -y build-essential cmake git

# Coverage tools (only when --coverage)
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    pip3 install fastcov psutil
fi

# Clone
git clone https://github.com/org/mysolver.git mysolver
cd mysolver && mkdir -p build && cd build

# Configure (adjust cmake flags for coverage/static/production)
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    CFLAGS="-O0 -g --coverage" CXXFLAGS="-O0 -g --coverage" \
      cmake -DCMAKE_BUILD_TYPE=Debug ..
elif [[ "$ENABLE_STATIC" == "true" ]]; then
    cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF ..
else
    cmake -DCMAKE_BUILD_TYPE=Release ..
fi

make -j$(nproc)
sudo make install

# Verify
./bin/mysolver --version
```

**Requirements:**
- `--coverage`: build with gcov instrumentation (`-O0 -g --coverage`)
- `--static`: build a static binary for CI artifact caching
- No flags: production release build
- Must install the binary so it's accessible at `binary_path`

### 3. Generate dispatcher workflows

```bash
python3 scripts/generate_workflows.py --write
```

This creates 5 workflow files in `.github/workflows/`:
- `{name}.yml` — build dispatcher
- `{name}-manager.yml` — build queue manager
- `{name}-commit-fuzzer.yml` — commit fuzzer dispatcher
- `{name}-coverage-mapper.yml` — coverage mapping dispatcher
- `{name}-coverage-daily-check.yml` — daily coverage check dispatcher

### 4. Verify

```bash
# Check that config discovery finds the new solver
python3 -c "from scripts.scheduling.config import discover_solvers; print(discover_solvers())"

# Check config loads correctly
python3 -c "from scripts.scheduling.config import get_solver_config; import json; print(json.dumps(get_solver_config('mysolver'), indent=2))"
```

---

## Adding a New Fuzzer

### 1. Create the fuzzer directory

```
scripts/fuzzers/{name}/
  __init__.py        # empty file
  fuzzer.json        # configuration
  fuzzer.py          # module implementing build_command() and parse_result()
  requirements.txt   # pip dependencies (optional)
```

### 2. Create `fuzzer.json`

```json
{
  "name": "myfuzzer",
  "description": "What this fuzzer does",
  "default_iterations": 100,
  "default_timeout": 60,
  "ci": {
    "install_repo": "https://github.com/org/myfuzzer.git"
  }
}
```

Every key starting with `default_` becomes a fuzzer parameter. The `default_` prefix is stripped and the value is passed to `build_command()` as a keyword argument.

For example, `"default_iterations": 100` becomes `build_command(..., iterations=100)`.

Solvers can override these via `fuzzer_overrides` in their `solver.json`, and users can override on the CLI with `--fuzzer-param iterations=500`.

### 3. Implement `fuzzer.py`

The module must export two functions:

```python
from typing import List, Tuple


def build_command(
    seed_path: str,
    solver_clis: str,
    bugs_dir: str,
    scratch_dir: str,
    log_dir: str,
    **kwargs,
) -> List[str]:
    """Build the CLI command to run the fuzzer.

    Args:
        seed_path: absolute path to the seed SMT file
        solver_clis: semicolon-separated solver CLI strings
                     (e.g. "z3 smt.threads=1;cvc5 --check-models")
        bugs_dir: directory to write discovered bug files (.smt2)
        scratch_dir: temporary working directory (cleaned up after each run)
        log_dir: directory for fuzzer logs (cleaned up after each run)
        **kwargs: fuzzer parameters from fuzzer.json default_* values,
                  solver fuzzer_overrides, and --fuzzer-param CLI overrides

    Returns:
        Command as a list of strings for subprocess.run()
    """
    iterations = kwargs.get("iterations", 100)
    timeout = kwargs.get("timeout", 60)

    return [
        "myfuzzer",
        "--iterations", str(iterations),
        "--timeout", str(timeout),
        "--bugs", bugs_dir,
        "--scratch", scratch_dir,
        solver_clis,
        seed_path,
    ]


def parse_result(exit_code: int, bugs_dir: str) -> Tuple[bool, str]:
    """Interpret the fuzzer's exit code.

    Args:
        exit_code: process exit code
        bugs_dir: directory where bug files were written

    Returns:
        (bug_found, exit_action) where exit_action is one of:
          - 'requeue': test produced results, run it again
          - 'remove': test is unsupported, don't run again
          - 'continue': test finished, move to next
    """
    if exit_code == 0:
        return False, 'requeue'
    # Define your fuzzer's exit codes here
    return False, 'continue'
```

**Important:** Accept `**kwargs` in `build_command()` so the system can pass arbitrary parameters from config without the fuzzer needing to know about all of them upfront.

### 4. Add `requirements.txt` (if needed)

```
some-dependency==1.2.3
another-dep>=2.0
```

The CI workflow runs `pip install -r requirements.txt` from the fuzzer directory if the file exists.

### 5. Point a solver to the new fuzzer

In `scripts/solvers/{solver}/solver.json`:

```json
{
  "default_fuzzer": "myfuzzer"
}
```

Or override parameters for this solver:

```json
{
  "default_fuzzer": "myfuzzer",
  "fuzzer_overrides": {
    "iterations": 500
  }
}
```

### 6. Verify

```bash
# Check discovery
python3 -c "from scripts.scheduling.config import discover_fuzzers; print(discover_fuzzers())"

# Check parameter resolution for a solver
python3 -c "from scripts.scheduling.config import get_fuzzer_params; print(get_fuzzer_params('z3', 'myfuzzer'))"
```

---

## Parameter Resolution

Fuzzer parameters are resolved in this order (later overrides earlier):

1. **`fuzzer.json`** `default_*` keys (e.g. `default_iterations: 250`)
2. **`solver.json`** `fuzzer_overrides` (e.g. `fuzzer_overrides.iterations: 500`)
3. **CLI** `--fuzzer-param` flags (e.g. `--fuzzer-param iterations=1000`)

The resolved parameters are passed as `**kwargs` to `build_command()`.

---

## Checklist

### New Solver

- [ ] `scripts/solvers/{name}/solver.json` with all required fields
- [ ] `scripts/solvers/{name}/build.sh` supporting `--coverage` and `--static`
- [ ] Run `python3 scripts/generate_workflows.py --write`
- [ ] Verify config discovery finds the solver
- [ ] Verify build script works: `bash scripts/solvers/{name}/build.sh`

### New Fuzzer

- [ ] `scripts/fuzzers/{name}/__init__.py` (empty)
- [ ] `scripts/fuzzers/{name}/fuzzer.json` with `default_*` parameters
- [ ] `scripts/fuzzers/{name}/fuzzer.py` with `build_command()` and `parse_result()`
- [ ] `scripts/fuzzers/{name}/requirements.txt` (if pip dependencies needed)
- [ ] Point at least one solver to the fuzzer via `default_fuzzer`
- [ ] Verify fuzzer discovery and parameter resolution
