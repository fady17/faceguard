"""
faceguard/logger.py

Structured logger that writes JSON lines to a daily rotating file
and also emits human-readable output to stderr for interactive use.

Why JSON lines:
  - Machine parseable — you can grep, jq, or feed into any log aggregator
  - One line per event — no multiline parsing needed
  - Every event has the same shape: timestamp, level, event, context dict

Why daily rotation:
  - Keeps individual files small
  - Easy to archive/delete old days without a log rotation daemon
  - Filename IS the date — no parsing needed to find "today's log"
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class FaceGuardLogger:
    """
    Writes structured JSON log lines.
    Each call produces exactly one line: { timestamp, level, event, **context }
    """

    LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}

    def __init__(self, logs_dir: Path, min_level: str = "INFO", verbose: bool = False):
        self.logs_dir = logs_dir
        self.min_level_value = self.LEVELS.get(min_level.upper(), 20)
        self.verbose = verbose  # if True, also print to stderr
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _log_path(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"{today}.jsonl"

    def _write(self, level: str, event: str, context: dict[str, Any]) -> None:
        if self.LEVELS.get(level, 0) < self.min_level_value:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            **context,
        }

        line = json.dumps(entry, default=str)

        try:
            with self._log_path().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # Disk full or permission issue — don't crash the guard, just stderr
            print(f"[faceguard] WARNING: could not write log: {line}", file=sys.stderr)

        if self.verbose or level in ("WARN", "ERROR", "CRITICAL"):
            ts = entry["timestamp"][11:19]  # HH:MM:SS only
            print(f"[{ts}] {level:8s} {event}", file=sys.stderr)
            if context:
                for k, v in context.items():
                    print(f"           {k}: {v}", file=sys.stderr)

    # ── Public API ─────────────────────────────────────────────────────────────

    def debug(self, event: str, **ctx: Any) -> None:
        self._write("DEBUG", event, ctx)

    def info(self, event: str, **ctx: Any) -> None:
        self._write("INFO", event, ctx)

    def warn(self, event: str, **ctx: Any) -> None:
        self._write("WARN", event, ctx)

    def error(self, event: str, **ctx: Any) -> None:
        self._write("ERROR", event, ctx)

    def critical(self, event: str, **ctx: Any) -> None:
        self._write("CRITICAL", event, ctx)

    def exception(self, event: str, exc: Exception, **ctx: Any) -> None:
        """Log an exception with its full traceback in the context."""
        self._write("ERROR", event, {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            **ctx,
        })


# ── Module-level convenience: logger is initialized by setup.py / main entry ──
# Other modules import the singleton via:
#   from faceguard.logger import get_logger
#   log = get_logger()

_instance: Optional[FaceGuardLogger] = None


def init_logger(logs_dir: Path, verbose: bool = False) -> FaceGuardLogger:
    global _instance
    _instance = FaceGuardLogger(logs_dir=logs_dir, verbose=verbose)
    return _instance


def get_logger() -> FaceGuardLogger:
    if _instance is None:
        # Fallback: log to /tmp so we never crash on a missing logger
        return FaceGuardLogger(logs_dir=Path("/tmp/faceguard_logs"), verbose=True)
    return _instance
