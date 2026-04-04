"""
faceguard/config.py

Loads, validates, and exposes the user config as typed dataclasses.
All path expansion (~) happens here — no other module calls os.path.expanduser().
A missing or corrupt config is a FATAL error: we raise ConfigError immediately
so every caller can catch one specific exception type rather than KeyError/TypeError.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Single canonical config path ──────────────────────────────────────────────
CONFIG_PATH = Path("~/.faceguard/config.json").expanduser()


class ConfigError(Exception):
    """Raised when config is missing, unreadable, or fails validation."""


# ── Sub-config dataclasses ─────────────────────────────────────────────────────

@dataclass
class DiscordConfig:
    webhook_url: str
    alert_channel_name: str = "security-alerts"
    log_known_entries: bool = False


@dataclass
class LMStudioConfig:
    enabled: bool = True
    base_url: str = "http://localhost:1234/v1"
    model: str = "gemma-4-e2b-it"
    timeout_seconds: int = 10
    describe_unknown: bool = True


@dataclass
class RecognitionConfig:
    tolerance: float = 0.5
    capture_retries: int = 3
    capture_retry_delay_seconds: float = 1.5
    no_face_retries: int = 3
    no_face_retry_delay_seconds: float = 1.5
    camera_index: int = 0


@dataclass
class SirenConfig:
    enabled: bool = True
    sound_file: Optional[str] = None   # None = built-in fallback
    volume: float = 1.0
    repeat: int = 3


@dataclass
class PathsConfig:
    roster_file: Path
    captures_dir: Path
    enrolled_dir: Path
    logs_dir: Path


@dataclass
class GuardConfig:
    startup_delay_seconds: int = 8
    pid_file: Path = field(default_factory=lambda: Path("~/.faceguard/faceguard.pid").expanduser())


@dataclass
class AppConfig:
    discord: DiscordConfig
    lm_studio: LMStudioConfig
    recognition: RecognitionConfig
    siren: SirenConfig
    paths: PathsConfig
    guard: GuardConfig


# ── Loader ────────────────────────────────────────────────────────────────────

def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    """
    Load and validate config.json → AppConfig.
    Raises ConfigError on any failure so callers have one catch target.
    """
    if not path.exists():
        raise ConfigError(
            f"Config file not found at {path}. "
            f"Copy config.example.json to {path} and fill in your values."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {exc}") from exc

    try:
        discord_raw = raw["discord"]
        webhook = discord_raw.get("webhook_url", "")
        if not webhook or "YOUR_WEBHOOK" in webhook:
            raise ConfigError(
                "discord.webhook_url is not set. "
                "Create a Discord webhook and paste the URL into config.json."
            )

        discord = DiscordConfig(
            webhook_url=webhook,
            alert_channel_name=discord_raw.get("alert_channel_name", "security-alerts"),
            log_known_entries=discord_raw.get("log_known_entries", False),
        )

        lm_raw = raw.get("lm_studio", {})
        lm_studio = LMStudioConfig(
            enabled=lm_raw.get("enabled", True),
            base_url=lm_raw.get("base_url", "http://localhost:1234/v1"),
            model=lm_raw.get("model", "gemma-4-e2b-it"),
            timeout_seconds=lm_raw.get("timeout_seconds", 10),
            describe_unknown=lm_raw.get("describe_unknown", True),
        )

        rec_raw = raw.get("recognition", {})
        recognition = RecognitionConfig(
            tolerance=float(rec_raw.get("tolerance", 0.5)),
            capture_retries=int(rec_raw.get("capture_retries", 3)),
            capture_retry_delay_seconds=float(rec_raw.get("capture_retry_delay_seconds", 1.5)),
            no_face_retries=int(rec_raw.get("no_face_retries", 3)),
            no_face_retry_delay_seconds=float(rec_raw.get("no_face_retry_delay_seconds", 1.5)),
            camera_index=int(rec_raw.get("camera_index", 0)),
        )

        siren_raw = raw.get("siren", {})
        siren = SirenConfig(
            enabled=siren_raw.get("enabled", True),
            sound_file=siren_raw.get("sound_file", None),
            volume=float(siren_raw.get("volume", 1.0)),
            repeat=int(siren_raw.get("repeat", 3)),
        )

        paths_raw = raw.get("paths", {})
        paths = PathsConfig(
            roster_file=_expand(paths_raw.get("roster_file", "~/.faceguard/roster.pkl")),
            captures_dir=_expand(paths_raw.get("captures_dir", "~/.faceguard/photos/captures")),
            enrolled_dir=_expand(paths_raw.get("enrolled_dir", "~/.faceguard/photos/enrolled")),
            logs_dir=_expand(paths_raw.get("logs_dir", "~/.faceguard/logs")),
        )

        guard_raw = raw.get("guard", {})
        guard = GuardConfig(
            startup_delay_seconds=int(guard_raw.get("startup_delay_seconds", 8)),
            pid_file=_expand(guard_raw.get("pid_file", "~/.faceguard/faceguard.pid")),
        )

    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Config validation failed: {exc}") from exc

    return AppConfig(
        discord=discord,
        lm_studio=lm_studio,
        recognition=recognition,
        siren=siren,
        paths=paths,
        guard=guard,
    )
