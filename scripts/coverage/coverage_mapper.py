#!/usr/bin/env python3
"""
Unified Coverage Mapper
Processes solver tests and extracts per-function coverage data using fastcov.

Supports two test types (configured in solver.json "coverage" section):
  - "filesystem": discovers test files by walking a directory, runs solver binary directly
  - "ctest": discovers tests via ctest --show-only, runs tests via ctest
"""

import sys
import json
import subprocess
import re
import argparse
import time
import random
import gc
import psutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Import solver config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scheduling"))
from config import get_solver_config


class CoverageMapper:
    # Paths that are never solver source — always excluded regardless of config
    SYSTEM_EXCLUDES = [
        '/usr/include/', '/usr/lib/', '/System/', '/Library/',
        '/Applications/', '/opt/', 'CMakeFiles/', 'cmake/', 'Makefile',
    ]

    # Default project-level excludes — overridable via coverage.source_exclude
    DEFAULT_SOURCE_EXCLUDE = [
        '/deps/', '/build/', '/include/', '/lib/',
        '/bin/', '/share/',
    ]

    def __init__(self, solver: str, build_dir: str = "build", test_dir: str = None):
        self.solver = solver
        self.build_dir = Path(build_dir)
        self.test_dir = Path(test_dir) if test_dir else None

        # Load solver config
        self.config = get_solver_config(solver)
        self.cov_config = self.config.get("coverage", {})
        self.test_type = self.cov_config.get("test_type", "ctest")

        # For direct execution: binary path and flags from config
        binary_subpath = self.config.get("artifacts", {}).get("binary_subpath", solver)
        self.solver_binary = self.build_dir / binary_subpath
        self.solver_test_flags = self.config.get("solver_flags", "").split()

        # Skip tests list from config
        self.skip_tests = set(self.cov_config.get("skip_tests", []))

        # Filesystem test discovery config
        self.test_subdir = self.cov_config.get("test_subdir", "regressions")
        self.test_glob = self.cov_config.get("test_glob", "*.smt*")

        # Per-test timeout from config
        self.test_timeout = self.cov_config.get("test_timeout", 120)

        # Source file filtering from config
        self.source_include = self.cov_config.get("source_include", ["src/"])
        self.source_exclude = self.cov_config.get("source_exclude", self.DEFAULT_SOURCE_EXCLUDE)

        # Pre-compile regex for ctest output parsing
        self.test_regex = re.compile(r'Test\s+#(\d+):\s*(.+)')
        # Cache for demangled names
        self.demangle_cache = {}
        # Memory monitoring
        self.max_memory_mb = 10000  # 10GB limit
        self.memory_check_interval = 50

    def demangle_function_name(self, mangled_name: str) -> str:
        """Demangle C++ function names using c++filt with caching"""
        if mangled_name in self.demangle_cache:
            return self.demangle_cache[mangled_name]

        try:
            result = subprocess.run(['c++filt', mangled_name], capture_output=True, text=True)
            demangled = result.stdout.strip() if result.returncode == 0 else mangled_name
            self.demangle_cache[mangled_name] = demangled
            return demangled
        except FileNotFoundError:
            self.demangle_cache[mangled_name] = mangled_name
            return mangled_name

    def simplify_file_path(self, file_path: str) -> str:
        """Simplify file path to show only the relevant project path starting from source root."""
        for pattern in self.source_include:
            if pattern in file_path:
                parts = file_path.split(pattern)
                if len(parts) > 1:
                    return pattern + parts[-1]
        return file_path

    def get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0

    def check_memory_limit(self) -> bool:
        """Check if memory usage is within limits"""
        memory_mb = self.get_memory_usage_mb()
        if memory_mb > self.max_memory_mb:
            print(f"Warning: Memory limit exceeded: {memory_mb:.1f}MB > {self.max_memory_mb}MB")
            return False
        return True

    def cleanup_memory(self):
        """Force garbage collection and clear caches"""
        if len(self.demangle_cache) > 1000:
            self.demangle_cache.clear()
        gc.collect()

    def write_intermediate_mapping(self, function_to_tests: Dict, output_file: Path):
        """Write intermediate mapping to disk to save memory"""
        with open(output_file, 'w') as f:
            json.dump(function_to_tests, f, separators=(',', ':'))

    # ── Test discovery ──────────────────────────────────────────────────

    def get_tests(self) -> List[Tuple[int, str]]:
        """Get tests using config-driven discovery method"""
        if self.test_type == "filesystem":
            return self._get_filesystem_tests()
        elif self.test_type == "ctest":
            return self._get_ctest_tests()
        else:
            print(f"Error: unsupported test_type '{self.test_type}' in coverage config")
            sys.stdout.flush()
            return []

    def _get_filesystem_tests(self) -> List[Tuple[int, str]]:
        """Get test files by walking a directory (e.g. z3test/regressions/*.smt*)"""
        try:
            if not self.test_dir or not self.test_dir.exists():
                print(f"Error: test directory not found: {self.test_dir}")
                sys.stdout.flush()
                return []

            subdir = self.test_dir / self.test_subdir
            if not subdir.exists():
                print(f"Error: test subdirectory not found: {subdir}")
                sys.stdout.flush()
                return []

            tests = []
            for test_file in subdir.rglob(self.test_glob):
                if test_file.name.endswith('.disabled'):
                    continue
                rel_path = test_file.relative_to(self.test_dir)
                tests.append(str(rel_path))

            tests = sorted(tests)
            indexed_tests = [(i + 1, test) for i, test in enumerate(tests)]

            print(f"Found {len(indexed_tests)} filesystem tests")
            sys.stdout.flush()
            return indexed_tests

        except Exception as e:
            print(f"Error discovering filesystem tests: {e}")
            sys.stdout.flush()
            return []

    def _get_ctest_tests(self) -> List[Tuple[int, str]]:
        """Get tests from ctest --show-only"""
        try:
            result = subprocess.run(["ctest", "--show-only"], cwd=self.build_dir,
                                    capture_output=True, text=True)

            if result.returncode != 0:
                print(f"Error running ctest --show-only: {result.stderr}")
                sys.stdout.flush()
                return []

            tests = []
            for line in result.stdout.split('\n'):
                match = self.test_regex.match(line.strip())
                if match:
                    tests.append((int(match.group(1)), match.group(2)))

            print(f"Found {len(tests)} ctest tests")
            sys.stdout.flush()
            return tests

        except Exception as e:
            print(f"Error discovering ctest tests: {e}")
            sys.stdout.flush()
            return []

    # ── Single test execution ───────────────────────────────────────────

    def process_single_test(self, test_info: Tuple[int, str]) -> Optional[Dict]:
        """Process a single test using config-driven execution method"""
        if self.test_type == "filesystem":
            return self._process_direct_test(test_info)
        elif self.test_type == "ctest":
            return self._process_ctest_test(test_info)
        else:
            return None

    def _process_direct_test(self, test_info: Tuple[int, str]) -> Optional[Dict]:
        """Run solver binary directly on a test file and extract coverage"""
        test_id, test_name = test_info

        if test_name in self.skip_tests:
            print(f"  {test_name} - skipped (in skip list)")
            sys.stdout.flush()
            return None

        try:
            for gcda in self.build_dir.rglob("*.gcda"):
                gcda.unlink()
            self.reset_coverage_counters()

            test_file = self.test_dir / test_name
            if not test_file.exists():
                print(f"  {test_name} - test file not found")
                sys.stdout.flush()
                return None

            cmd = [str(self.solver_binary)] + self.solver_test_flags + [str(test_file)]

            start_time = time.time()
            try:
                result = subprocess.run(
                    cmd, cwd=self.build_dir,
                    capture_output=True, text=True, check=False,
                    timeout=self.test_timeout
                )
            except subprocess.TimeoutExpired:
                print(f"  {test_name} - timeout after {self.test_timeout}s (skipping)")
                sys.stdout.flush()
                return None

            end_time = time.time()
            execution_time = round(end_time - start_time, 2)

            if result.returncode != 0:
                print(f"  {test_name} - failed (exit {result.returncode}) - {execution_time}s")
                sys.stdout.flush()
                return None

            coverage_data = self.extract_coverage_data(test_name)
            if coverage_data:
                print(f"  {test_name} - {len(coverage_data['functions'])} functions - {execution_time}s")
            else:
                print(f"  {test_name} - no coverage data - {execution_time}s")
            sys.stdout.flush()
            self.cleanup_memory()
            return coverage_data

        except Exception as e:
            print(f"  {test_name} - unexpected error: {e} (skipping)")
            sys.stdout.flush()
            return None

    def _process_ctest_test(self, test_info: Tuple[int, str]) -> Optional[Dict]:
        """Run a test via ctest and extract coverage"""
        test_id, test_name = test_info

        try:
            for gcda in self.build_dir.rglob("*.gcda"):
                gcda.unlink()
            self.reset_coverage_counters()

            start_time = time.time()

            try:
                result = subprocess.run(
                    ["ctest", "-I", f"{test_id},{test_id}", "-j4", "--output-on-failure"],
                    cwd=self.build_dir, capture_output=True, text=True, check=False,
                    timeout=self.test_timeout
                )
            except subprocess.TimeoutExpired:
                print(f"  {test_name} - timeout after {self.test_timeout}s (skipping)")
                sys.stdout.flush()
                return None

            end_time = time.time()
            execution_time = round(end_time - start_time, 2)

            if result.returncode != 0:
                print(f"  {test_name} - failed - {execution_time}s")
                return None

            coverage_data = self.extract_coverage_data(test_name)
            if coverage_data:
                print(f"  {test_name} - {len(coverage_data['functions'])} functions - {execution_time}s")
            else:
                print(f"  {test_name} - no coverage data - {execution_time}s")
            sys.stdout.flush()
            self.cleanup_memory()
            return coverage_data

        except Exception as e:
            print(f"  {test_name} - unexpected error: {e} (skipping)")
            sys.stdout.flush()
            return None

    # ── Coverage extraction ─────────────────────────────────────────────

    def extract_coverage_data(self, test_name: str) -> Optional[Dict]:
        """Extract coverage data using fastcov"""
        safe_name = test_name.replace('/', '_').replace('\\', '_')
        fastcov_output = self.build_dir / f"fastcov_{safe_name}.json"

        result = subprocess.run([
            "fastcov", "--gcov", "gcov", "--search-directory", str(self.build_dir),
            "--output", str(fastcov_output), "--exclude", "/usr/include/*",
            "--exclude", "*/deps/*", "--jobs", "4"
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            return None

        result_data = self.parse_fastcov_json(fastcov_output, test_name)

        try:
            fastcov_output.unlink()
        except Exception:
            pass

        return result_data

    def parse_fastcov_json(self, fastcov_file: Path, test_name: str) -> Optional[Dict]:
        """Parse fastcov JSON file to extract function information"""
        with open(fastcov_file, 'r') as f:
            data = json.load(f)

        functions = set()

        if 'sources' in data:
            for file_path, file_data in data['sources'].items():
                if self.is_source_file(file_path):
                    if '' in file_data and 'functions' in file_data['']:
                        for func_name, func_data in file_data['']['functions'].items():
                            if func_data.get('execution_count', 0) > 0:
                                demangled_name = self.demangle_function_name(func_name)
                                simplified_path = self.simplify_file_path(file_path)
                                line_num = func_data.get('start_line', 0)
                                func_id = f"{simplified_path}:{demangled_name}:{line_num}"
                                functions.add(func_id)

        if not functions:
            return None

        return {
            "test_name": test_name,
            "functions": sorted(list(functions))
        }

    def is_source_file(self, file_path: str) -> bool:
        """Check if a file path belongs to the solver project source.

        Uses coverage.source_include (at least one must match) and
        coverage.source_exclude + SYSTEM_EXCLUDES (none must match).
        """
        if not any(pattern in file_path for pattern in self.source_include):
            return False
        if any(pattern in file_path for pattern in self.SYSTEM_EXCLUDES):
            return False
        if any(pattern in file_path for pattern in self.source_exclude):
            return False
        return True

    def reset_coverage_counters(self):
        """Reset coverage counters using fastcov --zerocounters"""
        subprocess.run([
            "fastcov", "--zerocounters", "--search-directory", str(self.build_dir),
            "--exclude", "/usr/include/*", "--exclude", "*/deps/*"
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)

    # ── Test processing loop ────────────────────────────────────────────

    def process_tests(self, tests: List[Tuple[int, str]], max_runtime_seconds: int = None) -> str:
        """Process tests sequentially with time guard and streaming to disk"""
        print(f"Processing {len(tests)} tests")
        print(f"Memory limit: {self.max_memory_mb}MB")
        if max_runtime_seconds:
            print(f"Max runtime: {max_runtime_seconds // 60}m")
        sys.stdout.flush()

        job_start_time = time.time()
        temp_file = self.build_dir / "coverage_temp.json"
        function_to_tests = {}

        for i, test_info in enumerate(tests, 1):
            # Time guard: stop before hitting the job time limit
            if max_runtime_seconds:
                elapsed = time.time() - job_start_time
                if elapsed >= max_runtime_seconds:
                    print(f"Stopping at test {i}/{len(tests)} - reached time limit ({elapsed / 60:.0f}m)")
                    sys.stdout.flush()
                    break

            test_id, test_name = test_info
            print(f"Test {i}/{len(tests)} (#{test_id}): {test_name}")
            sys.stdout.flush()

            if i % self.memory_check_interval == 0:
                if not self.check_memory_limit():
                    print(f"Stopping at test {i} due to memory limit")
                    sys.stdout.flush()
                    break
                self.cleanup_memory()
                memory_mb = self.get_memory_usage_mb()
                print(f"Memory usage: {memory_mb:.1f}MB")
                sys.stdout.flush()

            try:
                result = self.process_single_test(test_info)
                if result:
                    test_name = result["test_name"]
                    for func in result["functions"]:
                        if func not in function_to_tests:
                            function_to_tests[func] = []
                        function_to_tests[func].append(test_name)

                    if i % 100 == 0:
                        self.write_intermediate_mapping(function_to_tests, temp_file)
            except Exception as e:
                print(f"  {test_name} - unexpected error: {e} (skipping)")
                sys.stdout.flush()
                continue

        self.write_intermediate_mapping(function_to_tests, temp_file)
        return str(temp_file)

    def run(self, max_tests: int = None, test_pattern: str = None,
            start_index: int = None, end_index: int = None,
            max_runtime_minutes: int = None):
        """Main execution method"""
        print(f"Discovering {self.solver} tests (type: {self.test_type})...")
        sys.stdout.flush()
        tests = self.get_tests()

        if not tests:
            print("No tests found")
            sys.stdout.flush()
            return

        if test_pattern:
            tests = [t for t in tests if test_pattern in t[1]]
            print(f"Filtered to {len(tests)} tests matching pattern: {test_pattern}")
            sys.stdout.flush()

        # Shuffle with fixed seed so long tests are spread evenly across jobs
        # (all jobs get the same shuffled order since they start from the same sorted list)
        random.seed(42)
        random.shuffle(tests)

        # Handle test range selection (1-based indexing)
        if start_index is not None and end_index is not None:
            start_idx = max(0, start_index - 1)
            end_idx = min(len(tests), end_index)
            tests = tests[start_idx:end_idx]
            print(f"Selected tests {start_index}-{end_index}: {len(tests)} tests")
            sys.stdout.flush()
        elif max_tests:
            tests = tests[:max_tests]
            print(f"Limited to {len(tests)} tests")
            sys.stdout.flush()

        max_runtime_seconds = max_runtime_minutes * 60 if max_runtime_minutes else None
        temp_file = self.process_tests(tests, max_runtime_seconds=max_runtime_seconds)

        if not temp_file or not Path(temp_file).exists():
            print("No coverage data generated")
            sys.stdout.flush()
            return

        output_file = f"coverage_mapping_{start_index}_{end_index}.json" if start_index is not None else "coverage_mapping.json"
        Path(temp_file).rename(output_file)

        with open(output_file, 'r') as f:
            coverage_mapping = json.load(f)

        print(f"Coverage mapping saved to {output_file}")
        print(f"Total functions: {len(coverage_mapping)}")
        print(f"Total tests: {len(tests)}")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description='Unified Coverage Mapper')
    parser.add_argument('--solver', required=True, help='Solver name (reads config from solver.json)')
    parser.add_argument('--build-dir', default='build', help='Build directory path')
    parser.add_argument('--test-dir', help='Test directory path (required for filesystem test type)')
    parser.add_argument('--max-tests', type=int, help='Maximum number of tests to process')
    parser.add_argument('--test-pattern', help='Filter tests by pattern')
    parser.add_argument('--start-index', type=int, help='Start index for test range (1-based)')
    parser.add_argument('--end-index', type=int, help='End index for test range (1-based, inclusive)')
    parser.add_argument('--max-runtime', type=int, default=350,
                        help='Max runtime in minutes before stopping gracefully (default: 350 = 5h50m)')

    args = parser.parse_args()

    try:
        mapper = CoverageMapper(args.solver, args.build_dir, args.test_dir)
        mapper.run(max_tests=args.max_tests, test_pattern=args.test_pattern,
                   start_index=args.start_index, end_index=args.end_index,
                   max_runtime_minutes=args.max_runtime)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.stdout.flush()
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.stdout.flush()
    # Always exit with code 0 to prevent GitHub Actions from stopping
    sys.exit(0)


if __name__ == "__main__":
    main()
