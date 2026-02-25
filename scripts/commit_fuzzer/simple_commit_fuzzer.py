#!/usr/bin/env python3

import argparse
import gc
import importlib
import json
import multiprocessing
import os
import psutil
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Add scripts/ to path so we can import scheduling.config and fuzzers.*
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "fuzzers"))

from scheduling.config import (
    get_solver_config,
    get_fuzzer_config,
    get_oracle_name,
    get_fuzzer_name,
    get_fuzzer_params,
)


class SimpleCommitFuzzer:
    RESOURCE_CONFIG = {
        'cpu_warning': 85.0,
        'cpu_critical': 95.0,
        'memory_warning_available_gb': 2.0,  # Warning if less than 2GB available
        'memory_critical_available_gb': 0.5,  # Critical if less than 500MB available (real low memory)
        'check_interval': 2,  # Check every 2 seconds
        'pause_duration': 10,
        'max_process_memory_mb': 2048,  # Kill processes exceeding 2GB (normal operation)
        'max_process_memory_mb_warning': 1536,  # Stricter threshold (1.5GB) when system memory is low
    }

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

        # Load solver configs
        self.solver_name = solver_name
        self.oracle_name = oracle_name
        self.fuzzer_name = fuzzer_name

        self.solver_config = get_solver_config(solver_name)
        self.oracle_config = get_solver_config(oracle_name)
        self.fuzzer_config = get_fuzzer_config(fuzzer_name)

        # Load fuzzer module
        self.fuzzer_module = importlib.import_module(f"{fuzzer_name}.fuzzer")

        # Build solver paths and CLI strings
        self.solver_binary = Path(self.solver_config["binary_path"])
        self.solver_flags = self.solver_config.get("solver_flags", "")
        self.oracle_binary = Path(self.oracle_config["binary_path"])
        self.oracle_flags = self.oracle_config.get("solver_flags", "")

        try:
            self.cpu_count = psutil.cpu_count()
        except Exception:
            self.cpu_count = 4

        self.num_workers = min(num_workers, self.cpu_count) if num_workers > 0 else self.cpu_count
        if num_workers > self.cpu_count:
            print(f"[WARN] Requested {num_workers} workers but only {self.cpu_count} CPU cores available, using {self.num_workers} workers", file=sys.stderr)

        if job_start_time is not None:
            self.time_remaining = self._compute_time_remaining(job_start_time, stop_buffer_minutes)
        elif time_remaining is not None:
            self.time_remaining = time_remaining
        else:
            self.time_remaining = None

        self._validate_solvers()
        self.bugs_folder.mkdir(parents=True, exist_ok=True)

        self.test_queue = multiprocessing.Queue()
        self.bugs_lock = multiprocessing.Lock()
        self.shutdown_event = multiprocessing.Event()

        self.resource_state = multiprocessing.Manager().dict({
            'status': 'normal',
            'paused': False,
        })
        self.resource_lock = multiprocessing.Lock()

        # Track which test each worker is currently processing (worker_id -> test_name)
        self.current_tests = multiprocessing.Manager().dict()

        self.stats = multiprocessing.Manager().dict({
            'tests_processed': 0,
            'bugs_found': 0,
            'tests_removed_unsupported': 0,
            'tests_removed_timeout': 0,
            'tests_requeued': 0,
        })

    def _validate_solvers(self):
        for name, binary in [(self.solver_name, self.solver_binary), (self.oracle_name, self.oracle_binary)]:
            if not binary.exists() and not shutil.which(str(binary)):
                raise ValueError(f"{name} not found at: {binary} (also not in PATH)")

    def _monitor_resources(self):
        while not self.shutdown_event.is_set():
            try:
                try:
                    cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
                    memory = psutil.virtual_memory()
                    memory_percent = memory.percent
                    memory_available_gb = memory.available / (1024**3)

                    max_cpu = max(cpu_percent) if cpu_percent else 0.0
                    avg_cpu = sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0.0

                    status = 'normal'

                    if (avg_cpu >= self.RESOURCE_CONFIG['cpu_critical'] or
                        memory_available_gb < self.RESOURCE_CONFIG['memory_critical_available_gb']):
                        status = 'critical'
                    elif (avg_cpu >= self.RESOURCE_CONFIG['cpu_warning'] or
                          memory_available_gb < self.RESOURCE_CONFIG['memory_warning_available_gb']):
                        status = 'warning'

                    with self.resource_lock:
                        self.resource_state['status'] = status

                    threshold = (self.RESOURCE_CONFIG['max_process_memory_mb_warning']
                                if memory_available_gb < self.RESOURCE_CONFIG['memory_warning_available_gb']
                                else self.RESOURCE_CONFIG['max_process_memory_mb'])
                    self._kill_high_memory_processes(threshold_mb=threshold)

                    if status == 'critical':
                        self._handle_critical_resources(memory_available_gb)

                except (ImportError, AttributeError) as e:
                    print(f"[WARN] psutil not available, skipping resource monitoring: {e}", file=sys.stderr)
                    break

                time.sleep(self.RESOURCE_CONFIG['check_interval'])
            except Exception as e:
                print(f"[WARN] Error in resource monitoring: {e}", file=sys.stderr)
                time.sleep(self.RESOURCE_CONFIG['check_interval'])

    def _kill_high_memory_processes(self, threshold_mb: Optional[float] = None):
        """Kill processes exceeding RAM threshold using recursive descendant tracking."""
        if threshold_mb is None:
            threshold_mb = self.RESOURCE_CONFIG['max_process_memory_mb']

        HIGH_MEMORY_REPORT_THRESHOLD_MB = 14336

        try:
            main_pid = os.getpid()
            worker_pids = {}
            if hasattr(self, 'workers'):
                for worker_id, w in enumerate(self.workers, start=1):
                    try:
                        worker_pids[w.pid] = worker_id
                    except (AttributeError, ValueError):
                        pass

            pid_to_worker = {}
            tracked_pids = {main_pid}
            tracked_pids.update(worker_pids.keys())
            for pid in list(tracked_pids):
                worker_id = worker_pids.get(pid)
                descendants = self._get_all_descendant_pids(pid)
                tracked_pids.update(descendants)
                if worker_id:
                    for desc_pid in descendants:
                        pid_to_worker[desc_pid] = worker_id

            killed_count = 0
            for pid in tracked_pids:
                try:
                    proc = psutil.Process(pid)
                    rss_mb = proc.memory_info().rss / (1024 * 1024)

                    if rss_mb > threshold_mb:
                        name = proc.name()
                        cmdline = ' '.join(proc.cmdline()[:3])
                        print(f"[RESOURCE] Killing process {pid} ({name}) using {rss_mb:.1f}MB RAM (threshold: {threshold_mb}MB)", file=sys.stderr)
                        print(f"  Command: {cmdline}...", file=sys.stderr)

                        if rss_mb >= HIGH_MEMORY_REPORT_THRESHOLD_MB:
                            worker_id = pid_to_worker.get(pid)
                            if worker_id and worker_id in self.current_tests:
                                test_name = self.current_tests[worker_id]
                                print(f"  HIGH RAM USAGE: Process used {rss_mb:.1f}MB RAM while processing test: {test_name}", file=sys.stderr)
                            else:
                                print(f"  HIGH RAM USAGE: Process used {rss_mb:.1f}MB RAM (could not determine test)", file=sys.stderr)

                        proc.kill()
                        killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, AttributeError):
                    pass

            if killed_count > 0:
                print(f"[RESOURCE] Killed {killed_count} process(es) exceeding {threshold_mb}MB RAM threshold", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Error killing high RAM processes: {e}", file=sys.stderr)

    def _get_all_descendant_pids(self, pid):
        descendant_pids = set()
        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    descendant_pids.add(child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return descendant_pids

    def _handle_critical_resources(self, memory_available_gb: float):
        print(f"[RESOURCE] Critical resource usage detected - RAM available: {memory_available_gb:.2f}GB - taking action", file=sys.stderr)

        if memory_available_gb < self.RESOURCE_CONFIG['memory_critical_available_gb']:
            self._log_bugs_summary_and_stop()
            return

        with self.resource_lock:
            self.resource_state['paused'] = True

        try:
            gc.collect()
        except Exception:
            pass

        time.sleep(self.RESOURCE_CONFIG['pause_duration'])

        with self.resource_lock:
            self.resource_state['paused'] = False

    def _log_bugs_summary_and_stop(self):
        print("\n" + "=" * 60, file=sys.stderr)
        print("CRITICAL RAM DETECTED - STOPPING TO PRESERVE BUGS", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        total_bugs = len(self._collect_bug_files(self.bugs_folder))
        for worker_id in range(1, self.num_workers + 1):
            total_bugs += len(self._collect_bug_files(self.bugs_folder / f"worker_{worker_id}"))

        print(f"  Total bugs found: {total_bugs}", file=sys.stderr)
        print(f"  Tests processed: {self.stats.get('tests_processed', 0)}", file=sys.stderr)
        print("Stopping fuzzer to preserve found bugs...", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)

        self.shutdown_event.set()

    def _check_resource_state(self) -> str:
        with self.resource_lock:
            return self.resource_state.get('status', 'normal')

    def _is_paused(self) -> bool:
        with self.resource_lock:
            return self.resource_state.get('paused', False)

    def _get_solver_clis(self) -> str:
        """Build semicolon-separated solver CLI strings from config."""
        solver_cli = f"{self.solver_binary} {self.solver_flags}".strip()
        oracle_cli = f"{self.oracle_binary} {self.oracle_flags}".strip()
        return ";".join([solver_cli, oracle_cli])

    def _compute_time_remaining(self, job_start_time: float, stop_buffer_minutes: int) -> int:
        GITHUB_TIMEOUT = 21600
        MIN_REMAINING = 600

        build_time = self.start_time - job_start_time
        stop_buffer_seconds = stop_buffer_minutes * 60
        available_time = GITHUB_TIMEOUT - build_time
        remaining = available_time - stop_buffer_seconds

        if remaining < MIN_REMAINING:
            print(f"[DEBUG] Computed remaining time ({remaining}s) is less than minimum ({MIN_REMAINING}s), using {MIN_REMAINING}s")
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
        return list(folder.glob("*.smt2")) + list(folder.glob("*.smt"))

    def _run_fuzzer(
        self,
        test_name: str,
        worker_id: int,
        per_test_timeout: Optional[float] = None,
    ) -> Tuple[bool, List[Path], float, str]:
        """Run the configured fuzzer on a single test.

        Returns:
            (bug_found, bug_files, runtime, exit_action)
        """
        test_path = self.tests_root / test_name
        if not test_path.exists():
            print(f"[WORKER {worker_id}] Error: Test file not found: {test_path}", file=sys.stderr)
            return (False, [], 0.0, 'continue')

        bugs_folder = self.bugs_folder / f"worker_{worker_id}"
        scratch_folder = Path(f"scratch_{worker_id}")
        log_folder = Path(f"logs_{worker_id}")

        for folder in [scratch_folder, log_folder]:
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        bugs_folder.mkdir(parents=True, exist_ok=True)

        solver_clis = self._get_solver_clis()

        cmd = self.fuzzer_module.build_command(
            seed_path=str(test_path),
            solver_clis=solver_clis,
            bugs_dir=str(bugs_folder),
            scratch_dir=str(scratch_folder),
            log_dir=str(log_folder),
            **self.fuzzer_params,
        )

        print(f"[WORKER {worker_id}] Running {self.fuzzer_name} on: {test_name} (timeout: {per_test_timeout}s)" if per_test_timeout else f"[WORKER {worker_id}] Running {self.fuzzer_name} on: {test_name}")

        start_time = time.time()

        try:
            if per_test_timeout and per_test_timeout > 0:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=per_test_timeout)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)

            exit_code = result.returncode
            runtime = time.time() - start_time
            bug_files = self._collect_bug_files(bugs_folder)
            bug_found, exit_action = self.fuzzer_module.parse_result(exit_code, str(bugs_folder))
            return (bug_found, bug_files, runtime, exit_action)

        except subprocess.TimeoutExpired:
            runtime = time.time() - start_time
            return (False, [], runtime, 'continue')
        except Exception:
            runtime = time.time() - start_time
            return (False, [], runtime, 'continue')
        finally:
            for folder in [scratch_folder, log_folder]:
                shutil.rmtree(folder, ignore_errors=True)

    def _handle_fuzzer_result(
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
            with self.bugs_lock:
                for bug_file in bug_files:
                    try:
                        dest = self.bugs_folder / bug_file.name
                        if dest.exists():
                            timestamp = int(time.time())
                            dest = self.bugs_folder / f"{bug_file.stem}_{timestamp}{bug_file.suffix}"
                        shutil.move(str(bug_file), str(dest))
                        self.stats['bugs_found'] += 1
                    except Exception as e:
                        print(f"[WORKER {worker_id}] Warning: Failed to move bug file {bug_file}: {e}", file=sys.stderr)
            return exit_action

        if exit_action == 'remove':
            print(f"[WORKER {worker_id}] {test_name} (unsupported operation - removing)")
            self.stats['tests_removed_unsupported'] += 1
            return 'remove'

        if exit_action == 'requeue':
            print(f"[WORKER {worker_id}] No bugs on {test_name} (runtime: {runtime:.1f}s) - requeuing")
            return 'requeue'

        return 'continue'

    def _worker_process(self, worker_id: int):
        print(f"[WORKER {worker_id}] Started")

        while not self.shutdown_event.is_set():
            try:
                if self._is_paused():
                    resource_status = self._check_resource_state()
                    print(f"[WORKER {worker_id}] Paused due to {resource_status} resource usage", file=sys.stderr)
                    time.sleep(self.RESOURCE_CONFIG['pause_duration'])
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

                resource_status = self._check_resource_state()
                if resource_status == 'warning':
                    time.sleep(2)
                elif resource_status == 'critical':
                    try:
                        self.test_queue.put(test_name)
                    except Exception:
                        pass
                    time.sleep(self.RESOURCE_CONFIG['pause_duration'])
                    continue

                self.current_tests[worker_id] = test_name

                time_remaining = self._get_time_remaining()
                bug_found, bug_files, runtime, exit_action = self._run_fuzzer(
                    test_name,
                    worker_id,
                    per_test_timeout=time_remaining if self.time_remaining and time_remaining > 0 else None,
                )

                if worker_id in self.current_tests:
                    del self.current_tests[worker_id]

                action = self._handle_fuzzer_result(test_name, bug_found, bug_files, runtime, exit_action, worker_id)

                if action == 'requeue':
                    try:
                        self.test_queue.put(test_name)
                        self.stats['tests_requeued'] += 1
                    except Exception:
                        pass

                self.stats['tests_processed'] += 1

            except Exception as e:
                print(f"[WORKER {worker_id}] Error in worker: {e}", file=sys.stderr)
                continue

        print(f"[WORKER {worker_id}] Stopped")

    def run(self):
        if not self.tests:
            print(f"No tests provided{' for job ' + self.job_id if self.job_id else ''}")
            return

        print(f"Running {self.fuzzer_name} fuzzer on {len(self.tests)} test(s){' for job ' + self.job_id if self.job_id else ''}")
        print(f"Solver: {self.solver_name} ({self.solver_binary} {self.solver_flags})")
        print(f"Oracle: {self.oracle_name} ({self.oracle_binary} {self.oracle_flags})")
        print(f"Fuzzer: {self.fuzzer_name}")
        print(f"Tests root: {self.tests_root}")
        print(f"Timeout: {self.time_remaining}s ({self.time_remaining // 60} minutes)" if self.time_remaining else "No timeout")
        if self.fuzzer_params:
            params_str = ", ".join(f"{k}={v}" for k, v in self.fuzzer_params.items())
            print(f"Fuzzer params: {params_str}")
        print(f"CPU cores: {self.cpu_count}")
        print(f"Workers: {self.num_workers}")
        print()

        for test in self.tests:
            self.test_queue.put(test)

        workers = []
        for worker_id in range(1, self.num_workers + 1):
            worker = multiprocessing.Process(target=self._worker_process, args=(worker_id,))
            worker.start()
            workers.append(worker)

        self.workers = workers

        monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        monitor_thread.start()

        def signal_handler(signum, frame):
            print("\nShutdown signal received, stopping workers...")
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        try:
            if self.time_remaining:
                end_time = self.start_time + self.time_remaining
                while time.time() < end_time and any(w.is_alive() for w in workers):
                    time.sleep(1)
                if time.time() >= end_time:
                    print("Timeout reached, stopping workers...")
                    self.shutdown_event.set()
            else:
                for worker in workers:
                    worker.join()
        except KeyboardInterrupt:
            print("\nInterrupted, stopping workers...")
            self.shutdown_event.set()

        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                worker_pid = getattr(worker, 'pid', 'unknown')
                print(f"Warning: Worker {worker_pid} did not terminate, killing...")
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()

        for worker_id in range(1, self.num_workers + 1):
            worker_bugs = self.bugs_folder / f"worker_{worker_id}"
            for bug_file in self._collect_bug_files(worker_bugs):
                try:
                    dest = self.bugs_folder / bug_file.name
                    if dest.exists():
                        timestamp = int(time.time())
                        dest = self.bugs_folder / f"{bug_file.stem}_{timestamp}{bug_file.suffix}"
                    shutil.move(str(bug_file), str(dest))
                except Exception:
                    pass

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
                    with open(bug_file, 'r') as f:
                        print(f.read())
                except Exception as e:
                    print(f"Error reading bug file: {e}")
                print("-" * 60)
        else:
            print("No bugs found.")

        print()
        print("Statistics:")
        print(f"  Tests processed: {self.stats.get('tests_processed', 0)}")
        print(f"  Bugs found: {self.stats.get('bugs_found', 0)}")
        print(f"  Tests requeued (bugs found): {self.stats.get('tests_requeued', 0)}")
        print(f"  Tests removed (unsupported): {self.stats.get('tests_removed_unsupported', 0)}")
        print(f"  Tests removed (timeout): {self.stats.get('tests_removed_timeout', 0)}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Commit fuzzer that runs configurable fuzzers on SMT solver test suites"
    )
    parser.add_argument(
        "--solver",
        required=True,
        help="Solver name (must have a solver.json in scripts/solvers/<name>/)",
    )
    parser.add_argument(
        "--oracle",
        help="Oracle solver name (default: from solver config, fallback: cvc5)",
    )
    parser.add_argument(
        "--fuzzer",
        help="Fuzzer name (default: from solver config, fallback: typefuzz)",
    )
    parser.add_argument(
        "--tests-json",
        required=True,
        help="JSON array of test names (relative to --tests-root)",
    )
    parser.add_argument(
        "--job-id",
        help="Job identifier (optional, for logging)",
    )
    parser.add_argument(
        "--tests-root",
        help="Root directory for tests (default: from solver config test_dir)",
    )
    parser.add_argument(
        "--time-remaining",
        type=int,
        help="Remaining time until job timeout in seconds (legacy, use --job-start-time instead)",
    )
    parser.add_argument(
        "--job-start-time",
        type=float,
        help="Unix timestamp when the job started (for automatic time calculation)",
    )
    parser.add_argument(
        "--stop-buffer-minutes",
        type=int,
        default=5,
        help="Minutes before timeout to stop (default: 5)",
    )
    parser.add_argument(
        "--fuzzer-param",
        action="append",
        metavar="KEY=VALUE",
        help="Override a fuzzer parameter (e.g. --fuzzer-param iterations=500). Repeatable.",
    )
    try:
        default_workers = psutil.cpu_count()
    except Exception:
        default_workers = 4

    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"Number of worker processes (default: {default_workers}, auto-detected from CPU cores)",
    )
    parser.add_argument(
        "--bugs-folder",
        default="bugs",
        help="Folder to store bugs (default: bugs)",
    )

    args = parser.parse_args()

    # Resolve oracle and fuzzer from config
    oracle_name = get_oracle_name(args.solver, args.oracle)
    fuzzer_name = get_fuzzer_name(args.solver, args.fuzzer)

    # Resolve tests-root from config if not provided
    if args.tests_root:
        tests_root = args.tests_root
    else:
        solver_config = get_solver_config(args.solver)
        tests_root = solver_config.get("test_dir", "test/regress/cli")

    # Resolve fuzzer parameters: solver overrides → fuzzer defaults, then CLI overrides
    fuzzer_params = get_fuzzer_params(args.solver, fuzzer_name)
    if args.fuzzer_param:
        for item in args.fuzzer_param:
            key, _, value = item.partition("=")
            # Try to convert numeric values
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
            fuzzer_params[key] = value

    # Parse tests JSON
    try:
        tests = json.loads(args.tests_json)
        if not isinstance(tests, list):
            raise ValueError("tests-json must be a JSON array")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in --tests-json: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Create and run fuzzer
    try:
        fuzzer = SimpleCommitFuzzer(
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
        )
        fuzzer.run()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
