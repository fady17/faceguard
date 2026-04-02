"""
faceguard/alerts/__init__.py

Alert dispatcher — the single function face_guard.py calls.

This is the only public surface of the alerts package.
face_guard.py imports dispatch() and passes the GuardResult.
The siren and Discord modules are implementation details.

Dispatch logic:
  dry_run=True   → log only, no siren, no Discord
  KNOWN          → silent (no alert) — the clean path
  UNKNOWN        → siren (background) + Discord (foreground with photo)
  NO_FACE        → siren + Discord (no-face frame attached if available)
  CAMERA_ERROR   → Discord only (no frame, no siren — guard is blind)
  ROSTER_ERROR   → Discord only (system fault notification)
  CONFIG_ERROR   → Discord only (system fault — should not reach here normally)

Siren thread join:
  After Discord sends we join() the siren thread with a timeout.
  This gives the siren time to finish its repetitions while also ensuring
  the guard process doesn't exit while the thread is mid-play (which would
  cut the sound off on a daemon thread). If the siren hasn't finished within
  the join timeout we let the process exit anyway — the daemon thread dies.
"""

from __future__ import annotations

import threading
from typing import Optional

from faceguard.config import AppConfig
from faceguard.logger import get_logger
from faceguard.result import GuardResult, Verdict
from faceguard.vision import describe_intruder
from faceguard.alerts.discord import send_alert
from faceguard.alerts.siren import play_siren

# How long (seconds) to wait for the siren thread after Discord completes
_SIREN_JOIN_TIMEOUT = 30.0


def dispatch(result: GuardResult, cfg: AppConfig, dry_run: bool = False) -> None:
    """
    Route a GuardResult to the appropriate alert actions.
    Never raises — all sub-calls are already exception-safe.

    Execution order for alarm verdicts:
      1. Fire siren (background thread — non-blocking)
      2. Call LM Studio for intruder description (foreground, time-bounded)
      3. Send Discord alert with photo + description (foreground, with retries)
      4. Join siren thread (wait up to _SIREN_JOIN_TIMEOUT)

    LM Studio runs before Discord so the description is ready to include in
    the embed. The siren fires before both so it starts immediately on alarm.
    """
    log = get_logger()
    verdict = result.verdict

    # ── Dry run — log only ─────────────────────────────────────────────────────
    if dry_run:
        log.info("dispatch_dry_run", verdict=verdict.value, message=result.message)
        _print_dry_run_summary(result, cfg)
        return

    # ── KNOWN — silent success ─────────────────────────────────────────────────
    if verdict == Verdict.KNOWN:
        log.info("dispatch_known", names=result.known_names)
        # No siren, no Discord. The log entry written by face_guard.py is enough.
        return

    # ── Alarm or fault — determine which alerts to fire ────────────────────────
    play_sound   = verdict.is_alarm and cfg.siren.enabled
    send_discord = True   # always attempt Discord for non-KNOWN verdicts

    log.info(
        "dispatch_alert",
        verdict=verdict.value,
        play_sound=play_sound,
        send_discord=send_discord,
        message=result.message,
    )

    # ── 1. Fire siren in background (non-blocking) ─────────────────────────────
    siren_thread: Optional[threading.Thread] = None
    if play_sound:
        siren_thread = play_siren(
            sound_file=cfg.siren.sound_file,
            volume=cfg.siren.volume,
            repeat=cfg.siren.repeat,
            block=False,
        )

    # ── 2. LM Studio vision description (soft, time-bounded) ──────────────────
    # Only attempted for UNKNOWN — other verdicts don't have a face to describe.
    # The result object is mutated in-place so Discord picks it up automatically.
    if (
        verdict in (Verdict.UNKNOWN, Verdict.NO_FACE)  
        and cfg.lm_studio.enabled
        and cfg.lm_studio.describe_unknown
        and result.frame_bgr is not None
    ):
        log.info("vision_layer_start", model=cfg.lm_studio.model)
        description = describe_intruder(result=result, lm_cfg=cfg.lm_studio)
        if description:
            result.lm_description = description
            log.info("vision_layer_done", chars=len(description))
        else:
            log.info("vision_layer_skipped", hint="LM Studio unavailable or timed out")

    # ── 3. Send Discord alert (foreground, with retries) ──────────────────────
    if send_discord:
        success = send_alert(result=result, webhook_url=cfg.discord.webhook_url)
        if not success:
            log.error(
                "discord_alert_failed",
                verdict=verdict.value,
                capture_path=str(result.capture_path),
                hint="Photo is saved locally — check captures dir",
            )

    # ── 4. Wait for siren to finish ────────────────────────────────────────────
    if siren_thread is not None:
        siren_thread.join(timeout=_SIREN_JOIN_TIMEOUT)
        if siren_thread.is_alive():
            log.warn("siren_thread_still_running", hint="Process will exit anyway")
    # ── 5. Shut down LM Studio after use ───────────────────────────────────────
    if cfg.lm_studio.enabled:
        _shutdown_lm_studio(cfg.lm_studio)

    log.info("dispatch_complete", verdict=verdict.value)


def _print_dry_run_summary(result: GuardResult, cfg: AppConfig) -> None:
    """Human-readable dry-run output for terminal use."""
    v = result.verdict.value
    print(f"\n[dry-run] ── Guard Result ──────────────────────────")
    print(f"[dry-run] Verdict : {v}")
    print(f"[dry-run] Message : {result.message}")
    print(f"[dry-run] Host    : {result.hostname}")

    if result.faces:
        print(f"[dry-run] Faces   :")
        for f in result.faces:
            if f.is_match:
                status = f"MATCH  → {f.matched_name}  (dist={f.distance:.4f})"
            else:
                dist = f"{f.distance:.4f}" if f.distance is not None else "n/a"
                status = f"UNKNOWN            (dist={dist})"
            print(f"[dry-run]   [{f.face_index}] {status}")

    if result.lm_description:
        print(f"[dry-run] LM desc : {result.lm_description}")
    elif result.verdict == Verdict.UNKNOWN and cfg.lm_studio.enabled:
        print(f"[dry-run] LM desc : (would call {cfg.lm_studio.model} — skipped in dry-run)")

    if result.capture_path:
        print(f"[dry-run] Capture : {result.capture_path}")
    if result.error_detail:
        print(f"[dry-run] Error   : {result.error_detail}")

    would_siren   = result.verdict.is_alarm
    would_discord = result.verdict != Verdict.KNOWN
    would_lm      = result.verdict == Verdict.UNKNOWN and cfg.lm_studio.enabled
    print(f"[dry-run] Would siren  : {would_siren}")
    print(f"[dry-run] Would Discord: {would_discord}")
    print(f"[dry-run] Would LM     : {would_lm}")
    print(f"[dry-run] ────────────────────────────────────────────\n")

def _shutdown_lm_studio(lm_cfg) -> None:
    """Unload the model and stop the LM Studio server after the pipeline completes."""
    import requests
    import subprocess
    import shutil
    log = get_logger()
    base_url = lm_cfg.base_url.rstrip("/").replace("/v1", "")

    # ── Unload model ───────────────────────────────────────────────────────────
    try:
        resp = requests.post(
            f"{base_url}/api/v1/models/unload",
            json={"identifier": lm_cfg.model},
            timeout=10,
        )
        log.info("lm_studio_model_unloaded", model=lm_cfg.model, status=resp.status_code)
    except Exception as exc:
        log.warn("lm_studio_unload_failed", detail=str(exc))

    # ── Stop server ────────────────────────────────────────────────────────────
    # Resolve lms from PATH first, then well-known install locations.
    # No hardcoded paths — works on any machine.
    lms = shutil.which("lms") or shutil.which(
        "lms",
        path="/usr/local/bin:/usr/bin:/bin"
             ":~/.lmstudio/bin"
             ":~/Applications/LM Studio.app/Contents/Resources/bin",
    )
    if not lms:
        log.warn("lm_studio_stop_skipped", hint="lms CLI not found in PATH or known locations")
        return

    try:
        subprocess.run([lms, "server", "stop"], timeout=10, capture_output=True)
        log.info("lm_studio_server_stopped")
    except Exception as exc:
        log.warn("lm_studio_stop_failed", detail=str(exc))