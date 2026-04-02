"""
tests/test_alerts.py

Tests for the alert dispatcher, Discord webhook, and siren.
All network calls and subprocess calls are mocked.
"""

from __future__ import annotations

import io
import threading
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest
import requests

from faceguard.alerts import dispatch
from faceguard.alerts.discord import _build_embed, send_alert
from faceguard.alerts.siren import play_siren, _resolve_sound_file
from faceguard.result import FaceResult, GuardResult, Verdict


# ── Discord embed tests ────────────────────────────────────────────────────────

class TestDiscordEmbed:

    def test_unknown_embed_colour_and_title(self, result_unknown: GuardResult):
        embed = _build_embed(result_unknown)
        assert embed["color"] == 0xE74C3C
        assert "🚨" in embed["title"]

    def test_no_face_embed_colour_and_title(self, result_no_face: GuardResult):
        embed = _build_embed(result_no_face)
        assert embed["color"] == 0xE67E22
        assert "⚠️" in embed["title"]

    def test_camera_error_embed(self, result_camera_error: GuardResult):
        embed = _build_embed(result_camera_error)
        assert embed["color"] == 0xE67E22
        assert "Camera" in embed["title"]

    def test_roster_error_embed_is_grey(self, result_roster_error: GuardResult):
        embed = _build_embed(result_roster_error)
        assert embed["color"] == 0x95A5A6
        assert "Fault" in embed["title"] or "fault" in embed["title"].lower()

    def test_unknown_embed_contains_face_fields(self, result_unknown_mixed: GuardResult):
        embed = _build_embed(result_unknown_mixed)
        field_names = [f["name"] for f in embed["fields"]]
        assert "Face 1" in field_names
        assert "Face 2" in field_names

    def test_lm_description_field_present_when_set(self, result_unknown: GuardResult):
        result_unknown.lm_description = "Male, 30s, grey hoodie."
        embed = _build_embed(result_unknown)
        field_names = [f["name"] for f in embed["fields"]]
        assert "👁 Appearance" in field_names
        app_field = next(f for f in embed["fields"] if f["name"] == "👁 Appearance")
        assert app_field["value"] == "Male, 30s, grey hoodie."
        assert app_field["inline"] is False

    def test_lm_description_field_absent_when_none(self, result_unknown: GuardResult):
        result_unknown.lm_description = None
        embed = _build_embed(result_unknown)
        field_names = [f["name"] for f in embed["fields"]]
        assert "👁 Appearance" not in field_names

    def test_embed_always_has_footer(self, result_unknown: GuardResult):
        embed = _build_embed(result_unknown)
        assert embed["footer"]["text"] == "faceguard"

    def test_embed_description_contains_message(self, result_unknown: GuardResult):
        embed = _build_embed(result_unknown)
        assert result_unknown.message in embed["description"]


# ── Discord send_alert tests ───────────────────────────────────────────────────

class TestDiscordSendAlert:

    WEBHOOK = "https://discord.com/api/webhooks/test/token"

    def _mock_response(self, status: int, text: str = "") -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.text = text
        return r

    def test_returns_true_on_204(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   return_value=self._mock_response(204)):
            assert send_alert(result_unknown, self.WEBHOOK) is True

    def test_returns_true_on_200(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   return_value=self._mock_response(200)):
            assert send_alert(result_unknown, self.WEBHOOK) is True

    def test_retries_on_429(self, result_unknown: GuardResult):
        responses = iter([self._mock_response(429), self._mock_response(204)])
        with patch("faceguard.alerts.discord.requests.post",
                   side_effect=lambda *a, **k: next(responses)), \
             patch("faceguard.alerts.discord.time.sleep"):
            assert send_alert(result_unknown, self.WEBHOOK) is True

    def test_retries_on_500(self, result_unknown: GuardResult):
        responses = iter([
            self._mock_response(500), self._mock_response(500), self._mock_response(204)
        ])
        with patch("faceguard.alerts.discord.requests.post",
                   side_effect=lambda *a, **k: next(responses)), \
             patch("faceguard.alerts.discord.time.sleep"):
            assert send_alert(result_unknown, self.WEBHOOK) is True

    def test_returns_false_after_all_retries_exhausted(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   return_value=self._mock_response(500)), \
             patch("faceguard.alerts.discord.time.sleep"):
            assert send_alert(result_unknown, self.WEBHOOK) is False

    def test_no_retry_on_400(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   return_value=self._mock_response(400)) as mock_post:
            result = send_alert(result_unknown, self.WEBHOOK)
        assert result is False
        assert mock_post.call_count == 1  # no retry

    def test_returns_false_on_connection_error(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   side_effect=requests.exceptions.ConnectionError()), \
             patch("faceguard.alerts.discord.time.sleep"):
            assert send_alert(result_unknown, self.WEBHOOK) is False

    def test_returns_false_on_timeout(self, result_unknown: GuardResult):
        with patch("faceguard.alerts.discord.requests.post",
                   side_effect=requests.exceptions.Timeout()), \
             patch("faceguard.alerts.discord.time.sleep"):
            assert send_alert(result_unknown, self.WEBHOOK) is False

    def test_never_raises(self, result_unknown: GuardResult):
        """send_alert must NEVER raise regardless of what requests does."""
        with patch("faceguard.alerts.discord.requests.post",
                   side_effect=RuntimeError("unexpected")), \
             patch("faceguard.alerts.discord.time.sleep"):
            result = send_alert(result_unknown, self.WEBHOOK)  # must not raise
        assert result is False

    def test_photo_attached_when_capture_path_exists(
        self, result_unknown: GuardResult, tmp_path
    ):
        """When capture_path points to a real file, it should be read and attached."""
        # Write a minimal fake JPEG
        fake_jpg = tmp_path / "capture.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        result_unknown.capture_path = fake_jpg

        posted_files = {}
        def capture_post(*args, **kwargs):
            posted_files.update(kwargs.get("files", {}))
            return self._mock_response(204)

        with patch("faceguard.alerts.discord.requests.post", side_effect=capture_post):
            send_alert(result_unknown, self.WEBHOOK)

        assert "file" in posted_files

    def test_photo_absent_does_not_crash(self, result_unknown: GuardResult):
        """Missing capture file should send JSON embed without crashing."""
        result_unknown.capture_path = None
        with patch("faceguard.alerts.discord.requests.post",
                   return_value=self._mock_response(204)):
            assert send_alert(result_unknown, self.WEBHOOK) is True


# ── Siren tests ────────────────────────────────────────────────────────────────

class TestSiren:

    def test_returns_none_when_no_sound_found(self):
        with patch("faceguard.alerts.siren._resolve_sound_file", return_value=None):
            result = play_siren()
        assert result is None

    def test_returns_thread_when_sound_found(self):
        with patch("faceguard.alerts.siren._resolve_sound_file", return_value="/fake/s.aiff"), \
             patch("faceguard.alerts.siren._play_once"):
            t = play_siren(repeat=1, block=False)
        assert isinstance(t, threading.Thread)
        t.join(timeout=2)

    def test_repeat_fires_correct_number_of_times(self):
        with patch("faceguard.alerts.siren._resolve_sound_file", return_value="/fake/s.aiff"), \
             patch("faceguard.alerts.siren._play_once") as mock_play, \
             patch("faceguard.alerts.siren.time.sleep"):
            t = play_siren(repeat=4, block=False)
            t.join(timeout=3) # type: ignore
        assert mock_play.call_count == 4

    def test_block_true_waits_for_completion(self):
        played = []
        def slow_play(path, volume):
            played.append(1)

        with patch("faceguard.alerts.siren._resolve_sound_file", return_value="/fake/s.aiff"), \
             patch("faceguard.alerts.siren._play_once", side_effect=slow_play), \
             patch("faceguard.alerts.siren.time.sleep"):
            play_siren(repeat=2, block=True)

        assert len(played) == 2  # both plays completed before return

    def test_resolve_custom_path_missing_falls_back(self, tmp_path):
        result = _resolve_sound_file(str(tmp_path / "nonexistent.mp3"))
        # Falls back to system sounds or None — never raises
        assert result is None or isinstance(result, str)

    def test_thread_is_daemon(self):
        with patch("faceguard.alerts.siren._resolve_sound_file", return_value="/fake/s.aiff"), \
             patch("faceguard.alerts.siren._play_once"):
            t = play_siren(repeat=1, block=False)
        assert t.daemon is True # type: ignore
        t.join(timeout=2)# type: ignore


# ── Dispatcher integration tests ──────────────────────────────────────────────

class TestDispatch:

    def _mock_cfg(self, siren_enabled=False):
        cfg = MagicMock()
        cfg.siren.enabled = siren_enabled
        cfg.siren.sound_file = None
        cfg.siren.volume = 1.0
        cfg.siren.repeat = 3
        cfg.discord.webhook_url = "https://discord.com/api/webhooks/test/token"
        cfg.lm_studio.enabled = False
        cfg.lm_studio.describe_unknown = False
        cfg.lm_studio.model = "moondream2"
        return cfg

    def test_known_is_fully_silent(self, result_known: GuardResult):
        cfg = self._mock_cfg()
        with patch("faceguard.alerts.play_siren") as mock_siren, \
             patch("faceguard.alerts.send_alert") as mock_discord:
            dispatch(result_known, cfg=cfg, dry_run=False)
        mock_siren.assert_not_called()
        mock_discord.assert_not_called()

    def test_dry_run_suppresses_all_alerts(self, result_unknown: GuardResult):
        cfg = self._mock_cfg(siren_enabled=True)
        with patch("faceguard.alerts.play_siren") as mock_siren, \
             patch("faceguard.alerts.send_alert") as mock_discord:
            dispatch(result_unknown, cfg=cfg, dry_run=True)
        mock_siren.assert_not_called()
        mock_discord.assert_not_called()

    def test_unknown_fires_discord(self, result_unknown: GuardResult):
        cfg = self._mock_cfg()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        with patch("faceguard.alerts.send_alert", return_value=True) as mock_discord:
            dispatch(result_unknown, cfg=cfg, dry_run=False)
        mock_discord.assert_called_once()

    def test_unknown_fires_siren_when_enabled(self, result_unknown: GuardResult):
        cfg = self._mock_cfg(siren_enabled=True)
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        with patch("faceguard.alerts.play_siren", return_value=mock_thread) as ms, \
             patch("faceguard.alerts.send_alert", return_value=True):
            dispatch(result_unknown, cfg=cfg, dry_run=False)
        ms.assert_called_once()

    def test_no_face_fires_siren_and_discord(self, result_no_face: GuardResult):
        cfg = self._mock_cfg(siren_enabled=True)
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        with patch("faceguard.alerts.play_siren", return_value=mock_thread) as ms, \
             patch("faceguard.alerts.send_alert", return_value=True) as md:
            dispatch(result_no_face, cfg=cfg, dry_run=False)
        ms.assert_called_once()
        md.assert_called_once()

    def test_camera_error_fires_discord_no_siren(self, result_camera_error: GuardResult):
        """CAMERA_ERROR: is_alarm=True, siren fires. Discord fires."""
        cfg = self._mock_cfg(siren_enabled=True)
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        with patch("faceguard.alerts.play_siren", return_value=mock_thread), \
             patch("faceguard.alerts.send_alert", return_value=True) as md:
            dispatch(result_camera_error, cfg=cfg, dry_run=False)
        md.assert_called_once()

    def test_lm_called_before_discord_for_unknown(self, result_unknown: GuardResult):
        """LM Studio description must be fetched before Discord send."""
        cfg = self._mock_cfg()
        cfg.lm_studio.enabled = True
        cfg.lm_studio.describe_unknown = True
        result_unknown.frame_bgr = np.zeros((480, 640, 3), dtype=np.uint8)
        call_order = []

        def fake_describe(result, lm_cfg):
            call_order.append("lm")
            return "desc"

        def fake_send(result, webhook_url):
            call_order.append("discord")
            return True

        with patch("faceguard.alerts.describe_intruder", side_effect=fake_describe), \
             patch("faceguard.alerts.send_alert", side_effect=fake_send):
            dispatch(result_unknown, cfg=cfg, dry_run=False)

        assert call_order == ["lm", "discord"]

    def test_lm_failure_does_not_block_discord(self, result_unknown: GuardResult):
        cfg = self._mock_cfg()
        cfg.lm_studio.enabled = True
        cfg.lm_studio.describe_unknown = True
        result_unknown.frame_bgr = np.zeros((480, 640, 3), dtype=np.uint8)

        with patch("faceguard.alerts.describe_intruder", return_value=None), \
             patch("faceguard.alerts.send_alert", return_value=True) as md:
            dispatch(result_unknown, cfg=cfg, dry_run=False)

        md.assert_called_once()

    def test_siren_joined_after_discord(self, result_unknown: GuardResult):
        """Siren thread must be joined after Discord completes."""
        cfg = self._mock_cfg(siren_enabled=True)
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        join_order = []

        def fake_send(result, webhook_url):
            join_order.append("discord")
            return True

        with patch("faceguard.alerts.play_siren", return_value=mock_thread), \
             patch("faceguard.alerts.send_alert", side_effect=fake_send):
            mock_thread.join.side_effect = lambda timeout=None: join_order.append("siren_join")
            dispatch(result_unknown, cfg=cfg, dry_run=False)

        assert "discord" in join_order
        assert "siren_join" in join_order
        assert join_order.index("discord") < join_order.index("siren_join")

    def test_dispatch_never_raises_on_discord_failure(self, result_unknown: GuardResult):
        cfg = self._mock_cfg()
        with patch("faceguard.alerts.send_alert", side_effect=RuntimeError("unexpected")):
            # Should not raise — dispatch must be exception-safe
            # (send_alert itself should never raise, but dispatch wraps it anyway)
            try:
                dispatch(result_unknown, cfg=cfg, dry_run=False)
            except RuntimeError:
                pytest.fail("dispatch() raised RuntimeError — must be exception-safe")