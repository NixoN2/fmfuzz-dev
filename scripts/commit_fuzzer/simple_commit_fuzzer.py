#!/usr/bin/env python3

import argparse
import importlib
import json
import multiprocessing
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import psutil

# Add scripts/ to path so we can import scheduling.config and fuzzers.*
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "fuzzers"))

from resource_monitor import ResourceMonitor
from scheduling.config import (
    get_fuzzer_name,
    get_fuzzer_params,
    get_oracle_name,
    get_solver_config,
)


class SimpleCommitFuzzer:

    def __init__(
        self,
        tests: List[str],
        tests_root: str,
        solver_name: str,
        oracle_name: str,
        fuzzer_name: str,
        bugs_folder: str = "bugs",
        num_workers: int = 4,
        fuzzer_params: Optional[dict] = None,
        time_remaining: Optional[int] = None,
        job_start_time: Optional[float] = None,
        stop_buffer_minutes: int = 5,
        job_id: Optional[str] = None,
    ):
        self.tests = tests
        self.tests_root = Path(tests_root)
        self.bugs_folder = Path(bugs_folder)
        self.fuzzer_params = fuzzer_params or {}
        self.job_id = job_id
        self.start_time = time.time()

        self.solver_name = solver_name
        self.oracle_name = oracle_name
        self.fuzzer_name = fuzzer_name

        self.solver_config = get_solver_config(solver_name)
        self.oracle_config = get_solver_config(oracle_name)

        self.FuzzerClass = importlib.import_module(f"{fuzzer_name}.fuzzer").Fuzzer

        self.solver_binary = Path(self.solver_config["binary_path"])
        self.solver_flags = self.solver_config.get("solver_flags", "")
        self.oracle_binary = Path(self.oracle_config["binary_path"])
        self.oracle_flags = self.oracle_config.get("solver_flags", "")
        self.solver_cli = f"{self.solver_binary} {self.solver_flags}".strip()
        self.oracle_cli = f"{self.oracle_binary} {self.oracle_flags}".strip()

        try:
            self.cpu_count = psutil.cpu_count()
        except Exception:
            self.cpu_count = 4

        self.num_workers = min(num_workers, self.cpu_count) if num_workers > 0 else self.cpu_count
        if num_workers > self.cpu_count:
            print(f"[WARN] Requested {num_workers} workers but only {self.cpu_count} CPU cores available, using {self.num_workers}", file=sys.stderr)

        if job_start_time is not None:
            self.time_remaining = self._compute_time_remaining(job_start_time, stop_buffer_minutes)
        elif time_remaining is not None:
            self.time_remaining = time_remaining
        else:
            self.time_remaining = None

        self._validate_solvers()
        self.bugs_folder.mkdir(parents=True, exist_ok=True)

        self.test_queue = multiprocessing.Queue()
        self.shutdown_event = multiprocessing.Event()
        self.stats = multiprocessing.Manager().dict({
            'tests_processed': 0,
            'bugs_found': 0,
            'tests_removed_unsupported': 0,
            'tests_removed_timeout': 0,
            'tests_requeued': 0,
        })

        self.monitor = ResourceMonitor(
            shutdown_event=self.shutdown_event,
            stats=self.stats,
            bugs_folder=self.bugs_folder,
            bug_patterns=self.FuzzerClass.DEFAULT_BUG_PATTERNS,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_solvers(self):
        for name, binary in [(self.solver_name, self.solver_binary), (self.oracle_name, self.oracle_binary)]:
            if not binary.exists() and not shutil.which(str(binary)):
                raise ValueError(f"{name} not found at: {binary} (also not in PATH)")

    def _compute_time_remaining(self, job_start_time: float, stop_buffer_minutes: int) -> int:
        GITHUB_TIMEOUT = 21600
        MIN_REMAINING = 600
        build_time = self.start_time - job_start_time
        remaining = GITHUB_TIMEOUT - build_time - stop_buffer_minutes * 60
        if remaining < MIN_REMAINING:
            print(f"[DEBUG] Computed remaining time ({remaining}s) < minimum ({MIN_REMAINING}s), using {MIN_REMAINING}s")
            remaining = MIN_REMAINING
        return int(remaining)

    def _get_time_remaining(self) -> float:
        if self.time_remaining is None:
            return float('inf')
        return max(0.0, self.time_remaining - (time.time() - self.start_time))

    def _is_time_expired(self) -> bool:
        return self.time_remaining is not None and self._get_time_remaining() <= 0

    def _collect_bug_files(self, folder: Path) -> List[Path]:
        if not folder.exists():
            return []
        return [f for pattern in self.FuzzerClass.DEFAULT_BUG_PATTERNS for f in folder.glob(pattern)]

    # ------------------------------------------------------------------
    # Per-test execution
    # ------------------------------------------------------------------

    def _run_fuzzer(
        self,
        test_name: str,
        worker_id: int,
        per_test_timeout: Optional[float] = None,
    ) -> Tuple[bool, List[Path], float, str]:
        test_path = self.tests_root / test_name
        if not test_path.exists():
            print(f"[WORKER {worker_id}] Error: Test file not found: {test_path}", file=sys.stderr)
            return (False, [], 0.0, 'continue')

        print(f"[WORKER {worker_id}] Running {self.fuzzer_name} on: {test_name}" +
              (f" (timeout: {per_test_timeout}s)" if per_test_timeout else ""))

        run = self.FuzzerClass(
            worker_id=worker_id,
            seed_path=str(test_path),
            solver_cli=self.solver_cli,
            oracle_cli=self.oracle_cli,
            params_override=self.fuzzer_params,
        )
        try:
            exit_code, runtime = run.execute(per_test_timeout)
            bug_files = run.collect()
            bug_found, exit_action = run.parse_result(exit_code)
            return (bug_found, bug_files, runtime, exit_action)
        finally:
            run.cleanup()

    def _handle_result(
        self,
        test_name: str,
        bug_found: bool,
        bug_files: List[Path],
        runtime: float,
        exit_action: str,
        worker_id: int,
    ) -> str:
        if bug_found and bug_files:
            print(f"[WORKER {worker_id}] Found {len(bug_files)} bug(s) on {test_name}")
            self.stats['bugs_found'] += len(bug_files)
            return exit_action

        if exit_action == 'remove':
            print(f"[WORKER {worker_id}] {test_name} — unsupported, removing")
            self.stats['tests_removed_unsupported'] += 1
            return 'remove'

        if exit_action == 'requeue':
            print(f"[WORKER {worker_id}] No bugs on {test_name} (runtime: {runtime:.1f}s) — requeuing")
            return 'requeue'

        return 'continue'

    # ------------------------------------------------------------------
    # Worker process
    # ------------------------------------------------------------------

    def _process_one_test(self, test_name: str, worker_id: int):
        resource_status = self.monitor.check_state()
        if resource_status == 'warning':
            time.sleep(2)
        elif resource_status == 'critical':
            try:
                self.test_queue.put(test_name)
            except Exception:
                pass
            time.sleep(ResourceMonitor.CONFIG['pause_duration'])
            return

        self.monitor.current_tests[worker_id] = test_name
        time_remaining = self._get_time_remaining()
        bug_found, bug_files, runtime, exit_action = self._run_fuzzer(
            test_name,
            worker_id,
            per_test_timeout=time_remaining if self.time_remaining and time_remaining > 0 else None,
        )
        self.monitor.current_tests.pop(worker_id, None)

        action = self._handle_result(test_name, bug_found, bug_files, runtime, exit_action, worker_id)
        if action == 'requeue':
            try:
                self.test_queue.put(test_name)
                self.stats['tests_requeued'] += 1
            except Exception:
                pass
        self.stats['tests_processed'] += 1

    def _worker_process(self, worker_id: int):
        print(f"[WORKER {worker_id}] Started")

        while not self.shutdown_event.is_set():
            try:
                if self.monitor.is_paused():
                    print(f"[WORKER {worker_id}] Paused due to {self.monitor.check_state()} resource usage", file=sys.stderr)
                    time.sleep(ResourceMonitor.CONFIG['pause_duration'])
                    continue

                try:
                    test_name = self.test_queue.get(timeout=1.0)
                except Exception:
                    if self.shutdown_event.is_set() or self._is_time_expired():
                        break
                    continue

                if self._is_time_expired():
                    try:
                        self.test_queue.put(test_name)
                    except Exception:
                        pass
                    break

                self._process_one_test(test_name, worker_id)

            except Exception as e:
                print(f"[WORKER {worker_id}] Error: {e}", file=sys.stderr)
                continue

        print(f"[WORKER {worker_id}] Stopped")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self):
        if not self.tests:
            print(f"No tests provided{' for job ' + self.job_id if self.job_id else ''}")
            return

        print(f"Running {self.fuzzer_name} fuzzer on {len(self.tests)} test(s){' for job ' + self.job_id if self.job_id else ''}")
        print(f"Solver: {self.solver_name} ({self.solver_cli})")
        print(f"Oracle: {self.oracle_name} ({self.oracle_cli})")
        print(f"Tests root: {self.tests_root}")
        print(f"Timeout: {self.time_remaining}s ({self.time_remaining // 60}m)" if self.time_remaining else "No timeout")
        if self.fuzzer_params:
            print(f"Fuzzer params: {', '.join(f'{k}={v}' for k, v in self.fuzzer_params.items())}")
        print(f"Workers: {self.num_workers} / {self.cpu_count} cores")
        print()

        for test in self.tests:
            self.test_queue.put(test)

        workers = [
            multiprocessing.Process(target=self._worker_process, args=(wid,))
            for wid in range(1, self.num_workers + 1)
        ]
        for w in workers:
            w.start()

        self.monitor.workers = workers
        self.monitor.start()

        def _on_signal(signum, frame):
            print("\nShutdown signal received, stopping workers...")
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        try:
            if self.time_remaining:
                end_time = self.start_time + self.time_remaining
                while time.time() < end_time and any(w.is_alive() for w in workers):
                    time.sleep(1)
                if time.time() >= end_time:
                    print("Timeout reached, stopping workers...")
                    self.shutdown_event.set()
            else:
                for w in workers:
                    w.join()
        except KeyboardInterrupt:
            print("\nInterrupted, stopping workers...")
            self.shutdown_event.set()

        for w in workers:
            w.join(timeout=5)
            if w.is_alive():
                print(f"Warning: Worker {getattr(w, 'pid', '?')} did not terminate, killing...")
                w.terminate()
                w.join(timeout=2)
                if w.is_alive():
                    w.kill()

        # Consolidate bugs from worker output dirs into bugs_folder
        for subdir in sorted(self.bugs_folder.glob("*/")):
            for bug_file in self._collect_bug_files(subdir):
                try:
                    dest = self.bugs_folder / bug_file.name
                    if dest.exists():
                        dest = self.bugs_folder / f"{bug_file.stem}_{int(time.time())}{bug_file.suffix}"
                    shutil.move(str(bug_file), str(dest))
                except Exception:
                    pass

        self._print_summary()

    def _print_summary(self):
        print()
        print("=" * 60)
        print(f"FINAL BUG SUMMARY{' FOR JOB ' + self.job_id if self.job_id else ''}")
        print("=" * 60)

        bug_files = self._collect_bug_files(self.bugs_folder)
        if bug_files:
            print(f"\nFound {len(bug_files)} bug(s):")
            for i, bug_file in enumerate(bug_files, 1):
                print(f"\nBug #{i}: {bug_file}")
                print("-" * 60)
                try:
                    print(bug_file.read_text())
                except Exception as e:
                    print(f"Error reading bug file: {e}")
                print("-" * 60)
        else:
            print("No bugs found.")

        print()
        print("Statistics:")
        print(f"  Tests processed:          {self.stats.get('tests_processed', 0)}")
        print(f"  Bugs found:               {self.stats.get('bugs_found', 0)}")
        print(f"  Tests requeued:           {self.stats.get('tests_requeued', 0)}")
        print(f"  Tests removed (unsupported): {self.stats.get('tests_removed_unsupported', 0)}")
        print(f"  Tests removed (timeout):  {self.stats.get('tests_removed_timeout', 0)}")
        print("=" * 60)


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Commit fuzzer that runs configurable fuzzers on SMT solver test suites"
    )
    parser.add_argument("--solver", required=True)
    parser.add_argument("--oracle")
    parser.add_argument("--fuzzer")
    parser.add_argument("--tests-json", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--tests-root")
    parser.add_argument("--time-remaining", type=int)
    parser.add_argument("--job-start-time", type=float)
    parser.add_argument("--stop-buffer-minutes", type=int, default=5)
    parser.add_argument("--fuzzer-param", action="append", metavar="KEY=VALUE")
    parser.add_argument("--bugs-folder", default="bugs")

    try:
        default_workers = psutil.cpu_count()
    except Exception:
        default_workers = 4
    parser.add_argument("--workers", type=int, default=default_workers)

    args = parser.parse_args()

    oracle_name = get_oracle_name(args.solver, args.oracle)
    fuzzer_name = get_fuzzer_name(args.solver, args.fuzzer)

    if args.tests_root:
        tests_root = args.tests_root
    else:
        tests_root = get_solver_config(args.solver).get("test_dir", "test/regress/cli")

    fuzzer_params = get_fuzzer_params(args.solver, fuzzer_name)
    if args.fuzzer_param:
        for item in args.fuzzer_param:
            key, _, value = item.partition("=")
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
            fuzzer_params[key] = value

    try:
        tests = json.loads(args.tests_json)
        if not isinstance(tests, list):
            raise ValueError("--tests-json must be a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        SimpleCommitFuzzer(
            tests=tests,
            tests_root=tests_root,
            solver_name=args.solver,
            oracle_name=oracle_name,
            fuzzer_name=fuzzer_name,
            bugs_folder=args.bugs_folder,
            num_workers=args.workers,
            fuzzer_params=fuzzer_params,
            time_remaining=args.time_remaining,
            job_start_time=args.job_start_time,
            stop_buffer_minutes=args.stop_buffer_minutes,
            job_id=args.job_id,
        ).run()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
