"""Typefuzz fuzzer plugin.

Typefuzz (yinyang) performs type-aware mutations on SMT formulas.
It takes a seed SMT file, generates mutants, and tests them against
one or more solvers to detect soundness bugs.
"""

from pathlib import Path
from typing import List, Tuple


def build_command(
    seed_path: str,
    solver_clis: str,
    bugs_dir: str,
    scratch_dir: str,
    log_dir: str,
    iterations: int,
    modulo: int,
    timeout: int,
) -> List[str]:
    """Build the typefuzz CLI command.

    Args:
        seed_path: path to the seed SMT file
        solver_clis: semicolon-separated solver CLI strings
        bugs_dir: directory to write bug files
        scratch_dir: temporary working directory
        log_dir: directory for fuzzer logs
        iterations: mutation iterations per seed
        modulo: modulo parameter for typefuzz -m flag
        timeout: per-solver timeout in seconds

    Returns:
        Command list for subprocess.run
    """
    return [
        "typefuzz",
        "-i", str(iterations),
        "-m", str(modulo),
        "--timeout", str(timeout),
        "--bugs", str(bugs_dir),
        "--scratch", str(scratch_dir),
        "--logfolder", str(log_dir),
        solver_clis,
        str(seed_path),
    ]


def parse_result(exit_code: int, bugs_dir: str) -> Tuple[bool, str]:
    """Interpret the typefuzz exit code.

    Args:
        exit_code: process exit code
        bugs_dir: directory where bug files were written

    Returns:
        (bug_found, exit_action) where exit_action is one of:
        'requeue', 'remove', 'continue'
    """
    # Exit code 10: bugs found
    if exit_code == 10:
        return True, 'requeue'
    # Exit code 3: unsupported operation (seed uses features the solver can't handle)
    elif exit_code == 3:
        return False, 'remove'
    # Exit code 0: no bugs found, normal completion
    elif exit_code == 0:
        return False, 'requeue'
    # Any other exit code: error, skip this seed
    return False, 'continue'
