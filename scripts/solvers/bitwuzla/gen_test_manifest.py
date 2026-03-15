#!/usr/bin/env python3
"""
Bitwuzla test manifest generator.
Parses test/regress/meson.build list-of-lists.

Usage: gen_test_manifest.py <bitwuzla_test_regress_dir>
  Where <bitwuzla_test_regress_dir> is the path to test/regress/ within bitwuzla.
Output: [{"file": "solver/bv/test.smt2", "flags": ["--bv-solver=prop"]}]
  (paths relative to test/regress/)
"""
import json
import re
import sys
from pathlib import Path


def parse_meson_tests(meson_file: Path) -> list:
    """Parse test entries from bitwuzla's test/regress/meson.build.

    Each entry is a 2-element meson list: ['path.smt2', ['--flag1', '--flag2']]
    """
    text = meson_file.read_text()

    # Find each test entry: ['some/path.smt2'] or ['some/path.smt2', [...flags...]]
    # Flags list is optional — entries without flags omit the second element.
    entry_pattern = re.compile(
        r"\[\s*'([^']+\.smt2?)'\s*(?:,\s*(\[[^\]]*\]))?\s*\]",
        re.DOTALL
    )

    entries = []
    for match in entry_pattern.finditer(text):
        file_path = match.group(1)
        flags_raw = match.group(2)
        flags = re.findall(r"'([^']+)'", flags_raw) if flags_raw is not None else []
        entries.append({"file": file_path, "flags": flags})

    return entries


def main():
    if len(sys.argv) < 2:
        print("Usage: gen_test_manifest.py <bitwuzla_test_regress_dir>", file=sys.stderr)
        sys.exit(1)

    test_regress_dir = Path(sys.argv[1])
    meson_file = test_regress_dir / "meson.build"

    if not meson_file.exists():
        print(f"Error: meson.build not found: {meson_file}", file=sys.stderr)
        sys.exit(1)

    entries = parse_meson_tests(meson_file)
    print(json.dumps(entries))


if __name__ == "__main__":
    main()
