# Contributing: Adding Solvers and Fuzzers

This guide covers how to integrate a new SMT solver or fuzzer into the system.

## Architecture Overview

```
scripts/
  solvers/{name}/
    solver.json       # solver configuration (required)
    build.sh          # build script (required)
  fuzzers/{name}/
    fuzzer.json       # CI configuration — name, description, setup script (required)
    fuzzer.py         # Fuzzer class — command, dirs, exit codes, lifecycle (required)
    setup.sh          # install/build the fuzzer tool (required if ci.setup_script set)
    requirements.txt  # pip dependencies (optional)
    __init__.py       # empty, makes it importable (required)
  commit_fuzzer/
    simple_commit_fuzzer.py  # generic runner — loads Fuzzer class via importlib
    resource_monitor.py      # CPU/RAM monitoring and graceful shutdown
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
  __init__.py        # empty file, makes it importable
  fuzzer.json        # CI configuration (name, description, setup script)
  fuzzer.py          # Fuzzer class with all runtime behavior
  setup.sh           # install/build the fuzzer tool (if needed)
  requirements.txt   # pip dependencies (optional)
```

### 2. Create `fuzzer.json`

This file is CI-only — it tells the workflow how to install the fuzzer tool:

```json
{
  "name": "myfuzzer",
  "description": "What this fuzzer does",
  "ci": {
    "setup_script": "setup.sh"
  }
}
```

The `ci.setup_script` is a path relative to the fuzzer directory. The CI workflow runs `bash scripts/fuzzers/{name}/{setup_script}` to install the fuzzer tool before running.

### 3. Implement `fuzzer.py`

The module must export a `Fuzzer` class. The runner instantiates it once per test and calls `execute()` → `collect()` → `parse_result()` → `cleanup()`.

```python
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class Fuzzer:
    # Default parameter values — overridden by solver.json fuzzer_overrides
    # and CLI --fuzzer-param flags
    DEFAULT_PARAMS = {"iterations": 100, "timeout": 60}

    # Command template — tokens are formatted with params + dir paths + seed_path
    DEFAULT_COMMAND = [
        "myfuzzer",
        "--iterations", "{iterations}",
        "--timeout", "{timeout}",
        "--bugs", "{bugs_dir}",
        "--scratch", "{scratch_dir}",
        "{solver_clis}",
        "{seed_path}",
    ]

    # Exit code → (bug_found, action) mapping
    # action: 'requeue' | 'remove' | 'continue'
    DEFAULT_EXIT_CODES = {
        "0":  {"bug_found": False, "action": "requeue"},
        "3":  {"bug_found": False, "action": "remove"},
        "10": {"bug_found": True,  "action": "requeue"},
    }
    DEFAULT_EXIT_ACTION = {"bug_found": False, "action": "continue"}

    # Working directories — type 'output' dirs are kept (bugs accumulate);
    # type 'temp' dirs are deleted after each run in cleanup()
    DEFAULT_DIRS = {
        "bugs_dir":    {"path": "bugs/worker_{worker_id}", "type": "output"},
        "scratch_dir": {"path": "scratch_{worker_id}",     "type": "temp"},
    }

    # Glob patterns for collecting bug files from output dirs
    DEFAULT_BUG_PATTERNS = ["*.smt2", "*.smt"]

    # Separator between solver_cli and oracle_cli in the {solver_clis} token
    DEFAULT_SOLVER_CLIS_SEPARATOR = ";"

    def __init__(
        self,
        worker_id: int,
        seed_path: str,
        solver_cli: str,
        oracle_cli: str,
        params_override: Optional[Dict] = None,
    ):
        params = {**self.DEFAULT_PARAMS, **(params_override or {})}

        self.dirs = {
            name: {"path": Path(cfg["path"].format(worker_id=worker_id)), "type": cfg["type"]}
            for name, cfg in self.DEFAULT_DIRS.items()
        }

        ctx = {
            **params,
            "seed_path": seed_path,
            "solver_cli": solver_cli,
            "oracle_cli": oracle_cli,
            "solver_clis": self.DEFAULT_SOLVER_CLIS_SEPARATOR.join([solver_cli, oracle_cli]),
            "worker_id": str(worker_id),
        }
        ctx.update({name: str(info["path"]) for name, info in self.dirs.items()})
        self.cmd = [token.format_map(ctx) for token in self.DEFAULT_COMMAND]

        for info in self.dirs.values():
            info["path"].mkdir(parents=True, exist_ok=True)

    def execute(self, timeout: Optional[float] = None) -> Tuple[int, float]:
        start = time.time()
        try:
            kwargs: Dict = {"capture_output": True, "text": True}
            if timeout and timeout > 0:
                kwargs["timeout"] = timeout
            result = subprocess.run(self.cmd, **kwargs)
            return result.returncode, time.time() - start
        except subprocess.TimeoutExpired:
            return -1, time.time() - start
        except Exception:
            return -1, time.time() - start

    def collect(self) -> List[Path]:
        files = []
        for info in self.dirs.values():
            if info["type"] == "output" and info["path"].exists():
                for pattern in self.DEFAULT_BUG_PATTERNS:
                    files.extend(info["path"].glob(pattern))
        return files

    def parse_result(self, exit_code: int) -> Tuple[bool, str]:
        entry = self.DEFAULT_EXIT_CODES.get(str(exit_code), self.DEFAULT_EXIT_ACTION)
        return entry["bug_found"], entry["action"]

    def cleanup(self):
        for info in self.dirs.values():
            if info["type"] == "temp":
                shutil.rmtree(info["path"], ignore_errors=True)
```

**Key design points:**

- All defaults live as class constants — override only what differs from `typefuzz`
- `DEFAULT_COMMAND` tokens are formatted with params + dir paths; add/remove tokens freely
- `DEFAULT_DIRS` controls what directories exist and whether they're kept (`output`) or cleaned (`temp`) after each run
- `DEFAULT_SOLVER_CLIS_SEPARATOR` controls how solver and oracle CLIs are joined into `{solver_clis}`; change if your fuzzer uses a different format

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

1. **`Fuzzer.DEFAULT_PARAMS`** — class constant in `fuzzer.py` (e.g. `{"iterations": 250}`)
2. **`solver.json`** `fuzzer_overrides` (e.g. `fuzzer_overrides.iterations: 500`)
3. **CLI** `--fuzzer-param` flags (e.g. `--fuzzer-param iterations=1000`)

The resolved parameters are merged and passed as `params_override` to `Fuzzer.__init__()`, which applies them on top of `DEFAULT_PARAMS`.

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
- [ ] `scripts/fuzzers/{name}/fuzzer.json` with `name`, `description`, `ci.setup_script`
- [ ] `scripts/fuzzers/{name}/fuzzer.py` with `Fuzzer` class and all `DEFAULT_*` constants
- [ ] `scripts/fuzzers/{name}/setup.sh` to install the fuzzer tool (if `ci.setup_script` set)
- [ ] `scripts/fuzzers/{name}/requirements.txt` (if pip dependencies needed)
- [ ] Point at least one solver to the fuzzer via `default_fuzzer`
- [ ] Verify fuzzer discovery and parameter resolution
