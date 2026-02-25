"""Resource monitoring for the commit fuzzer.

Tracks CPU/RAM usage, kills runaway processes, and triggers graceful
shutdown when memory becomes critically low.
"""

import gc
import multiprocessing
import os
import psutil
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional


class ResourceMonitor:
    CONFIG = {
        'cpu_warning': 85.0,
        'cpu_critical': 95.0,
        'memory_warning_available_gb': 2.0,
        'memory_critical_available_gb': 0.5,
        'check_interval': 2,
        'pause_duration': 10,
        'max_process_memory_mb': 2048,
        'max_process_memory_mb_warning': 1536,
    }

    def __init__(
        self,
        shutdown_event: multiprocessing.Event,
        stats: Dict,
        bugs_folder: Path,
        bug_patterns: List[str],
    ):
        self.shutdown_event = shutdown_event
        self.stats = stats
        self.bugs_folder = bugs_folder
        self.bug_patterns = bug_patterns
        self.workers: List = []

        _manager = multiprocessing.Manager()
        self._state = _manager.dict({'status': 'normal', 'paused': False})
        self._state_lock = multiprocessing.Lock()
        self.current_tests = _manager.dict()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        thread.start()
        return thread

    def check_state(self) -> str:
        with self._state_lock:
            return self._state.get('status', 'normal')

    def is_paused(self) -> bool:
        with self._state_lock:
            return self._state.get('paused', False)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _monitor_loop(self):
        while not self.shutdown_event.is_set():
            try:
                try:
                    cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
                    memory = psutil.virtual_memory()
                    memory_available_gb = memory.available / (1024 ** 3)
                    avg_cpu = sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0.0

                    status = 'normal'
                    if (avg_cpu >= self.CONFIG['cpu_critical'] or
                            memory_available_gb < self.CONFIG['memory_critical_available_gb']):
                        status = 'critical'
                    elif (avg_cpu >= self.CONFIG['cpu_warning'] or
                          memory_available_gb < self.CONFIG['memory_warning_available_gb']):
                        status = 'warning'

                    with self._state_lock:
                        self._state['status'] = status

                    threshold = (
                        self.CONFIG['max_process_memory_mb_warning']
                        if memory_available_gb < self.CONFIG['memory_warning_available_gb']
                        else self.CONFIG['max_process_memory_mb']
                    )
                    self._kill_high_memory_processes(threshold)

                    if status == 'critical':
                        self._handle_critical(memory_available_gb)

                except (ImportError, AttributeError) as e:
                    print(f"[WARN] psutil not available, skipping resource monitoring: {e}", file=sys.stderr)
                    break

                time.sleep(self.CONFIG['check_interval'])
            except Exception as e:
                print(f"[WARN] Error in resource monitoring: {e}", file=sys.stderr)
                time.sleep(self.CONFIG['check_interval'])

    def _handle_critical(self, memory_available_gb: float):
        print(f"[RESOURCE] Critical resource usage — RAM available: {memory_available_gb:.2f}GB", file=sys.stderr)

        if memory_available_gb < self.CONFIG['memory_critical_available_gb']:
            self._stop_and_report()
            return

        with self._state_lock:
            self._state['paused'] = True
        try:
            gc.collect()
        except Exception:
            pass
        time.sleep(self.CONFIG['pause_duration'])
        with self._state_lock:
            self._state['paused'] = False

    def _stop_and_report(self):
        print("\n" + "=" * 60, file=sys.stderr)
        print("CRITICAL RAM — STOPPING TO PRESERVE BUGS", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        subdirs = [self.bugs_folder] + [d for d in self.bugs_folder.iterdir() if d.is_dir()]
        total_bugs = sum(len(self._collect(d)) for d in subdirs)

        print(f"  Total bugs found: {total_bugs}", file=sys.stderr)
        print(f"  Tests processed: {self.stats.get('tests_processed', 0)}", file=sys.stderr)
        print("Stopping fuzzer...", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)
        self.shutdown_event.set()

    def _collect(self, folder: Path) -> List[Path]:
        if not folder.exists():
            return []
        return [f for pattern in self.bug_patterns for f in folder.glob(pattern)]

    def _kill_high_memory_processes(self, threshold_mb: float):
        HIGH_MEMORY_REPORT_MB = 14336
        try:
            main_pid = os.getpid()
            worker_pids = {}
            for worker_id, w in enumerate(self.workers, start=1):
                try:
                    worker_pids[w.pid] = worker_id
                except (AttributeError, ValueError):
                    pass

            pid_to_worker: Dict = {}
            tracked_pids = {main_pid} | set(worker_pids)
            for pid in list(tracked_pids):
                worker_id = worker_pids.get(pid)
                descendants = self._descendants(pid)
                tracked_pids.update(descendants)
                if worker_id:
                    for d in descendants:
                        pid_to_worker[d] = worker_id

            killed = 0
            for pid in tracked_pids:
                try:
                    proc = psutil.Process(pid)
                    rss_mb = proc.memory_info().rss / (1024 * 1024)
                    if rss_mb <= threshold_mb:
                        continue
                    name = proc.name()
                    cmdline = ' '.join(proc.cmdline()[:3])
                    print(f"[RESOURCE] Killing {pid} ({name}) {rss_mb:.1f}MB > {threshold_mb}MB", file=sys.stderr)
                    print(f"  Command: {cmdline}...", file=sys.stderr)
                    if rss_mb >= HIGH_MEMORY_REPORT_MB:
                        wid = pid_to_worker.get(pid)
                        test = self.current_tests.get(wid) if wid else None
                        msg = f"while processing: {test}" if test else "(test unknown)"
                        print(f"  HIGH RAM: {rss_mb:.1f}MB {msg}", file=sys.stderr)
                    proc.kill()
                    killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, AttributeError):
                    pass

            if killed:
                print(f"[RESOURCE] Killed {killed} process(es) > {threshold_mb}MB", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Error killing high-RAM processes: {e}", file=sys.stderr)

    def _descendants(self, pid: int):
        result = set()
        try:
            for child in psutil.Process(pid).children(recursive=True):
                try:
                    result.add(child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return result
