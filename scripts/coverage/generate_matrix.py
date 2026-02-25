#!/usr/bin/env python3
"""
Unified dynamic matrix generator for coverage mapping jobs.
Uses solver config to determine test discovery method, defaults, and filtering.
"""

import json
import math
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scheduling"))
from config import get_solver_config
from coverage_mapper import CoverageMapper


def filter_tests(tests: list, skip_tests: set) -> list:
    """Filter tests using skip list from solver config, re-indexing after removal."""
    filtered = [(i + 1, name) for i, (_, name) in enumerate(t for t in tests if t[1] not in skip_tests)]
    return filtered


def generate_matrix(solver: str, build_dir: str = "build", test_dir: str = None,
                    target_jobs: int = None):
    """Generate dynamic matrix for coverage mapping jobs."""
    config = get_solver_config(solver)
    cov_config = config.get("coverage", {})

    if target_jobs is None:
        target_jobs = cov_config.get("target_jobs", 4)

    mapper = CoverageMapper(solver, build_dir=build_dir, test_dir=test_dir)
    all_tests = mapper.get_tests()

    if not all_tests:
        print("No tests found", file=sys.stderr)
        return {'matrix': {'include': []}, 'total_tests': 0, 'total_jobs': 0}

    # Filter using skip_tests from config
    skip_tests = set(cov_config.get("skip_tests", []))
    if skip_tests:
        tests = filter_tests(all_tests, skip_tests)
        if not tests:
            print("No tests remaining after filtering", file=sys.stderr)
            return {'matrix': {'include': []}, 'total_tests': 0, 'total_jobs': 0}
    else:
        tests = all_tests

    total_tests = len(tests)
    total_jobs = min(target_jobs, total_tests)
    tests_per_job = math.ceil(total_tests / total_jobs)

    print(f"Found {total_tests} tests", file=sys.stderr)
    print(f"Total jobs: {total_jobs}, Tests per job: {tests_per_job}", file=sys.stderr)

    matrix_entries = []
    for job_id in range(1, total_jobs + 1):
        start_index = (job_id - 1) * tests_per_job + 1
        end_index = min(job_id * tests_per_job, total_tests)
        matrix_entries.append({
            'job_name': f"{solver}-part{job_id}",
            'start_index': start_index,
            'end_index': end_index
        })

    return {
        'matrix': {'include': matrix_entries},
        'total_tests': total_tests,
        'total_jobs': total_jobs,
        'tests_per_job': tests_per_job
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate dynamic matrix for coverage mapping')
    parser.add_argument('--solver', required=True, help='Solver name (reads config from solver.json)')
    parser.add_argument('--build-dir', default='build', help='Path to build directory')
    parser.add_argument('--test-dir', help='Path to test directory (required for filesystem test type)')
    parser.add_argument('--target-jobs', type=int, help='Target number of parallel jobs (default: from config)')
    parser.add_argument('--output', default='matrix.json', help='Output JSON file')

    args = parser.parse_args()

    result = generate_matrix(
        solver=args.solver,
        build_dir=args.build_dir,
        test_dir=args.test_dir,
        target_jobs=args.target_jobs
    )

    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"Matrix written to {args.output}")
    print(f"Total tests: {result['total_tests']}, Total jobs: {result['total_jobs']}")
