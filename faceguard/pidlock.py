"""
faceguard/pidlock.py

PID-based lockfile to prevent multiple guard instances running simultaneously.

Why this matters:
  macOS LaunchAgents can fire more than once — fast user switching, a buggy
  KeepAlive setting, or a Sleep/Wake cycle can all cause a second launch before
  the first finishes. Two guard instances racing on the same camera index causes
  OpenCV to fail silently, and two simultaneous Discord alerts for the same login
  event are confusing and noisy.

Stale PID handling:
  If the guard crashed without cleaning up its PID file, the next run would be
  blocked forever unless we check whether the recorded PID is actually alive.
  os.kill(pid, 0) does exactly this — it sends signal 0 (no-op) and raises
  ProcessLookupError if the process doesn't exist. This is the POSIX-standard
  way to test process liveness without affecting it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class LockError(Exception):
    """Raised when the lock cannot be acquired because another instance is running."""


class PidLock:
    def __init__(self, pid_path: Path) -> None:
        self.pid_path = pid_path
        self._acquired = False

    def acquire(self) -> None:
        """
        Acquire the lock.
        Raises LockError if another live instance holds it.
        Silently removes stale lockfiles from dead processes.
        """
        if self.pid_path.exists():
            try:
                existing_pid = int(self.pid_path.read_text().strip())
            except (ValueError, OSError):
                # Unreadable or corrupt PID file — treat as stale
                self.pid_path.unlink(missing_ok=True)
            else:
                if _is_alive(existing_pid):
                    raise LockError(
                        f"Another faceguard instance is already running "
                        f"(PID {existing_pid}, lockfile: {self.pid_path}). "
                        f"If this is wrong, delete {self.pid_path} and try again."
                    )
                else:
                    # Process is dead — stale lockfile, clean it up
                    self.pid_path.unlink(missing_ok=True)

        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(os.getpid()))
        self._acquired = True

    def release(self) -> None:
        """Release the lock. Safe to call even if never acquired."""
        if self._acquired and self.pid_path.exists():
            try:
                # Only delete if it's still OUR pid (guard against race)
                if self.pid_path.read_text().strip() == str(os.getpid()):
                    self.pid_path.unlink()
            except OSError:
                pass
        self._acquired = False

    def __enter__(self) -> "PidLock":
        self.acquire()
        return self

    def __exit__(self, *_) -> None:
        self.release()


def _is_alive(pid: int) -> bool:
    """Return True if a process with this PID currently exists."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it.
        # In practice this means it's alive.
        return True
