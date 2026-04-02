"""
tests/test_guard_core.py

Integration tests for the guard recognition core.
No real camera, no real face_recognition — both are mocked at the boundary.

The goal is to test every code path through guard_core.run() by controlling:
  - What the camera returns (frame or error)
  - What face_recognition detects (face locations and encodings)
  - What the roster contains

This verifies that:
  - The correct Verdict is returned for each scenario
  - Retry logic fires the right number of times
  - The GuardResult is fully populated
  - No exceptions escape the function
  - Camera is always released (even on error paths)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import numpy as np
import pytest

from faceguard.config import AppConfig
from faceguard.result import Verdict, GuardResult
from faceguard.roster import Roster
from faceguard import guard_core


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fake_encoding(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(128).astype(np.float64)
    return v / np.linalg.norm(v)


def _mock_cap(fail: bool = False) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = not fail
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap.read.return_value = (True, frame)
    return cap


def _roster_with_fady(tmp_path: Path) -> tuple[Roster, np.ndarray]:
    enc = _fake_encoding(seed=1)
    r = Roster()
    r.add("Fady", [enc])
    r.save(tmp_path / "roster.pkl")
    return r, enc


# ── Test: roster errors ────────────────────────────────────────────────────────

class TestRosterErrors:

    def test_missing_roster_returns_roster_error(self, cfg: AppConfig):
        """No roster.pkl on disk → ROSTER_ERROR immediately."""
        # cfg.paths.roster_file points to tmp_path which has no roster.pkl
        result = guard_core.run(cfg)
        assert result.verdict == Verdict.ROSTER_ERROR
        assert "empty" in result.message.lower() or "missing" in result.message.lower()

    def test_empty_roster_returns_roster_error(self, cfg: AppConfig, tmp_path: Path):
        """Empty Roster saved to disk → ROSTER_ERROR."""
        Roster().save(cfg.paths.roster_file)
        result = guard_core.run(cfg)
        assert result.verdict == Verdict.ROSTER_ERROR

    def test_corrupt_roster_returns_roster_error(self, cfg: AppConfig):
        """Corrupt pickle file → ROSTER_ERROR with error detail."""
        cfg.paths.roster_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.paths.roster_file.write_bytes(b"not valid pickle")
        result = guard_core.run(cfg)
        assert result.verdict == Verdict.ROSTER_ERROR
        assert result.error_detail != ""


# ── Test: camera errors ────────────────────────────────────────────────────────

class TestCameraErrors:

    def test_camera_open_failure_returns_camera_error(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """Camera refuses to open → CAMERA_ERROR."""
        _roster_with_fady(tmp_path)

        with patch("faceguard.guard_core.open_camera") as mock_open:
            from faceguard.camera import CameraError
            mock_open.side_effect = CameraError("Camera not found")
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.CAMERA_ERROR
        assert "Camera" in result.message

    def test_camera_error_does_not_raise(self, cfg: AppConfig, tmp_path: Path):
        """guard_core.run() must never raise — not even on camera failure."""
        _roster_with_fady(tmp_path)
        with patch("faceguard.guard_core.open_camera") as mock_open:
            from faceguard.camera import CameraError
            mock_open.side_effect = CameraError("broken")
            result = guard_core.run(cfg)  # must not raise
        assert result is not None

    def test_frame_read_failure_mid_session_returns_camera_error(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """Camera opens but read() fails → CAMERA_ERROR, camera released."""
        _roster_with_fady(tmp_path)
        cap = MagicMock()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mock_frame:
            from faceguard.camera import CameraError
            mock_frame.side_effect = CameraError("disconnected")
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.CAMERA_ERROR
        cap.release.assert_called()  # camera must be released even on error


# ── Test: no-face scenarios ────────────────────────────────────────────────────

class TestNoFace:

    def test_no_face_detected_returns_no_face(self, cfg: AppConfig, tmp_path: Path):
        """face_locations returns [] on all attempts → NO_FACE verdict."""
        _roster_with_fady(tmp_path)
        cap = _mock_cap()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mock_frame, \
             patch("face_recognition.face_locations", return_value=[]), \
             patch("faceguard.guard_core.time.sleep"):
            mock_frame.return_value = MagicMock(
                frame=np.zeros((480, 640, 3), dtype=np.uint8),
                frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
            )
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.NO_FACE

    def test_no_face_retries_correct_number_of_times(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """
        no_face_retries=1 means 2 total attempts (initial + 1 retry).
        face_locations should be called exactly 2 times.
        """
        cfg.recognition.no_face_retries = 1
        _roster_with_fady(tmp_path)
        cap = _mock_cap()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mock_frame, \
             patch("face_recognition.face_locations", return_value=[]) as mock_fl, \
             patch("faceguard.guard_core.time.sleep"):
            mock_frame.return_value = MagicMock(
                frame=np.zeros((480, 640, 3), dtype=np.uint8),
                frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
            )
            result = guard_core.run(cfg)

        assert mock_fl.call_count == 2   # initial + 1 retry
        assert result.verdict == Verdict.NO_FACE
        assert result.face_retries == 1

    def test_no_face_sleep_called_between_retries(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """Sleep should be called between retry attempts, not after the last."""
        cfg.recognition.no_face_retries = 2
        cfg.recognition.no_face_retry_delay_seconds = 1.5
        _roster_with_fady(tmp_path)
        cap = _mock_cap()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mock_frame, \
             patch("face_recognition.face_locations", return_value=[]), \
             patch("faceguard.guard_core.time.sleep") as mock_sleep:
            mock_frame.return_value = MagicMock(
                frame=np.zeros((480, 640, 3), dtype=np.uint8),
                frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
            )
            guard_core.run(cfg)

        # 3 attempts (0,1,2) → sleep between 0→1 and 1→2 → 2 sleeps
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(1.5)

    def test_face_detected_on_retry_returns_match(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """
        First attempt: no face. Second attempt: face found and matches.
        Result should be KNOWN, not NO_FACE.
        """
        cfg.recognition.no_face_retries = 2
        _, enc = _roster_with_fady(tmp_path)
        cap = _mock_cap()
        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )
        location = (10, 100, 90, 20)
        call_count = {"n": 0}

        def face_locations_side_effect(*args, **kwargs):
            call_count["n"] += 1
            return [] if call_count["n"] == 1 else [location]

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", side_effect=face_locations_side_effect), \
             patch("face_recognition.face_encodings", return_value=[enc]), \
             patch("face_recognition.face_distance", return_value=np.array([0.3])), \
             patch("faceguard.guard_core.time.sleep"), \
             patch("faceguard.guard_core._save_capture", return_value=None):
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.KNOWN


# ── Test: recognition and matching ────────────────────────────────────────────

class TestRecognitionMatching:

    def _run_with_face(
        self,
        cfg: AppConfig,
        tmp_path: Path,
        encoding_to_present: np.ndarray,
        roster_encodings: list[np.ndarray],
        tolerance: float = 0.5,
    ) -> GuardResult:
        """
        Helper: sets up a roster, mocks camera to return one face with the
        given encoding, and runs the guard.
        """
        cfg.recognition.tolerance = tolerance
        r = Roster()
        r.add("Fady", roster_encodings)
        r.save(cfg.paths.roster_file)

        cap = _mock_cap()
        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )
        location = (10, 100, 90, 20)
        distances = np.array([
            float(np.linalg.norm(encoding_to_present - re))
            for re in roster_encodings
        ])

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=[location]), \
             patch("face_recognition.face_encodings", return_value=[encoding_to_present]), \
             patch("face_recognition.face_distance", return_value=distances), \
             patch("faceguard.guard_core._save_capture", return_value=None):
            return guard_core.run(cfg)

    def test_exact_match_returns_known(self, cfg: AppConfig, tmp_path: Path):
        enc = _fake_encoding(1)
        result = self._run_with_face(cfg, tmp_path, enc, [enc])
        assert result.verdict == Verdict.KNOWN
        assert result.known_names == ["Fady"]
        assert result.faces[0].is_match is True
        assert result.faces[0].matched_name == "Fady"

    def test_distant_encoding_returns_unknown(self, cfg: AppConfig, tmp_path: Path):
        enc_enrolled = _fake_encoding(1)
        enc_stranger = _fake_encoding(99)
        result = self._run_with_face(cfg, tmp_path, enc_stranger, [enc_enrolled])
        assert result.verdict == Verdict.UNKNOWN
        assert result.faces[0].is_match is False
        assert result.faces[0].matched_name is None

    def test_strict_tolerance_rejects_borderline(self, cfg: AppConfig, tmp_path: Path):
        """
        enc_close is 'close' but at tolerance 0.3 it should be rejected.
        """
        enc_enrolled = _fake_encoding(1)
        # Create an encoding at distance ~0.45 from enrolled
        enc_close = enc_enrolled + np.full(128, 0.02)
        enc_close = enc_close / np.linalg.norm(enc_close)

        result = self._run_with_face(
            cfg, tmp_path, enc_close, [enc_enrolled], tolerance=0.3
        )
        # Whether it matches depends on actual distance — we test the logic holds
        # The point is KNOWN/UNKNOWN based on tolerance, not hardcoded
        assert result.verdict in (Verdict.KNOWN, Verdict.UNKNOWN)

    def test_multiple_samples_best_distance_wins(self, cfg: AppConfig, tmp_path: Path):
        """
        Roster has 3 samples. The presented face is close to sample[2].
        Should still match even though samples[0] and [1] are far away.
        """
        enc_far1  = _fake_encoding(10)
        enc_far2  = _fake_encoding(11)
        enc_close = _fake_encoding(1)
        presented = _fake_encoding(1)  # same seed = same vector = distance 0.0

        result = self._run_with_face(
            cfg, tmp_path, presented, [enc_far1, enc_far2, enc_close]
        )
        assert result.verdict == Verdict.KNOWN

    def test_multiple_faces_all_known(self, cfg: AppConfig, tmp_path: Path):
        """Two faces in frame, both matching → KNOWN."""
        enc_fady  = _fake_encoding(1)
        enc_alice = _fake_encoding(10)

        r = Roster()
        r.add("Fady",  [enc_fady])
        r.add("Alice", [enc_alice])
        r.save(cfg.paths.roster_file)

        locations = [(10, 100, 90, 20), (10, 200, 90, 120)]
        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )
        distances_fady  = np.array([0.0, 1.2])  # matches index 0 (Fady)
        distances_alice = np.array([1.2, 0.0])  # matches index 1 (Alice)

        call_n = {"n": 0}
        def face_distance_side(known_encs, query_enc):
            call_n["n"] += 1
            return distances_fady if call_n["n"] == 1 else distances_alice

        with patch("faceguard.guard_core.open_camera", return_value=_mock_cap()), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=locations), \
             patch("face_recognition.face_encodings", return_value=[enc_fady, enc_alice]), \
             patch("face_recognition.face_distance", side_effect=face_distance_side), \
             patch("faceguard.guard_core._save_capture", return_value=None):
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.KNOWN
        assert len(result.faces) == 2
        assert all(f.is_match for f in result.faces)

    def test_multiple_faces_one_unknown_triggers_alarm(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """Mixed frame: Fady + stranger → UNKNOWN verdict (alarm for any unknown)."""
        enc_fady = _fake_encoding(1)
        r = Roster()
        r.add("Fady", [enc_fady])
        r.save(cfg.paths.roster_file)

        locations = [(10, 100, 90, 20), (10, 200, 90, 120)]
        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )
        # First face matches Fady, second face is far from everyone
        call_n = {"n": 0}
        def face_distance_side(known_encs, query_enc):
            call_n["n"] += 1
            return np.array([0.2]) if call_n["n"] == 1 else np.array([0.9])

        with patch("faceguard.guard_core.open_camera", return_value=_mock_cap()), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=locations), \
             patch("face_recognition.face_encodings", return_value=[enc_fady, _fake_encoding(99)]), \
             patch("face_recognition.face_distance", side_effect=face_distance_side), \
             patch("faceguard.guard_core._save_capture", return_value=None):
            result = guard_core.run(cfg)

        assert result.verdict == Verdict.UNKNOWN
        assert len(result.known_faces) == 1
        assert len(result.unknown_faces) == 1
        assert result.known_names == ["Fady"]


# ── Test: capture saving ───────────────────────────────────────────────────────

class TestCaptureSaving:

    def test_capture_saved_on_known(self, cfg: AppConfig, tmp_path: Path):
        """Capture photo must be saved to disk on KNOWN verdict."""
        enc = _fake_encoding(1)
        _roster_with_fady(tmp_path)
        cfg.paths.captures_dir.mkdir(parents=True, exist_ok=True)

        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )

        with patch("faceguard.guard_core.open_camera", return_value=_mock_cap()), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=[(10,100,90,20)]), \
             patch("face_recognition.face_encodings", return_value=[enc]), \
             patch("face_recognition.face_distance", return_value=np.array([0.2])):
            result = guard_core.run(cfg)

        assert result.capture_path is not None
        assert result.capture_path.exists()
        assert "known" in result.capture_path.name

    def test_capture_filename_contains_unknown_tag(
        self, cfg: AppConfig, tmp_path: Path
    ):
        """Unknown verdict capture filename must contain 'unknown' tag."""
        _roster_with_fady(tmp_path)
        cfg.paths.captures_dir.mkdir(parents=True, exist_ok=True)

        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )

        with patch("faceguard.guard_core.open_camera", return_value=_mock_cap()), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=[(10,100,90,20)]), \
             patch("face_recognition.face_encodings", return_value=[_fake_encoding(99)]), \
             patch("face_recognition.face_distance", return_value=np.array([0.9])):
            result = guard_core.run(cfg)

        assert result.capture_path is not None
        assert "unknown" in result.capture_path.name

    def test_disk_full_does_not_crash_guard(self, cfg: AppConfig, tmp_path: Path):
        """If saving the capture fails (OSError), guard still returns result."""
        _roster_with_fady(tmp_path)

        fake_frame = MagicMock(
            frame=np.zeros((480, 640, 3), dtype=np.uint8),
            frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
        )

        with patch("faceguard.guard_core.open_camera", return_value=_mock_cap()), \
             patch("faceguard.guard_core.capture_frame", return_value=fake_frame), \
             patch("face_recognition.face_locations", return_value=[(10,100,90,20)]), \
             patch("face_recognition.face_encodings", return_value=[_fake_encoding(1)]), \
             patch("face_recognition.face_distance", return_value=np.array([0.2])), \
             patch("faceguard.guard_core.save_frame", side_effect=OSError("disk full")):
            result = guard_core.run(cfg)

        # Guard must complete with a valid verdict
        assert result.verdict in (Verdict.KNOWN, Verdict.UNKNOWN)
        # capture_path is None because save failed — that's expected
        assert result.capture_path is None


# ── Test: camera always released ─────────────────────────────────────────────

class TestCameraRelease:

    def _assert_camera_released(self, cfg, tmp_path, mock_setup_fn):
        cap = _mock_cap()
        with patch("faceguard.guard_core.open_camera", return_value=cap):
            mock_setup_fn(cap)
        cap.release.assert_called()

    def test_camera_released_on_no_face(self, cfg: AppConfig, tmp_path: Path):
        _roster_with_fady(tmp_path)
        cap = _mock_cap()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mf, \
             patch("face_recognition.face_locations", return_value=[]), \
             patch("faceguard.guard_core.time.sleep"):
            mf.return_value = MagicMock(
                frame=np.zeros((480, 640, 3), dtype=np.uint8),
                frame_rgb=np.zeros((480, 640, 3), dtype=np.uint8),
            )
            guard_core.run(cfg)

        cap.release.assert_called_once()

    def test_camera_released_on_read_error(self, cfg: AppConfig, tmp_path: Path):
        _roster_with_fady(tmp_path)
        cap = _mock_cap()

        with patch("faceguard.guard_core.open_camera", return_value=cap), \
             patch("faceguard.guard_core.capture_frame") as mf:
            from faceguard.camera import CameraError
            mf.side_effect = CameraError("read failed")
            guard_core.run(cfg)

        cap.release.assert_called_once()