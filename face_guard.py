#!/usr/bin/env python3
"""
face_guard.py — faceguard entry point

This is the script the LaunchAgent runs at login. It owns:
  - Startup delay (let camera driver and LM Studio settle after login)
  - PID lockfile (prevent duplicate runs)
  - Config + logger initialization
  - Calling guard_core.run()
  - Passing the GuardResult to the alert dispatcher (siren + Discord)
  - Writing the structured log entry
  - Clean exit with appropriate code

Exit codes:
  0  — KNOWN (clean run, identity confirmed)
  1  — UNKNOWN or NO_FACE (alarm condition, alerts were fired)
  2  — CAMERA_ERROR, ROSTER_ERROR, CONFIG_ERROR (system fault)
  3  — Another instance already running (PID lock)

--dry-run flag:
  Runs the full recognition core but skips all alerts (no siren, no Discord).
  Use this to test the guard after enrollment without waking the neighbours.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def _check_deps() -> None:
    missing = []
    for pkg, install_name in [
        ("face_recognition", "face_recognition"),
        ("cv2", "opencv-python"),
    ]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(install_name)
    if missing:
        print(
            f"[faceguard] FATAL: Missing dependencies: {', '.join(missing)}\n"
            f"Install with: pip install {' '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(2)


_check_deps()
from .faceguard.config import load_config, ConfigError
from .faceguard.logger import init_logger, get_logger
from .faceguard.pidlock import PidLock, LockError
from .faceguard.alerts import Verdict, GuardResult
from .faceguard import guard_core
from .faceguard.alerts import dispatch

# from faceguard.config import load_config, ConfigError
# from faceguard.logger import init_logger, get_logger
# from faceguard.pidlock import PidLock, LockError
# from faceguard.result import Verdict, GuardResult
# from faceguard import guard_core
# from faceguard.alerts import dispatch


# ── Alert dispatcher ───────────────────────────────────────────────────────────

def _dispatch_alerts(result: GuardResult, cfg, dry_run: bool) -> None:
    dispatch(result=result, cfg=cfg, dry_run=dry_run)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="faceguard — login face recognition guard"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run recognition but skip all alerts (siren, Discord). Safe for testing.",
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Skip the startup delay. Useful when running manually.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all log lines to stderr (not just WARN/ERROR).",
    )
    args = parser.parse_args()

    # ── Config ─────────────────────────────────────────────────────────────────
    try:
        cfg = load_config()
    except ConfigError as exc:
        # Logger not yet initialized — write directly to stderr
        print(f"[faceguard] FATAL: Config error: {exc}", file=sys.stderr)
        sys.exit(2)

    # ── Logger — must be initialized before anything else logs ────────────────
    log = init_logger(logs_dir=cfg.paths.logs_dir, verbose=args.verbose or args.dry_run)
    log.info("faceguard_starting", dry_run=args.dry_run, verbose=args.verbose)

    # ── PID lock ───────────────────────────────────────────────────────────────
    lock = PidLock(cfg.guard.pid_file)
    try:
        lock.acquire()
    except LockError as exc:
        log.warn("pid_lock_failed", detail=str(exc))
        print(f"[faceguard] {exc}", file=sys.stderr)
        sys.exit(3)

    try:
        _run_guarded(cfg, args, log)
    finally:
        lock.release()
        log.info("faceguard_exit")


def _run_guarded(cfg, args, log) -> None:
    """
    Inner logic after lock is acquired.
    Extracted so the finally: lock.release() in main() always runs.
    """
    # ── Startup delay ──────────────────────────────────────────────────────────
    # After login the camera driver and LM Studio both need time to initialize.
    # Skipping this causes camera open failures and LM Studio connection errors
    # on the first run. The delay is configurable — 8s is a safe default.
    if not args.no_delay:
        delay = cfg.guard.startup_delay_seconds
        log.info("startup_delay", seconds=delay)
        time.sleep(delay)

    # ── Run recognition core ───────────────────────────────────────────────────
    result = guard_core.run(cfg)
    log.info("guard_result", **result.to_log_dict())

    # ── Dispatch alerts ────────────────────────────────────────────────────────
    _dispatch_alerts(result, cfg=cfg, dry_run=args.dry_run)

    # ── Exit code ──────────────────────────────────────────────────────────────
    if result.verdict == Verdict.KNOWN:
        sys.exit(0)
    elif result.verdict.is_alarm:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
