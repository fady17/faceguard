"""
tests/conftest.py — shared pytest fixtures

Fixtures are designed to be:
  - Self-contained: each test gets its own temp directory, no shared state
  - Realistic: fake face encodings are the correct numpy dtype and shape
  - Composable: a fixture can use another fixture as a parameter
  - Zero hardware: no camera, no Discord, no LM Studio, no afplay required

Naming convention:
  cfg_*     — AppConfig variants (minimal, full, lm_disabled, etc.)
  roster_*  — Roster objects with various states
  result_*  — GuardResult objects for each verdict type
  frame_*   — numpy arrays simulating camera frames
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import numpy as np
import pytest

# Ensure the package root is importable from tests/
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from faceguard.config import (
    AppConfig, DiscordConfig, GuardConfig, LMStudioConfig,
    PathsConfig, RecognitionConfig, SirenConfig,
)
from faceguard.logger import init_logger
from faceguard.result import FaceResult, GuardResult, Verdict
from faceguard.roster import Roster


# ── Temp directory per test ────────────────────────────────────────────────────

@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """A clean temp directory for each test. Uses pytest's built-in tmp_path."""
    return tmp_path


@pytest.fixture(autouse=True)
def _init_logger(tmp_path: Path):
    """
    Initialise the logger for every test automatically.
    Without this, any test that exercises a code path with get_logger()
    would fall back to /tmp/faceguard_logs — which is fine but noisy.
    autouse=True means every test gets this without asking for it.
    """
    init_logger(logs_dir=tmp_path / "logs", verbose=False)


# ── Config fixtures ────────────────────────────────────────────────────────────

def _make_paths(base: Path) -> PathsConfig:
    return PathsConfig(
        roster_file=base / "roster.pkl",
        captures_dir=base / "captures",
        enrolled_dir=base / "enrolled",
        logs_dir=base / "logs",
    )


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    """
    Minimal valid AppConfig pointing to tmp_path.
    LM Studio enabled but pointing at a port nothing is listening on.
    """
    return AppConfig(
        discord=DiscordConfig(
            webhook_url="https://discord.com/api/webhooks/test/token",
        ),
        lm_studio=LMStudioConfig(
            enabled=True,
            base_url="http://localhost:19999/v1",  # nothing listens here
            model="moondream2",
            timeout_seconds=2,
            describe_unknown=True,
        ),
        recognition=RecognitionConfig(
            tolerance=0.5,
            capture_retries=1,
            capture_retry_delay_seconds=0.0,
            no_face_retries=1,
            no_face_retry_delay_seconds=0.0,
            camera_index=0,
        ),
        siren=SirenConfig(
            enabled=False,   # disabled by default in tests — no sound during CI
            sound_file=None,
            volume=1.0,
            repeat=1,
        ),
        paths=_make_paths(tmp_path),
        guard=GuardConfig(
            startup_delay_seconds=0,
            pid_file=tmp_path / "faceguard.pid",
        ),
    )


@pytest.fixture
def cfg_lm_disabled(cfg: AppConfig) -> AppConfig:
    """AppConfig with LM Studio disabled."""
    cfg.lm_studio.enabled = False
    return cfg


@pytest.fixture
def cfg_strict_tolerance(cfg: AppConfig) -> AppConfig:
    """AppConfig with very strict face matching tolerance."""
    cfg.recognition.tolerance = 0.3
    return cfg


@pytest.fixture
def cfg_loose_tolerance(cfg: AppConfig) -> AppConfig:
    """AppConfig with loose face matching tolerance."""
    cfg.recognition.tolerance = 0.7
    return cfg


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    """
    Write a valid config.json to disk and return its path.
    Used for testing the config loader directly.
    """
    config_data = {
        "discord": {
            "webhook_url": "https://discord.com/api/webhooks/test/token",
            "alert_channel_name": "security-alerts",
            "log_known_entries": False,
        },
        "lm_studio": {
            "enabled": True,
            "base_url": "http://localhost:1234/v1",
            "model": "moondream2",
            "timeout_seconds": 10,
            "describe_unknown": True,
        },
        "recognition": {
            "tolerance": 0.5,
            "capture_retries": 3,
            "capture_retry_delay_seconds": 0.0,
            "no_face_retries": 3,
            "no_face_retry_delay_seconds": 0.0,
            "camera_index": 0,
        },
        "siren": {
            "enabled": False,
            "sound_file": None,
            "volume": 1.0,
            "repeat": 3,
        },
        "paths": {
            "roster_file":   str(tmp_path / "roster.pkl"),
            "captures_dir":  str(tmp_path / "captures"),
            "enrolled_dir":  str(tmp_path / "enrolled"),
            "logs_dir":      str(tmp_path / "logs"),
        },
        "guard": {
            "startup_delay_seconds": 0,
            "pid_file": str(tmp_path / "faceguard.pid"),
        },
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config_data))
    return p


# ── Face encoding fixtures ─────────────────────────────────────────────────────

def _make_encoding(seed: int = 0) -> np.ndarray:
    """
    Create a deterministic 128-d float64 unit-normalised vector.
    face_recognition returns unit-normalised encodings so we match that exactly.
    Using a seed makes tests deterministic — same seed = same encoding every run.
    """
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(128).astype(np.float64)
    return v / np.linalg.norm(v)


@pytest.fixture
def enc_fady() -> np.ndarray:
    return _make_encoding(seed=1)


@pytest.fixture
def enc_fady_alt() -> np.ndarray:
    """Second sample for Fady (slightly different angle simulation)."""
    return _make_encoding(seed=2)


@pytest.fixture
def enc_unknown() -> np.ndarray:
    """An encoding far from Fady — guaranteed not to match at tolerance 0.5."""
    return _make_encoding(seed=99)


# ── Roster fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def roster_empty() -> Roster:
    return Roster()


@pytest.fixture
def roster_one(enc_fady: np.ndarray, enc_fady_alt: np.ndarray) -> Roster:
    """Roster with one enrolled person (Fady, 2 samples)."""
    r = Roster()
    r.add("Fady", [enc_fady, enc_fady_alt], enrolled_photo="/fake/fady.jpg")
    return r


@pytest.fixture
def roster_two(enc_fady: np.ndarray, tmp_path: Path) -> Roster:
    """Roster with two different people."""
    alice_enc = _make_encoding(seed=10)
    r = Roster()
    r.add("Fady",  [enc_fady],    enrolled_photo="/fake/fady.jpg")
    r.add("Alice", [alice_enc],   enrolled_photo="/fake/alice.jpg")
    return r


@pytest.fixture
def roster_file(roster_one: Roster, tmp_path: Path) -> Path:
    """Save roster_one to disk and return the path."""
    path = tmp_path / "roster.pkl"
    roster_one.save(path)
    return path


# ── Frame fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def frame_blank() -> np.ndarray:
    """Solid black 640x480 BGR frame — represents a covered camera."""
    return np.zeros((480, 640, 3), dtype=np.uint8)


@pytest.fixture
def frame_grey() -> np.ndarray:
    """Mid-grey 640x480 frame — represents a dark room."""
    return np.full((480, 640, 3), 128, dtype=np.uint8)


@pytest.fixture
def frame_noise() -> np.ndarray:
    """Random noise frame — camera malfunction or extreme low light."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)


# ── GuardResult fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def result_known(frame_blank: np.ndarray, tmp_path: Path) -> GuardResult:
    return GuardResult(
        verdict=Verdict.KNOWN,
        hostname="test-mac",
        message="Access granted: Fady on test-mac.",
        frame_bgr=frame_blank,
        capture_path=tmp_path / "known.jpg",
        faces=[FaceResult(0, (10, 100, 90, 20), "Fady", 0.32, True)],
    )


@pytest.fixture
def result_unknown(frame_blank: np.ndarray, tmp_path: Path) -> GuardResult:
    return GuardResult(
        verdict=Verdict.UNKNOWN,
        hostname="test-mac",
        message="Intruder alert on test-mac — 1 unknown face(s).",
        frame_bgr=frame_blank,
        capture_path=tmp_path / "unknown.jpg",
        faces=[FaceResult(0, (10, 100, 90, 20), None, 0.71, False)],
    )


@pytest.fixture
def result_unknown_mixed(frame_blank: np.ndarray, tmp_path: Path) -> GuardResult:
    """Two faces: one known, one unknown — the multi-face alarm scenario."""
    return GuardResult(
        verdict=Verdict.UNKNOWN,
        hostname="test-mac",
        message="Intruder alert on test-mac — known: Fady, 1 unknown face(s).",
        frame_bgr=frame_blank,
        capture_path=tmp_path / "mixed.jpg",
        faces=[
            FaceResult(0, (10, 100, 90, 20),  "Fady", 0.35, True),
            FaceResult(1, (10, 200, 90, 120), None,   0.72, False),
        ],
    )


@pytest.fixture
def result_no_face(frame_blank: np.ndarray) -> GuardResult:
    return GuardResult(
        verdict=Verdict.NO_FACE,
        hostname="test-mac",
        message="No face detected after 3 attempt(s).",
        frame_bgr=frame_blank,
        face_retries=3,
    )


@pytest.fixture
def result_camera_error() -> GuardResult:
    return GuardResult(
        verdict=Verdict.CAMERA_ERROR,
        hostname="test-mac",
        message="Camera could not be opened on test-mac.",
        error_detail="Could not open camera at index 0 after 3 attempts.",
        camera_attempts=3,
    )


@pytest.fixture
def result_roster_error() -> GuardResult:
    return GuardResult(
        verdict=Verdict.ROSTER_ERROR,
        hostname="test-mac",
        message="Roster is empty. Run: python enroll.py add <your-name>",
        error_detail="No enrolled faces found.",
    )