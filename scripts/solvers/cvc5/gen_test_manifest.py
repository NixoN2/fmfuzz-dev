#!/usr/bin/env python3
"""
CVC5 test manifest generator.
Parses CMakeLists.txt in the test/regress/cli/ directory, then reads each
test file for embedded '; COMMAND-LINE:' directives to emit per-run flags.

Usage: gen_test_manifest.py <cvc5_test_regress_cli_dir>
  Where <cvc5_test_regress_cli_dir> is the path to test/regress/cli/ within cvc5 source.
Output: [{"file": "arith/add.smt2", "flags": ["-q"]}]
  (paths relative to test/regress/cli/; one entry per COMMAND-LINE directive)
"""
import json
import re
import shlex
import sys
from pathlib import Path


def parse_cmake_test_lists(cmake_file: Path) -> list:
    """Extract .smt2 file paths from set(...) blocks in a CMakeLists.txt."""
    text = cmake_file.read_text()
    # Match indented lines whose sole token ends with .smt2/.smt.
    # \S+ captures the full filename including TPTP-style chars like '=', '+', '^'.
    return sorted(set(re.findall(r'^\s+(\S+\.smt2?)\s*$', text, re.MULTILINE)))


def get_command_line_flag_sets(smt2_file: Path) -> list:
    """Read '; COMMAND-LINE: <flags>' directives from an smt2 file.

    Returns a list of flag-lists, one per non-empty directive.
    If no non-empty directive exists, returns [[]] (one run with no extra flags).
    """
    if not smt2_file.exists():
        return [[]]
    flag_sets = []
    for line in smt2_file.read_text(errors="replace").splitlines():
        m = re.match(r"^\s*;\s*COMMAND-LINE:\s*(.*)", line)
        if m:
            raw = m.group(1).strip()
            if raw:
                flag_sets.append(shlex.split(raw))
    return flag_sets if flag_sets else [[]]


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
    entries = []
    for f in test_files:
        for flags in get_command_line_flag_sets(source_dir / f):
            entries.append({"file": f, "flags": flags})
    print(json.dumps(entries))


if __name__ == "__main__":
    main()
