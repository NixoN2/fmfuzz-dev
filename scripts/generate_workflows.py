#!/usr/bin/env python3
"""Generate per-solver GitHub Actions dispatcher workflows.

Reads solver names from scripts/solvers/*/solver.json and generates
the thin dispatcher workflow files that call the reusable templates.

Usage:
    python3 scripts/generate_workflows.py          # preview changes
    python3 scripts/generate_workflows.py --write   # write files to disk
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SOLVERS_DIR = REPO_ROOT / "scripts" / "solvers"

# ── Templates ───────────────────────────────────────────────────────────

TEMPLATES = {
    "{solver}.yml": """\
name: Build {name}

on:
  workflow_dispatch:
    inputs:
      commit_hash:
        description: 'Optional: {name} commit hash to add to build queue'
        required: false
        type: string
  schedule:
    - cron: '5,35 * * * *'

jobs:
  build:
    uses: ./.github/workflows/build.yml
    with:
      solver: {solver}
      commit_hash: ${{{{ inputs.commit_hash || '' }}}}
    secrets: inherit
""",
    "{solver}-manager.yml": """\
name: {name} Manager

on:
  workflow_dispatch:
    inputs:
      commit_hash:
        description: 'Optional: Commit hash to add to build queue'
        required: false
        type: string
  schedule:
    - cron: '*/15 * * * *'

jobs:
  manager:
    uses: ./.github/workflows/manager.yml
    with:
      solver: {solver}
      commit_hash: ${{{{ inputs.commit_hash || '' }}}}
    secrets: inherit
""",
    "{solver}-commit-fuzzer.yml": """\
name: {name} Commit Fuzzer

on:
  workflow_dispatch:
    inputs:
      commit_hash:
        description: 'Optional: {name} commit hash to analyze (defaults to HEAD)'
        required: false
        type: string
      coverage_commit_hash:
        description: 'Optional: {name} commit hash for coverage mapping (defaults to latest in S3)'
        required: false
        type: string
      stop_buffer_minutes:
        description: 'Optional: Minutes before timeout to stop fuzzing (default: 5)'
        required: false
        type: number
        default: 5
  schedule:
    - cron: '45 0,6,12,18 * * *'

jobs:
  fuzzer:
    uses: ./.github/workflows/commit-fuzzer.yml
    with:
      solver: {solver}
      commit_hash: ${{{{ inputs.commit_hash || '' }}}}
      coverage_commit_hash: ${{{{ inputs.coverage_commit_hash || '' }}}}
      stop_buffer_minutes: ${{{{ inputs.stop_buffer_minutes || 5 }}}}
    secrets: inherit
""",
    "{solver}-coverage-mapper.yml": """\
name: {name} Coverage Mapper

on:
  workflow_call:
    inputs:
      commit_hash:
        required: false
        type: string
      test_count:
        required: false
        type: number
    secrets:
      AWS_ACCESS_KEY_ID:
        required: true
      AWS_SECRET_ACCESS_KEY:
        required: true
      AWS_REGION:
        required: true
      AWS_S3_BUCKET:
        required: true

  workflow_dispatch:
    inputs:
      commit_hash:
        description: 'Optional: commit hash to build coverage mapping for (defaults to HEAD)'
        required: false
        type: string

  schedule:
    - cron: '0 0 1 * *'

jobs:
  coverage:
    uses: ./.github/workflows/coverage-mapper.yml
    with:
      solver: {solver}
      commit_hash: ${{{{ inputs.commit_hash || '' }}}}
      test_count: ${{{{ inputs.test_count || 0 }}}}
    secrets: inherit
""",
    "{solver}-coverage-daily-check.yml": """\
name: {name} Coverage Daily Check

on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:

jobs:
  check:
    uses: ./.github/workflows/coverage-daily-check.yml
    with:
      solver: {solver}
    secrets: inherit
""",
}


def discover_solvers():
    """Return list of (solver_name, display_name) from solver.json files."""
    solvers = []
    for config_file in sorted(SOLVERS_DIR.glob("*/solver.json")):
        solver_name = config_file.parent.name
        with open(config_file) as f:
            config = json.load(f)
        display_name = config.get("display_name", solver_name.upper())
        solvers.append((solver_name, display_name))
    return solvers


def generate_for_solver(solver, name):
    """Generate all workflow files for a solver. Returns dict of filename -> content."""
    files = {}
    for template_name, template in TEMPLATES.items():
        filename = template_name.format(solver=solver)
        content = template.format(solver=solver, name=name)
        files[filename] = content
    return files


def main():
    parser = argparse.ArgumentParser(description="Generate per-solver dispatcher workflows")
    parser.add_argument("--write", action="store_true", help="Write files to disk (default: preview only)")
    parser.add_argument("--solver", help="Generate for a single solver only")
    args = parser.parse_args()

    solvers = discover_solvers()
    if not solvers:
        print("No solvers found in scripts/solvers/*/solver.json")
        sys.exit(1)

    if args.solver:
        solvers = [(s, n) for s, n in solvers if s == args.solver]
        if not solvers:
            print(f"Solver '{args.solver}' not found")
            sys.exit(1)

    all_files = {}
    for solver, name in solvers:
        all_files.update(generate_for_solver(solver, name))

    if args.write:
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
        for filename, content in sorted(all_files.items()):
            path = WORKFLOWS_DIR / filename
            path.write_text(content)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        print(f"\nGenerated {len(all_files)} workflow files for {len(solvers)} solver(s)")
    else:
        for filename in sorted(all_files.keys()):
            print(f"  {filename}")
        print(f"\nWould generate {len(all_files)} files for {len(solvers)} solver(s)")
        print("Run with --write to write files to disk")


if __name__ == "__main__":
    main()
