"""
tests/test_vision.py

Tests for the LM Studio vision layer.
All HTTP calls are mocked — no real LM Studio required.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from faceguard.config import LMStudioConfig
from faceguard.result import GuardResult, Verdict
from faceguard.vision import describe_intruder, _prepare_image_b64, _MAX_DIMENSION


# ── Image preprocessing ────────────────────────────────────────────────────────

class TestImagePrep:

    def test_large_frame_is_downscaled(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        b64 = _prepare_image_b64(frame)
        raw = base64.b64decode(b64)
        import cv2
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert max(img.shape[:2]) <= _MAX_DIMENSION # type: ignore

    def test_small_frame_not_upscaled(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        b64 = _prepare_image_b64(frame)
        raw = base64.b64decode(b64)
        import cv2
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[1] == 160  # width unchanged # type: ignore

    def test_aspect_ratio_preserved(self):
        """Downscaling must preserve aspect ratio — no stretching."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)  # 4:3
        b64 = _prepare_image_b64(frame)
        raw = base64.b64decode(b64)
        import cv2
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2] # type: ignore
        assert abs(w / h - 640 / 480) < 0.01

    def test_output_is_valid_base64(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        b64 = _prepare_image_b64(frame)
        # Must not raise
        decoded = base64.b64decode(b64)
        assert len(decoded) > 0

    def test_output_is_jpeg(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        b64 = _prepare_image_b64(frame)
        decoded = base64.b64decode(b64)
        # JPEG magic bytes: FF D8 FF
        assert decoded[:3] == b"\xff\xd8\xff"


# ── describe_intruder guard conditions ────────────────────────────────────────

class TestDescribeIntruderGuards:

    def _lm_cfg(self) -> LMStudioConfig:
        return LMStudioConfig(
            enabled=True,
            base_url="http://localhost:1234/v1",
            model="moondream2",
            timeout_seconds=2,
            describe_unknown=True,
        )

    def test_returns_none_for_known_verdict(self):
        gr = GuardResult(verdict=Verdict.KNOWN, hostname="mac", message="ok")
        assert describe_intruder(gr, self._lm_cfg()) is None

    def test_returns_none_for_no_face_verdict(self):
        gr = GuardResult(verdict=Verdict.NO_FACE, hostname="mac", message="no face")
        assert describe_intruder(gr, self._lm_cfg()) is None

    def test_returns_none_for_camera_error_verdict(self):
        gr = GuardResult(verdict=Verdict.CAMERA_ERROR, hostname="mac", message="err")
        assert describe_intruder(gr, self._lm_cfg()) is None

    def test_returns_none_when_frame_is_none(self):
        gr = GuardResult(
            verdict=Verdict.UNKNOWN, hostname="mac", message="alert", frame_bgr=None
        )
        assert describe_intruder(gr, self._lm_cfg()) is None


# ── describe_intruder network failure modes ───────────────────────────────────

class TestDescribeIntruderNetworkFailures:

    def _unknown_result(self) -> GuardResult:
        return GuardResult(
            verdict=Verdict.UNKNOWN,
            hostname="mac",
            message="alert",
            frame_bgr=np.zeros((480, 640, 3), dtype=np.uint8),
        )

    def _lm_cfg(self) -> LMStudioConfig:
        return LMStudioConfig(
            enabled=True,
            base_url="http://localhost:1234/v1",
            model="moondream2",
            timeout_seconds=2,
            describe_unknown=True,
        )

    def test_connection_refused_returns_none(self):
        gr = self._unknown_result()
        with patch("faceguard.vision.requests.post",
                   side_effect=requests.exceptions.ConnectionError("refused")):
            assert describe_intruder(gr, self._lm_cfg()) is None

    def test_timeout_returns_none(self):
        gr = self._unknown_result()
        with patch("faceguard.vision.requests.post",
                   side_effect=requests.exceptions.Timeout()):
            assert describe_intruder(gr, self._lm_cfg()) is None

    def test_http_500_returns_none(self):
        gr = self._unknown_result()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_resp
        )
        with patch("faceguard.vision.requests.post", return_value=mock_resp):
            assert describe_intruder(gr, self._lm_cfg()) is None

    def test_empty_choices_returns_none(self):
        gr = self._unknown_result()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"choices": []}
        with patch("faceguard.vision.requests.post", return_value=mock_resp):
            assert describe_intruder(gr, self._lm_cfg()) is None

    def test_empty_content_returns_none(self):
        gr = self._unknown_result()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "   "}}]  # whitespace only
        }
        with patch("faceguard.vision.requests.post", return_value=mock_resp):
            assert describe_intruder(gr, self._lm_cfg()) is None

    def test_unexpected_exception_returns_none(self):
        gr = self._unknown_result()
        with patch("faceguard.vision.requests.post",
                   side_effect=RuntimeError("completely unexpected")):
            result = describe_intruder(gr, self._lm_cfg())  # must not raise
        assert result is None

    def test_successful_response_returns_description(self):
        gr = self._unknown_result()
        expected = "Male, approximately 30s, dark hair, wearing a grey hoodie."
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": expected}}]
        }
        with patch("faceguard.vision.requests.post", return_value=mock_resp):
            result = describe_intruder(gr, self._lm_cfg())
        assert result == expected

    def test_description_is_stripped(self):
        """Leading/trailing whitespace in model output must be stripped."""
        gr = self._unknown_result()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "  A description.  \n"}}]
        }
        with patch("faceguard.vision.requests.post", return_value=mock_resp):
            result = describe_intruder(gr, self._lm_cfg())
        assert result == "A description."

    def test_request_payload_has_image_url(self):
        """The API call must include a base64 image_url content block."""
        gr = self._unknown_result()
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": "A person."}}]
            }
            return resp

        with patch("faceguard.vision.requests.post", side_effect=capture_post):
            describe_intruder(gr, self._lm_cfg())

        messages = captured_payload.get("messages", [])
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        assert user_msg is not None
        content_blocks = user_msg["content"]
        image_blocks = [b for b in content_blocks if b.get("type") == "image_url"]
        assert len(image_blocks) == 1
        url = image_blocks[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_max_tokens_is_bounded(self):
        """Request must cap max_tokens to prevent runaway generation."""
        gr = self._unknown_result()
        captured = {}

        def capture_post(url, json=None, timeout=None):
            captured.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": "desc"}}]
            }
            return resp

        with patch("faceguard.vision.requests.post", side_effect=capture_post):
            describe_intruder(gr, self._lm_cfg())

        assert captured.get("max_tokens", 9999) <= 200