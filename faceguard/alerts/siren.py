"""
faceguard/alerts/siren.py

Siren playback via macOS afplay.

Why afplay and not a Python audio library:
  afplay is a macOS system binary that ships on every Mac, requires no pip
  install, handles .mp3/.wav/.aiff natively, and respects system volume.
  A Python audio library (pygame, simpleaudio, sounddevice) adds a compiled
  dependency that can fail on different Python versions or Apple Silicon
  without Rosetta. afplay never fails to install because it's already there.

Why a background thread for repeat playback:
  The guard fires siren and Discord simultaneously. If siren blocks, the
  Discord photo doesn't arrive until after the siren finishes — which is
  the wrong order for a security alert. Thread lets both happen at once.

Volume control:
  afplay accepts -v <float> where 1.0 is system volume, 2.0 is double, etc.
  We clamp to [0.1, 5.0] — below 0.1 is inaudible, above 5.0 clips badly.

Custom sound file:
  If config.siren.sound_file is set and the file exists, we use it.
  Otherwise we fall back to the built-in macOS Sosumi alert sound.
  For maximum showoff effect, drop a siren .mp3 at the configured path.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from faceguard.logger import get_logger

# ── Built-in fallback sounds (all ship with every macOS) ─────────────────────
# Ordered by dramatic impact — we try each until one exists on this machine.
_FALLBACK_SOUNDS = [
    "/System/Library/Sounds/Sosumi.aiff",
    "/System/Library/Sounds/Basso.aiff",
    "/System/Library/Sounds/Funk.aiff",
    "/System/Library/Sounds/Hero.aiff",
]


def _resolve_sound_file(configured: Optional[str]) -> Optional[str]:
    """
    Return the sound file path to use, or None if nothing is playable.
    Priority: configured path → built-in fallbacks → None (silent).
    """
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
        get_logger().warn(
            "siren_custom_sound_not_found",
            path=str(p),
            fallback="using built-in sound",
        )

    for candidate in _FALLBACK_SOUNDS:
        if Path(candidate).exists():
            return candidate

    return None


def _play_once(sound_path: str, volume: float) -> None:
    """Play sound file once via afplay. Blocks until playback completes."""
    volume = max(0.1, min(5.0, volume))
    try:
        subprocess.run(
            ["afplay", "-v", str(volume), sound_path],
            check=False,            # non-zero exit from afplay is non-fatal
            timeout=30,             # hard ceiling so a corrupt file can't hang forever
            capture_output=True,    # suppress afplay's own stderr noise
        )
    except FileNotFoundError:
        # afplay not found — not on macOS, or PATH is stripped in LaunchAgent env
        get_logger().warn("afplay_not_found", hint="siren disabled — not running on macOS?")
    except subprocess.TimeoutExpired:
        get_logger().warn("afplay_timeout", sound=sound_path)
    except Exception as exc:
        get_logger().exception("afplay_error", exc, sound=sound_path)


def _repeat_play(sound_path: str, volume: float, repeat: int) -> None:
    """Play sound `repeat` times sequentially. Runs in a background thread."""
    log = get_logger()
    log.info("siren_start", sound=sound_path, repeat=repeat, volume=volume)
    for i in range(repeat):
        _play_once(sound_path, volume)
        if i < repeat - 1:
            time.sleep(0.2)   # brief gap between repetitions
    log.info("siren_done", repeat=repeat)


def play_siren(
    sound_file: Optional[str] = None,
    volume: float = 1.0,
    repeat: int = 3,
    block: bool = False,
) -> Optional[threading.Thread]:
    """
    Play the siren sound.

    Args:
        sound_file: Path to custom sound file. None = use built-in fallback.
        volume:     Playback volume multiplier (1.0 = system volume).
        repeat:     Number of times to play the sound.
        block:      If True, wait for playback to finish before returning.
                    Default False — fires in background thread.

    Returns:
        The background Thread if block=False, None if block=True or no sound found.
    """
    log = get_logger()
    resolved = _resolve_sound_file(sound_file)

    if resolved is None:
        log.warn("siren_no_sound_file", detail="No playable sound found — siren silent")
        return None

    if block:
        _repeat_play(resolved, volume, repeat)
        return None

    t = threading.Thread(
        target=_repeat_play,
        args=(resolved, volume, repeat),
        daemon=True,    # daemon=True: thread dies with the main process
        name="faceguard-siren",
    )
    t.start()
    return t
