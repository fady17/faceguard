"""
faceguard/camera.py

Camera capture utilities shared by enroll.py and face_guard.py.

Why a shared module:
  Both enroll and guard need to open the camera, grab frames, and detect faces.
  Duplicating this logic would mean two places to fix when macOS changes a
  camera permission behaviour or OpenCV updates an API.

Camera permission note (macOS):
  The first time any Python process calls cv2.VideoCapture(), macOS may show
  a permission dialog. The process must be granted Camera access in:
    System Settings → Privacy & Security → Camera
  In a LaunchAgent context (no UI), if permission was never granted interactively,
  the camera will open but return blank frames silently. Running enroll.py at
  least once in a terminal grants the permission for that Python binary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


class CameraError(Exception):
    """Raised when the camera cannot be opened or a frame cannot be captured."""


@dataclass
class CaptureResult:
    frame: np.ndarray           # BGR frame as returned by OpenCV
    frame_rgb: np.ndarray       # RGB frame ready for face_recognition


def open_camera(index: int = 0, retries: int = 3, delay: float = 2.0) -> cv2.VideoCapture:
    """
    Open the camera with retries.
    On macOS the camera driver can take a moment to become available after login,
    so we retry rather than failing immediately.
    Raises CameraError if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            # Warm up: discard first few frames — early frames are often
            # green/black while the sensor auto-adjusts exposure.
            for _ in range(5):
                cap.read()
            return cap
        cap.release()
        if attempt < retries:
            time.sleep(delay)

    raise CameraError(
        f"Could not open camera at index {index} after {retries} attempts. "
        f"Check that the camera is connected and that this Python binary has "
        f"Camera access in System Settings → Privacy & Security → Camera."
    )


def capture_frame(cap: cv2.VideoCapture) -> CaptureResult:
    """
    Grab a single frame from an already-open VideoCapture.
    Raises CameraError if the read fails.
    """
    ok, frame = cap.read()
    if not ok or frame is None:
        raise CameraError(
            "Failed to read a frame from the camera. "
            "The camera may have been disconnected or lost permission."
        )
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return CaptureResult(frame=frame, frame_rgb=frame_rgb)


def capture_frames_burst(
    cap: cv2.VideoCapture,
    count: int = 5,
    interval: float = 0.4,
) -> list[CaptureResult]:
    """
    Capture `count` frames spaced `interval` seconds apart.
    Used by enroll to get multiple angles in one sitting.
    Returns all successfully captured frames (may be fewer than count on error).
    """
    results = []
    for i in range(count):
        try:
            results.append(capture_frame(cap))
        except CameraError:
            pass  # Skip bad frames, don't abort the whole burst
        if i < count - 1:
            time.sleep(interval)
    return results


def save_frame(frame_bgr: np.ndarray, path: Path) -> None:
    """Save a BGR frame to disk as JPEG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), frame_bgr)


def frames_with_faces(
    results: list[CaptureResult],
    model: str = "hog",
) -> list[tuple[CaptureResult, list]]:
    """
    Filter a list of CaptureResults to only those that contain at least one face.
    Returns list of (result, face_locations) tuples.

    model: "hog" is faster and works well in good lighting.
           "cnn" is more accurate but requires GPU or is slow on CPU.
           For enrollment in a well-lit terminal session, "hog" is fine.
    """
    import face_recognition

    found = []
    for result in results:
        locations = face_recognition.face_locations(result.frame_rgb, model=model)
        if locations:
            found.append((result, locations))
    return found
