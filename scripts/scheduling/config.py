"""Solver and fuzzer configuration discovery.

Discovers available solvers by globbing scripts/solvers/*/solver.json
and available fuzzers by globbing scripts/fuzzers/*/fuzzer.json.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

# Root of the scripts/ directory, resolved from this file's location
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_SOLVERS_DIR = _SCRIPTS_DIR / "solvers"
_FUZZERS_DIR = _SCRIPTS_DIR / "fuzzers"

# Default oracle for solvers that don't specify one
DEFAULT_ORACLE = "cvc5"


def discover_solvers() -> List[str]:
    """Return list of solver names that have a solver.json config."""
    solvers = []
    for config_file in sorted(_SOLVERS_DIR.glob("*/solver.json")):
        solvers.append(config_file.parent.name)
    return solvers


def discover_fuzzers() -> List[str]:
    """Return list of fuzzer names that have a fuzzer.json config."""
    fuzzers = []
    for config_file in sorted(_FUZZERS_DIR.glob("*/fuzzer.json")):
        fuzzers.append(config_file.parent.name)
    return fuzzers


def get_solver_config(name: str) -> Dict:
    """Load a solver's config from scripts/solvers/{name}/solver.json.

    Raises FileNotFoundError if solver config does not exist.
    """
    config_path = _SOLVERS_DIR / name / "solver.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Solver config not found: {config_path}\n"
            f"Available solvers: {discover_solvers()}"
        )
    with open(config_path) as f:
        return json.load(f)


def get_fuzzer_config(name: str) -> Dict:
    """Load a fuzzer's config from scripts/fuzzers/{name}/fuzzer.json.

    Raises FileNotFoundError if fuzzer config does not exist.
    """
    config_path = _FUZZERS_DIR / name / "fuzzer.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Fuzzer config not found: {config_path}\n"
            f"Available fuzzers: {discover_fuzzers()}"
        )
    with open(config_path) as f:
        return json.load(f)


def get_oracle_name(solver_name: str, override: Optional[str] = None) -> str:
    """Get the oracle solver name for a given solver.

    Uses explicit override if provided, otherwise reads default_oracle
    from solver config, falling back to DEFAULT_ORACLE.
    """
    if override:
        return override
    config = get_solver_config(solver_name)
    return config.get("default_oracle", DEFAULT_ORACLE)


def get_fuzzer_name(solver_name: str, override: Optional[str] = None) -> str:
    """Get the fuzzer name for a given solver.

    Uses explicit override if provided, otherwise reads default_fuzzer
    from solver config, falling back to 'typefuzz'.
    """
    if override:
        return override
    config = get_solver_config(solver_name)
    return config.get("default_fuzzer", "typefuzz")


def get_fuzzer_params(solver_name: str, fuzzer_name: str) -> Dict:
    """Resolve all fuzzer parameters with solver-level overrides.

    Collects every ``default_*`` key from fuzzer.json (stripping the
    ``default_`` prefix), then applies any solver-level overrides from
    ``solver.json -> fuzzer_overrides``.

    Resolution order per parameter:
      1. solver.json  -> fuzzer_overrides.{param}
      2. fuzzer.json  -> default_{param}

    Returns a plain dict that can be spread into build_command(**params).
    """
    fuzzer_config = get_fuzzer_config(fuzzer_name)

    # Collect defaults: default_iterations -> iterations, etc.
    params = {}
    for key, value in fuzzer_config.items():
        if key.startswith("default_"):
            param_name = key[len("default_"):]
            params[param_name] = value

    # Apply solver-level overrides
    solver_config = get_solver_config(solver_name)
    overrides = solver_config.get("fuzzer_overrides", {})
    params.update(overrides)

    return params
