"""
faceguard/result.py

Typed result objects produced by the guard core and consumed by
the alert layer (Phase 4) and LM Studio layer (Phase 5).

Keeping this in its own module means Phase 4 and Phase 5 can import
the result types without importing the full guard logic. It also makes
the shape explicit and documentable in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np


class Verdict(str, Enum):
    """
    Overall outcome of one guard run.

    KNOWN        — all detected faces matched the roster
    UNKNOWN      — at least one face did not match (alarm condition)
    NO_FACE      — camera worked but no face was detected after all retries
                   (possible obstruction, very dark room, or user looked away)
    CAMERA_ERROR — camera could not be opened or frames could not be read
    ROSTER_ERROR — roster missing or unreadable (guard cannot make decisions)
    CONFIG_ERROR — config missing or invalid (guard cannot start)
    """
    KNOWN        = "KNOWN"
    UNKNOWN      = "UNKNOWN"
    NO_FACE      = "NO_FACE"
    CAMERA_ERROR = "CAMERA_ERROR"
    ROSTER_ERROR = "ROSTER_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"

    @property
    def is_alarm(self) -> bool:
        """True for any verdict that should trigger siren + Discord alert."""
        return self in (Verdict.UNKNOWN, Verdict.NO_FACE, Verdict.CAMERA_ERROR)

    @property
    def is_fatal(self) -> bool:
        """True for verdicts caused by misconfiguration, not by an intruder."""
        return self in (Verdict.ROSTER_ERROR, Verdict.CONFIG_ERROR)


@dataclass
class FaceResult:
    """
    Result for a single detected face in the captured frame.
    One GuardResult may contain multiple FaceResults (multiple people in frame).
    """
    face_index: int                  # 0-based index in the frame
    location: tuple[int,int,int,int] # (top, right, bottom, left) pixel coords
    matched_name: Optional[str]      # None if no roster match
    distance: Optional[float]        # raw face_recognition distance (lower = closer match)
    is_match: bool                   # distance <= tolerance

    @property
    def label(self) -> str:
        return self.matched_name if self.is_match else "Unknown" # type: ignore


@dataclass
class GuardResult:
    """
    Complete output of one guard execution.
    Produced by guard_core.run() and passed to alert and LM layers.
    """
    verdict: Verdict
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hostname: str = ""

    # Frame data
    capture_path: Optional[Path] = None      # path where the frame was saved to disk
    frame_bgr: Optional[np.ndarray] = None   # in-memory frame (not persisted in logs)

    # Per-face results (populated when verdict is KNOWN or UNKNOWN)
    faces: list[FaceResult] = field(default_factory=list)

    # Human-readable summary for logs and alerts
    message: str = ""

    # Optional intruder description from LM Studio vision model (Phase 5)
    # Populated only for UNKNOWN verdict when lm_studio.enabled=True and model responds.
    # None means LM Studio was skipped, unavailable, or timed out — not an error.
    lm_description: Optional[str] = None

    # Error detail (populated when verdict is a fault condition)
    error_detail: str = ""

    # Retry accounting (informational)
    camera_attempts: int = 0
    face_retries: int = 0

    # ── Convenience queries ────────────────────────────────────────────────────

    @property
    def known_faces(self) -> list[FaceResult]:
        return [f for f in self.faces if f.is_match]

    @property
    def unknown_faces(self) -> list[FaceResult]:
        return [f for f in self.faces if not f.is_match]

    @property
    def known_names(self) -> list[str]:
        return [f.matched_name for f in self.known_faces if f.matched_name]

    def to_log_dict(self) -> dict:
        """
        Serializable dict for the JSON log line.
        Strips the raw numpy frame (not loggable) but keeps everything else.
        """
        return {
            "verdict":         self.verdict.value,
            "timestamp":       self.timestamp.isoformat(),
            "hostname":        self.hostname,
            "message":         self.message,
            "capture_path":    str(self.capture_path) if self.capture_path else None,
            "known_names":     self.known_names,
            "unknown_count":   len(self.unknown_faces),
            "total_faces":     len(self.faces),
            "lm_description":  self.lm_description,
            "camera_attempts": self.camera_attempts,
            "face_retries":    self.face_retries,
            "error_detail":    self.error_detail,
            "faces": [
                {
                    "index":        f.face_index,
                    "label":        f.label,
                    "distance":     round(f.distance, 4) if f.distance is not None else None,
                    "is_match":     f.is_match,
                }
                for f in self.faces
            ],
        }