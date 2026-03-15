#!/usr/bin/env python3
"""
CVC5 test manifest generator.
Parses CMakeLists.txt in the test/regress/cli/ directory.

Usage: gen_test_manifest.py <cvc5_test_regress_cli_dir>
  Where <cvc5_test_regress_cli_dir> is the path to test/regress/cli/ within cvc5 source.
Output: [{"file": "arith/add.smt2", "flags": []}]
  (paths relative to test/regress/cli/)
"""
import json
import re
import sys
from pathlib import Path


def parse_cmake_test_lists(cmake_file: Path) -> list:
    """Extract .smt2 file paths from set(...) blocks in a CMakeLists.txt."""
    text = cmake_file.read_text()
    # Match lines that look like smt2 file paths (word chars, slashes, dots)
    return sorted(set(re.findall(r'[\w./][\w./-]*\.smt2?', text)))


def main():
    if len(sys.argv) < 2:
        print("Usage: gen_test_manifest.py <cvc5_test_regress_cli_dir>", file=sys.stderr)
        sys.exit(1)

    source_dir = Path(sys.argv[1])
    cmake_file = source_dir / "CMakeLists.txt"

    if not cmake_file.exists():
        print(f"Error: CMakeLists.txt not found: {cmake_file}", file=sys.stderr)
        sys.exit(1)

    test_files = parse_cmake_test_lists(cmake_file)
    entries = [{"file": f, "flags": []} for f in test_files]
    print(json.dumps(entries))


if __name__ == "__main__":
    main()
