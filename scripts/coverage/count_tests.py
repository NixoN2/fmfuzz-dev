#!/usr/bin/env python3
"""
Unified test counting utility.
Uses CoverageMapper and solver config for config-driven test discovery.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scheduling"))
from config import get_solver_config
from coverage_mapper import CoverageMapper


def count_tests(solver: str, build_dir: str = "build", test_dir: str = None,
                solver_dir: str = None) -> dict:
    """Count tests for a solver using config-driven discovery.

    Args:
        solver: Solver name (reads config from solver.json)
        build_dir: Path to build directory (used for ctest discovery)
        test_dir: Path to test directory (used for filesystem discovery)
        solver_dir: Solver git repo directory for commit hash (defaults to build_dir parent)
    """
    config = get_solver_config(solver)
    cov_config = config.get("coverage", {})

    mapper = CoverageMapper(solver, build_dir=build_dir, test_dir=test_dir)
    all_tests = mapper.get_tests()

    # Apply skip_tests filter from config
    skip_tests = set(cov_config.get("skip_tests", []))
    if skip_tests:
        tests = [t for t in all_tests if t[1] not in skip_tests]
    else:
        tests = all_tests

    # Determine solver repo directory for commit hash
    if solver_dir:
        repo_dir = Path(solver_dir)
    elif build_dir:
        repo_dir = Path(build_dir).parent
    else:
        repo_dir = Path(solver)

    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )
        commit_hash = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"Warning: Failed to get commit hash from {repo_dir}", file=sys.stderr)
        commit_hash = "unknown"

    return {
        'test_count': len(tests),
        'commit_hash': commit_hash,
        'solver_version': 'main'
    }


def main():
    parser = argparse.ArgumentParser(description='Count tests for a solver')
    parser.add_argument('--solver', required=True, help='Solver name (reads config from solver.json)')
    parser.add_argument('--build-dir', default='build', help='Path to build directory')
    parser.add_argument('--test-dir', help='Path to test directory (for filesystem test type)')
    parser.add_argument('--solver-dir', help='Solver git repo directory (for commit hash; defaults to build-dir parent)')
    parser.add_argument('--output', type=Path, help='Output JSON file (prints to stdout if not specified)')

    args = parser.parse_args()

    print(f"Counting {args.solver} tests...", file=sys.stderr)

    result = count_tests(
        solver=args.solver,
        build_dir=args.build_dir,
        test_dir=args.test_dir,
        solver_dir=args.solver_dir
    )

    print(f"Found {result['test_count']} tests at commit {result['commit_hash'][:8]}", file=sys.stderr)

    output_json = json.dumps(result, indent=2)

    if args.output:
        args.output.write_text(output_json)
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    return 0


if __name__ == '__main__':
    sys.exit(main())
