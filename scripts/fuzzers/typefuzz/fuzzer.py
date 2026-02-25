"""Typefuzz (yinyang) fuzzer — type-aware SMT formula mutation."""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class Fuzzer:
    DEFAULT_PARAMS = {"iterations": 250, "modulo": 2, "timeout": 120}
    DEFAULT_COMMAND = [
        "typefuzz",
        "-i", "{iterations}",
        "-m", "{modulo}",
        "--timeout", "{timeout}",
        "--bugs", "{bugs_dir}",
        "--scratch", "{scratch_dir}",
        "--logfolder", "{log_dir}",
        "{solver_clis}",
        "{seed_path}",
    ]
    DEFAULT_EXIT_CODES = {
        "10": {"bug_found": True,  "action": "requeue"},
        "3":  {"bug_found": False, "action": "remove"},
        "0":  {"bug_found": False, "action": "requeue"},
    }
    DEFAULT_EXIT_ACTION = {"bug_found": False, "action": "continue"}
    DEFAULT_DIRS = {
        "bugs_dir":    {"path": "bugs/worker_{worker_id}", "type": "output"},
        "scratch_dir": {"path": "scratch_{worker_id}",     "type": "temp"},
        "log_dir":     {"path": "logs_{worker_id}",        "type": "temp"},
    }
    DEFAULT_BUG_PATTERNS = ["*.smt2", "*.smt"]
    DEFAULT_SOLVER_CLIS_SEPARATOR = ";"

    def __init__(
        self,
        worker_id: int,
        seed_path: str,
        solver_cli: str,
        oracle_cli: str,
        params_override: Optional[Dict] = None,
    ):
        params = {**self.DEFAULT_PARAMS, **(params_override or {})}

        self.dirs = {
            name: {"path": Path(cfg["path"].format(worker_id=worker_id)), "type": cfg["type"]}
            for name, cfg in self.DEFAULT_DIRS.items()
        }

        ctx = {
            **params,
            "seed_path": seed_path,
            "solver_cli": solver_cli,
            "oracle_cli": oracle_cli,
            "solver_clis": self.DEFAULT_SOLVER_CLIS_SEPARATOR.join([solver_cli, oracle_cli]),
            "worker_id": str(worker_id),
        }
        ctx.update({name: str(info["path"]) for name, info in self.dirs.items()})
        self.cmd = [token.format_map(ctx) for token in self.DEFAULT_COMMAND]

        for info in self.dirs.values():
            info["path"].mkdir(parents=True, exist_ok=True)

    def execute(self, timeout: Optional[float] = None) -> Tuple[int, float]:
        start = time.time()
        try:
            kwargs: Dict = {"capture_output": True, "text": True}
            if timeout and timeout > 0:
                kwargs["timeout"] = timeout
            result = subprocess.run(self.cmd, **kwargs)
            return result.returncode, time.time() - start
        except subprocess.TimeoutExpired:
            return -1, time.time() - start
        except Exception:
            return -1, time.time() - start

    def collect(self) -> List[Path]:
        files = []
        for info in self.dirs.values():
            if info["type"] == "output" and info["path"].exists():
                for pattern in self.DEFAULT_BUG_PATTERNS:
                    files.extend(info["path"].glob(pattern))
        return files

    def parse_result(self, exit_code: int) -> Tuple[bool, str]:
        entry = self.DEFAULT_EXIT_CODES.get(str(exit_code), self.DEFAULT_EXIT_ACTION)
        return entry["bug_found"], entry["action"]

    def cleanup(self):
        for info in self.dirs.values():
            if info["type"] == "temp":
                shutil.rmtree(info["path"], ignore_errors=True)
