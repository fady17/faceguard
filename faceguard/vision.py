"""
faceguard/vision.py

LM Studio vision layer — describes the appearance of an unknown intruder.

Responsibility:
  Given a GuardResult with verdict=UNKNOWN, encode the capture frame as
  base64, send it to the LM Studio vision model, and return a plain-English
  description of the person's appearance. This description is attached to
  the Discord alert embed.

What this module is NOT responsible for:
  - Deciding whether someone is known or unknown (that's guard_core)
  - Sending any alerts (that's alerts/)
  - Running if the verdict is not UNKNOWN (caller filters)

Soft dependency contract:
  Every failure path returns None. The caller (dispatcher) treats None as
  "description unavailable" and sends the Discord alert without it.
  This function must NEVER raise, NEVER block beyond timeout_seconds,
  and NEVER affect the primary siren+Discord alert path.

LM Studio API:
  LM Studio exposes an OpenAI-compatible endpoint at http://localhost:1234/v1.
  Vision models accept messages with image_url content blocks where the URL
  is a data URI: "data:image/jpeg;base64,<b64string>".
  We use requests directly rather than the openai SDK to avoid an extra
  dependency and to have precise control over timeout behavior.

Model notes:
  - moondream2: fast, small (1.8B), good for simple descriptions
  - llava-1.5-7b: more detailed, slower
  - llava-1.5-13b: most detailed, requires more VRAM
  Any vision-capable model loaded in LM Studio works — set the name in config.

Image sizing:
  We downscale the frame before encoding. The original capture is typically
  1280x720 or 640x480 — at JPEG quality 70 that's 80-150KB encoded as base64.
  Most vision models don't benefit from full resolution for a description task
  and the smaller payload means faster inference and less context usage.
  We cap at 640px on the longest side.
"""

from __future__ import annotations

import base64
import io
import time
from typing import Optional

import cv2
import numpy as np
import requests

from faceguard.config import LMStudioConfig
from faceguard.logger import get_logger
from faceguard.result import GuardResult, Verdict


# ── Prompt ─────────────────────────────────────────────────────────────────────
# Concise, directive prompt. We want a 2-3 sentence description suitable for
# a security alert — not a creative essay, not a medical report.
# We explicitly ask for observable physical features only, in plain English.

_SYSTEM_PROMPT = (
    "You are a security camera assistant. "
    "When shown an image, describe the person's observable physical appearance "
    "in 2-3 sentences. Include: approximate age range, gender presentation, "
    "hair color and style, skin tone, and any notable clothing or accessories. "
    "Be factual and concise. Do not speculate about identity or intent."
)

_USER_PROMPT = (
    "Describe the person in this image for a security alert. "
    "Focus on physical appearance and clothing only."
)

# ── Image preprocessing ────────────────────────────────────────────────────────
_MAX_DIMENSION = 640   # pixels — cap longest side before encoding
_JPEG_QUALITY  = 72    # 0-100 — balance between size and model clarity


def _prepare_image_b64(frame_bgr: np.ndarray) -> str:
    """
    Downscale frame if needed, encode as JPEG, return as base64 string.
    Raises nothing — caller handles exceptions.
    """
    h, w = frame_bgr.shape[:2]
    if max(h, w) > _MAX_DIMENSION:
        scale = _MAX_DIMENSION / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        frame_bgr = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise ValueError("cv2.imencode failed — cannot encode frame as JPEG")

    return base64.b64encode(buf.tobytes()).decode("ascii")


def _call_lm_studio(
    base_url: str,
    model: str,
    image_b64: str,
    timeout: int,
) -> str:
    """
    Send one vision request to LM Studio. Returns the raw text response.
    Raises requests.RequestException or ValueError on any failure.
    """
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "max_tokens": 900,       # description should be concise — hard cap
        "temperature": 0.7,      # low temp = consistent, factual output
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repetition_penalty": 1.0,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                            "detail": "low",   # "low" = faster, 85-token fixed cost
                        },
                    },
                    {"type": "text", "text": _USER_PROMPT},
                ],
            },
        ],
    }

    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"LM Studio returned no choices: {data}")

 
    message = choices[0].get("message", {})
    content = message.get("content", "").strip()

    # Thinking models (Qwen3, etc.) put reasoning in reasoning_content and
    # leave content empty until they finish drafting. Fall back to it so we
    # still get a useful description even when max_tokens cuts off early.
    if not content:
        content = message.get("reasoning_content", "").strip()

    if not content:
        raise ValueError("LM Studio returned empty content")

    return content


# ── Public API ─────────────────────────────────────────────────────────────────

def describe_intruder(result: GuardResult, lm_cfg: LMStudioConfig) -> Optional[str]:
    """
    Attempt to generate a description of the unknown person in the capture frame.

    Returns:
        A plain-English description string, or None if unavailable for any reason.

    Called by the dispatcher only when:
      - result.verdict == Verdict.UNKNOWN
      - lm_cfg.enabled == True
      - lm_cfg.describe_unknown == True
      - result.frame_bgr is not None
    """
    log = get_logger()

    # ── Guard against being called in wrong context ────────────────────────────
    if result.verdict not in (Verdict.UNKNOWN, Verdict.NO_FACE):
        log.warn("vision_wrong_verdict", verdict=result.verdict.value)
        return None

    if result.frame_bgr is None:
        log.warn("vision_no_frame", hint="No frame available to describe")
        return None

    # ── Prepare image ──────────────────────────────────────────────────────────
    try:
        image_b64 = _prepare_image_b64(result.frame_bgr)
    except Exception as exc:
        log.exception("vision_image_encode_failed", exc)
        return None

    log.info(
        "vision_request_start",
        model=lm_cfg.model,
        base_url=lm_cfg.base_url,
        timeout=lm_cfg.timeout_seconds,
        image_b64_len=len(image_b64),
    )

    start = time.monotonic()

    # ── Call LM Studio ─────────────────────────────────────────────────────────
    try:
        description = _call_lm_studio(
            base_url=lm_cfg.base_url,
            model=lm_cfg.model,
            image_b64=image_b64,
            timeout=lm_cfg.timeout_seconds,
        )

        elapsed = time.monotonic() - start
        log.info(
            "vision_request_success",
            elapsed_seconds=round(elapsed, 2),
            description_length=len(description),
        )
        return description

    except requests.exceptions.ConnectionError:
        # LM Studio not running — most common case, not worth a full error log
        log.warn(
            "vision_lm_studio_unavailable",
            base_url=lm_cfg.base_url,
            hint="LM Studio is not running or not listening on this port",
        )
        return None

    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - start
        log.warn(
            "vision_timeout",
            timeout_seconds=lm_cfg.timeout_seconds,
            elapsed=round(elapsed, 2),
            hint="Increase lm_studio.timeout_seconds if model is slow to respond",
        )
        return None

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log.warn(
            "vision_http_error",
            status=status,
            hint="Model may not be loaded in LM Studio — check the LM Studio UI",
        )
        return None

    except ValueError as exc:
        # Empty response, no choices, etc.
        log.warn("vision_bad_response", detail=str(exc))
        return None

    except Exception as exc:
        log.exception("vision_unexpected_error", exc)
        return None