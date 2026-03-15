#!/usr/bin/env python3
"""
Z3 test manifest generator.
Walks z3test/regressions/**/*.smt* and emits JSON to stdout.

Usage: gen_test_manifest.py <z3test_dir>
Output: [{"file": "regressions/sub/test.smt2", "flags": []}]
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: gen_test_manifest.py <z3test_dir>", file=sys.stderr)
        sys.exit(1)

    base_dir = Path(sys.argv[1])
    regress_dir = base_dir / "regressions"

    if not regress_dir.exists():
        print(f"Error: regressions directory not found: {regress_dir}", file=sys.stderr)
        sys.exit(1)

    entries = []
    for test_file in sorted(regress_dir.rglob("*.smt*")):
        if test_file.name.endswith(".disabled"):
            continue
        rel_path = test_file.relative_to(base_dir)
        entries.append({"file": str(rel_path), "flags": []})

    print(json.dumps(entries))


if __name__ == "__main__":
    main()
