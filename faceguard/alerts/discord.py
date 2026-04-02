"""
faceguard/alerts/discord.py

Discord webhook alert dispatch.

Why webhooks and not a bot:
  A webhook is a single URL — no OAuth, no bot token, no server permissions to
  configure. It takes 30 seconds to create and the URL is the only secret.
  For a personal security alert this is all we need.

Message structure per verdict:
  UNKNOWN      — 🚨 embed with photo attached, per-face breakdown, distance scores
  NO_FACE      — ⚠️  embed with captured frame (may be blank/dark), obstruction warning
  CAMERA_ERROR — ⚠️  text-only (no frame available), hostname + error detail
  ROSTER_ERROR / CONFIG_ERROR — ⚠️  text-only system fault notification

Why multipart/form-data instead of JSON:
  Attaching a photo requires multipart. We always send multipart even for
  text-only alerts so the sending code stays uniform — an empty files dict
  is valid multipart and Discord handles it fine.

Retry policy:
  2 retries, exponential backoff: 1s → 2s.
  Total ceiling: ~3s of wait time beyond the initial attempt.
  After all retries are exhausted: log the failure and return — never raise.
  A failed Discord send must NEVER crash the guard or delay the exit.

Rate limiting:
  Discord webhooks are rate-limited to ~30 req/5s per webhook.
  We send at most one message per guard run so we will never hit this.
  We handle 429 responses defensively anyway (treat as retriable).
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from faceguard.result import GuardResult, Verdict
from faceguard.logger import get_logger


# ── Retry config ───────────────────────────────────────────────────────────────
_MAX_RETRIES   = 2
_RETRY_DELAYS  = [1.0, 2.0]   # seconds between attempts
_SEND_TIMEOUT  = 8             # seconds per individual HTTP request


# ── Embed colour codes (Discord uses decimal integers) ────────────────────────
_COLOUR_RED    = 0xE74C3C   # UNKNOWN
_COLOUR_ORANGE = 0xE67E22   # NO_FACE / CAMERA_ERROR
_COLOUR_GREY   = 0x95A5A6   # system fault (ROSTER_ERROR / CONFIG_ERROR)


def _build_embed(result: GuardResult) -> dict:
    """Build the Discord embed object from a GuardResult."""
    ts = result.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    if result.verdict == Verdict.UNKNOWN:
        colour = _COLOUR_RED
        title  = "🚨 Intruder Alert"
        desc   = result.message

        fields = []
        for f in result.faces:
            if f.is_match:
                val = f"✅ Known — **{f.matched_name}** (dist: `{f.distance:.4f}`)"
            else:
                dist_str = f"`{f.distance:.4f}`" if f.distance is not None else "n/a"
                val = f"❌ Unknown (dist: {dist_str})"
            fields.append({"name": f"Face {f.face_index + 1}", "value": val, "inline": True})

        # LM Studio description — only present when model was available and responded
        if result.lm_description:
            fields.append({
                "name":   "👁 Appearance",
                "value":  result.lm_description,
                "inline": False,
            })

        fields.append({"name": "Host",      "value": result.hostname,           "inline": True})
        fields.append({"name": "Timestamp", "value": ts,                        "inline": True})
        fields.append({"name": "Tolerance", "value": "see config.json",         "inline": True})

    elif result.verdict == Verdict.NO_FACE:
        colour = _COLOUR_ORANGE
        title  = "⚠️ No Face Detected"
        desc   = result.message
        fields = [
            {"name": "Host",      "value": result.hostname, "inline": True},
            {"name": "Timestamp", "value": ts,              "inline": True},
            {"name": "Retries",   "value": str(result.face_retries), "inline": True},
        ]

    elif result.verdict == Verdict.CAMERA_ERROR:
        colour = _COLOUR_ORANGE
        title  = "⚠️ Camera Error"
        desc   = result.message
        fields = [
            {"name": "Host",   "value": result.hostname,    "inline": True},
            {"name": "Detail", "value": result.error_detail or "n/a", "inline": False},
        ]

    else:
        # ROSTER_ERROR / CONFIG_ERROR
        colour = _COLOUR_GREY
        title  = "⚠️ System Fault"
        desc   = result.message
        fields = [
            {"name": "Verdict", "value": result.verdict.value,        "inline": True},
            {"name": "Host",    "value": result.hostname,              "inline": True},
            {"name": "Detail",  "value": result.error_detail or "n/a", "inline": False},
        ]

    return {
        "title":       title,
        "description": desc,
        "color":       colour,
        "fields":      fields,
        "footer":      {"text": "faceguard"},
    }


def _load_photo(capture_path: Optional[Path]) -> Optional[bytes]:
    """
    Read the capture photo from disk.
    Returns None (silently) if path is absent or unreadable.
    A missing photo should never prevent the alert from sending.
    """
    if capture_path is None:
        return None
    try:
        return capture_path.read_bytes()
    except OSError as exc:
        get_logger().warn("discord_photo_read_failed", path=str(capture_path), detail=str(exc))
        return None


def _send_once(webhook_url: str, payload: dict, photo_bytes: Optional[bytes]) -> requests.Response:
    """
    Send one webhook request. Returns the Response.
    Raises requests.RequestException on network failure.
    """
    embed_json = {
        "embeds":   [payload],
        "username": "faceguard",
    }

    if photo_bytes:
        files = {
            "file":    ("capture.jpg", io.BytesIO(photo_bytes), "image/jpeg"),
            "payload_json": (None, __import__("json").dumps(embed_json), "application/json"),
        }
        return requests.post(webhook_url, files=files, timeout=_SEND_TIMEOUT)
    else:
        return requests.post(webhook_url, json=embed_json, timeout=_SEND_TIMEOUT)


def send_alert(result: GuardResult, webhook_url: str) -> bool:
    """
    Send a Discord alert for the given GuardResult.

    Returns True if sent successfully, False if all attempts failed.
    Never raises — all exceptions are caught and logged.

    Only sends for alarm or fatal verdicts. KNOWN with log_known_entries=False
    is filtered upstream in the dispatcher, not here — this function sends
    whatever it's given.
    """
    log = get_logger()
    embed  = _build_embed(result)
    photo  = _load_photo(result.capture_path)

    log.info(
        "discord_send_attempt",
        verdict=result.verdict.value,
        has_photo=photo is not None,
    )

    last_error: Optional[str] = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = _send_once(webhook_url, embed, photo)

            if resp.status_code in (200, 204):
                log.info("discord_sent", attempt=attempt, status=resp.status_code)
                return True

            # 429 = rate limited, 5xx = Discord server error — both retriable
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                log.warn(
                    "discord_retriable_error",
                    attempt=attempt,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
            else:
                # 4xx other than 429 — client error, no point retrying
                log.error(
                    "discord_client_error",
                    status=resp.status_code,
                    body=resp.text[:400],
                )
                return False

        except requests.exceptions.Timeout:
            last_error = "timeout"
            log.warn("discord_timeout", attempt=attempt, timeout_seconds=_SEND_TIMEOUT)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"connection error: {exc}"
            log.warn("discord_connection_error", attempt=attempt, detail=str(exc))

        except Exception as exc:
            log.exception("discord_unexpected_error", exc, attempt=attempt)
            return False

        if attempt < _MAX_RETRIES:
            delay = _RETRY_DELAYS[attempt]
            log.info("discord_retry_wait", seconds=delay, next_attempt=attempt + 1)
            time.sleep(delay)

    log.error(
        "discord_all_attempts_failed",
        attempts=_MAX_RETRIES + 1,
        last_error=last_error,
        capture_path=str(result.capture_path),
        hint="Capture photo is saved locally — check logs dir",
    )
    return False