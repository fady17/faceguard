"""
faceguard/guard_core.py

Pure recognition engine. Single responsibility: capture a frame, run face
recognition against the roster, return a GuardResult.

What this module deliberately does NOT do:
  - Play sounds
  - Send Discord messages
  - Call LM Studio
  - Print to stdout
  - Exit the process

All of those are the alert layer's job (Phase 4 and 5). This separation means:
  - The core is unit-testable with mock cameras and mock rosters
  - Alert logic can be changed without touching recognition logic
  - A --dry-run flag in face_guard.py can run the full core and skip only alerts

Flow:
  1. Load roster (ROSTER_ERROR if missing/corrupt)
  2. Open camera with retries (CAMERA_ERROR if all fail)
  3. Capture frame, retry on no-face up to config limit (NO_FACE if exhausted)
  4. For each detected face, compare against all roster encodings
  5. Resolve best match per face (lowest distance wins)
  6. Classify each face as KNOWN or UNKNOWN
  7. Set overall verdict: KNOWN if all faces matched, UNKNOWN if any didn't
  8. Save capture frame to disk
  9. Return GuardResult
"""

from __future__ import annotations

import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from faceguard.camera import (
    CameraError,
    CaptureResult,
    capture_frame,
    open_camera,
    save_frame,
)
from faceguard.config import AppConfig
from faceguard.logger import get_logger
from faceguard.result import FaceResult, GuardResult, Verdict
from faceguard.roster import Roster, RosterError


def run(cfg: AppConfig) -> GuardResult:
    """
    Execute one full guard cycle.
    Always returns a GuardResult — never raises. All exceptions are caught,
    logged, and surfaced as the appropriate Verdict so the alert layer has
    a consistent type to work with regardless of what went wrong.
    """
    log = get_logger()
    hostname = socket.gethostname()
    log.info("guard_run_start", hostname=hostname)

    # ── 1. Load roster ─────────────────────────────────────────────────────────
    try:
        roster = Roster.load(cfg.paths.roster_file)
    except RosterError as exc:
        log.error("roster_load_failed", detail=str(exc))
        return GuardResult(
            verdict=Verdict.ROSTER_ERROR,
            hostname=hostname,
            message="Roster file is missing or corrupt — guard cannot identify faces.",
            error_detail=str(exc),
        )

    if roster.is_empty():
        log.error("roster_empty")
        return GuardResult(
            verdict=Verdict.ROSTER_ERROR,
            hostname=hostname,
            message="Roster is empty. Run: python enroll.py add <your-name>",
            error_detail="No enrolled faces found.",
        )

    known_encodings, known_names = roster.all_encodings()
    log.info("roster_loaded", people=roster.names(), encoding_count=len(known_encodings))

    # ── 2. Open camera ─────────────────────────────────────────────────────────
    cap = None
    camera_attempts = 0
    try:
        cap = open_camera(
            index=cfg.recognition.camera_index,
            retries=cfg.recognition.capture_retries,
            delay=cfg.recognition.capture_retry_delay_seconds,
        )
        camera_attempts = 1  # open_camera succeeded
        log.info("camera_opened", index=cfg.recognition.camera_index)
    except CameraError as exc:
        log.error("camera_open_failed", detail=str(exc))
        return GuardResult(
            verdict=Verdict.CAMERA_ERROR,
            hostname=hostname,
            message=f"Camera could not be opened on {hostname}.",
            error_detail=str(exc),
            camera_attempts=cfg.recognition.capture_retries,
        )

    # ── 3. Capture frame + face detection with retries ─────────────────────────
    # We import face_recognition here rather than at module top so that
    # import failures produce a clear error rather than crashing on startup.
    try:
        import face_recognition as fr
    except ImportError as exc:
        if cap:
            cap.release()
        log.critical("face_recognition_import_failed", detail=str(exc))
        return GuardResult(
            verdict=Verdict.ROSTER_ERROR,
            hostname=hostname,
            message="face_recognition library is not installed.",
            error_detail=str(exc),
        )

    capture: Optional[CaptureResult] = None
    face_locations: list = []
    face_retries = 0

    for attempt in range(cfg.recognition.no_face_retries + 1):
        face_retries = attempt
        try:
            capture = capture_frame(cap)
        except CameraError as exc:
            # Frame read failed mid-session — hardware error, don't retry
            log.error("frame_read_failed", attempt=attempt, detail=str(exc))
            cap.release()
            return GuardResult(
                verdict=Verdict.CAMERA_ERROR,
                hostname=hostname,
                message=f"Camera disconnected mid-capture on {hostname}.",
                error_detail=str(exc),
                camera_attempts=camera_attempts,
                face_retries=face_retries,
            )

        face_locations = fr.face_locations(capture.frame_rgb, model="hog")

        if face_locations:
            log.info(
                "faces_detected",
                count=len(face_locations),
                attempt=attempt,
            )
            break

        log.warn(
            "no_face_detected",
            attempt=attempt + 1,
            max_attempts=cfg.recognition.no_face_retries + 1,
        )

        if attempt < cfg.recognition.no_face_retries:
            time.sleep(cfg.recognition.no_face_retry_delay_seconds)

    cap.release()
    log.info("camera_released")

    # ── 4. No face after all retries ───────────────────────────────────────────
    if not face_locations or capture is None:
        capture_path = _save_capture(capture, cfg, hostname, "no_face")
        log.warn(
            "no_face_after_retries",
            retries=face_retries,
            capture_path=str(capture_path),
        )
        return GuardResult(
            verdict=Verdict.NO_FACE,
            hostname=hostname,
            message=(
                f"No face detected after {face_retries + 1} attempt(s). "
                f"Camera may be obstructed or the room may be too dark."
            ),
            capture_path=capture_path,
            frame_bgr=capture.frame if capture else None,
            camera_attempts=camera_attempts,
            face_retries=face_retries,
        )

    # ── 5 + 6. Encode and match each detected face ────────────────────────────
    face_encodings = fr.face_encodings(capture.frame_rgb, face_locations)
    tolerance = cfg.recognition.tolerance

    face_results: list[FaceResult] = []

    for idx, (location, encoding) in enumerate(zip(face_locations, face_encodings)):
        distances = fr.face_distance(known_encodings, encoding)

        if len(distances) == 0:
            # Encoding list was empty — shouldn't happen after roster.is_empty check
            face_results.append(FaceResult(
                face_index=idx,
                location=location,
                matched_name=None,
                distance=None,
                is_match=False,
            ))
            continue

        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        is_match = best_distance <= tolerance

        # When multiple encodings exist per person, several indices may belong
        # to the same name. We already have the best distance — just grab the name.
        matched_name = known_names[best_idx] if is_match else None

        log.info(
            "face_classified",
            face_index=idx,
            matched_name=matched_name,
            best_distance=round(best_distance, 4),
            is_match=is_match,
            tolerance=tolerance,
        )

        face_results.append(FaceResult(
            face_index=idx,
            location=location,
            matched_name=matched_name,
            distance=best_distance,
            is_match=is_match,
        ))

    # ── 7. Overall verdict ────────────────────────────────────────────────────
    has_unknown = any(not f.is_match for f in face_results)
    verdict = Verdict.UNKNOWN if has_unknown else Verdict.KNOWN

    # ── 8. Save capture to disk ───────────────────────────────────────────────
    tag = "unknown" if verdict == Verdict.UNKNOWN else "known"
    capture_path = _save_capture(capture, cfg, hostname, tag)

    # ── 9. Build and return result ────────────────────────────────────────────
    known_names_found = list({f.matched_name for f in face_results if f.matched_name})
    unknown_count = sum(1 for f in face_results if not f.is_match)

    if verdict == Verdict.KNOWN:
        message = f"Access granted: {', '.join(known_names_found)} on {hostname}."
    else:
        parts = []
        if known_names_found:
            parts.append(f"known: {', '.join(known_names_found)}")
        parts.append(f"{unknown_count} unknown face(s)")
        message = f"Intruder alert on {hostname} — {', '.join(parts)}."

    log.info(
        "guard_run_complete",
        verdict=verdict.value,
        message=message,
        capture_path=str(capture_path),
        known=known_names_found,
        unknown_count=unknown_count,
    )

    return GuardResult(
        verdict=verdict,
        hostname=hostname,
        message=message,
        capture_path=capture_path,
        frame_bgr=capture.frame,
        faces=face_results,
        camera_attempts=camera_attempts,
        face_retries=face_retries,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _save_capture(
    capture: Optional[CaptureResult],
    cfg: AppConfig,
    hostname: str,
    tag: str,
) -> Optional[Path]:
    """
    Save the captured frame to the captures directory.
    Returns the path, or None if saving failed (non-fatal — logged only).
    """
    if capture is None:
        return None

    log = get_logger()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_host = hostname.replace(".", "_").replace(" ", "_")
    filename = f"{ts}_{safe_host}_{tag}.jpg"
    path = cfg.paths.captures_dir / filename

    try:
        save_frame(capture.frame, path)
        log.info("capture_saved", path=str(path))
        return path
    except OSError as exc:
        log.warn("capture_save_failed", detail=str(exc))
        return None
