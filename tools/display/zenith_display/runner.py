"""Subprocess execution with uniform logging and dry-run support."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("zenith-display")


@dataclass
class Result:
    """Outcome of one executed (or skipped) command."""

    argv: List[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    """Runs commands; dry-run skips *mutations* but still performs queries.

    Read-only commands (``mutating=False``) execute even under ``--dry-run``
    so previews and plans reflect the machine's real state; anything that
    changes the system is logged and skipped.
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.trace: List[List[str]] = []

    def run(self, argv: List[str], timeout: float = 15.0, check: bool = False,
            mutating: bool = True) -> Result:
        self.trace.append(list(argv))
        if self.dry_run and mutating:
            log.info("DRY-RUN: %s", " ".join(argv))
            return Result(argv=argv, returncode=0)
        log.debug("exec: %s", " ".join(argv))
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            return Result(argv=argv, returncode=127, stderr=f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return Result(argv=argv, returncode=124, stderr=f"{argv[0]}: timeout after {timeout}s")
        res = Result(argv=argv, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        if check and not res.ok:
            raise RuntimeError(f"command failed ({res.returncode}): {' '.join(argv)}\n{res.stderr.strip()}")
        if not res.ok:
            log.debug("rc=%d stderr=%s", res.returncode, res.stderr.strip())
        return res

    def query(self, argv: List[str], timeout: float = 15.0, check: bool = False) -> Result:
        """A read-only command: runs even in dry-run mode."""
        return self.run(argv, timeout=timeout, check=check, mutating=False)


def which(tool: str) -> Optional[str]:
    """shutil.which, importable from one place so tests can monkeypatch it."""
    return shutil.which(tool)
