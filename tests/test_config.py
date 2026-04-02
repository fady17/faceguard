"""
tests/test_config.py

Tests for the config loader. Focuses on:
  - Valid config loads and produces correct types
  - Every required field missing or malformed
  - Path expansion works correctly
  - Webhook URL validation
  - Default values are correct
"""

import json
import pytest
from pathlib import Path

from faceguard.config import load_config, ConfigError


class TestConfigLoading:

    def test_valid_config_loads(self, cfg_file: Path):
        cfg = load_config(cfg_file)
        assert cfg.discord.webhook_url == "https://discord.com/api/webhooks/test/token"
        assert cfg.recognition.tolerance == 0.5
        assert cfg.lm_studio.model == "moondream2"

    def test_missing_config_file_raises(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.json")

    def test_corrupt_json_raises(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text("{ this is not valid json }")
        with pytest.raises(ConfigError, match="not valid JSON"):
            load_config(p)

    def test_empty_json_object_raises(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text("{}")
        with pytest.raises(ConfigError):
            load_config(p)

    def test_placeholder_webhook_raises(self, tmp_path: Path):
        """Config with the example placeholder webhook URL must be rejected."""
        data = {
            "discord": {
                "webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ConfigError, match="webhook_url"):
            load_config(p)

    def test_empty_webhook_raises(self, tmp_path: Path):
        data = {"discord": {"webhook_url": ""}}
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        with pytest.raises(ConfigError, match="webhook_url"):
            load_config(p)

    def test_missing_discord_section_raises(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"lm_studio": {}}))
        with pytest.raises(ConfigError):
            load_config(p)

    def test_path_tilde_expansion(self, cfg_file: Path):
        """Paths in config must be fully expanded (no ~ remaining)."""
        cfg = load_config(cfg_file)
        assert "~" not in str(cfg.paths.roster_file)
        assert "~" not in str(cfg.paths.captures_dir)
        assert "~" not in str(cfg.paths.logs_dir)

    def test_defaults_applied_for_missing_optional_sections(self, tmp_path: Path):
        """A config with only discord section should use defaults for everything else."""
        data = {"discord": {"webhook_url": "https://discord.com/api/webhooks/x/y"}}
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        cfg = load_config(p)
        assert cfg.recognition.tolerance == 0.5
        assert cfg.siren.enabled is True
        assert cfg.lm_studio.enabled is True
        assert cfg.guard.startup_delay_seconds == 8

    def test_recognition_tolerance_type_coercion(self, tmp_path: Path):
        """Tolerance given as int in JSON should be coerced to float."""
        data = {
            "discord": {"webhook_url": "https://discord.com/api/webhooks/x/y"},
            "recognition": {"tolerance": 1},   # int, not float
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        cfg = load_config(p)
        assert isinstance(cfg.recognition.tolerance, float)
        assert cfg.recognition.tolerance == 1.0

    def test_lm_studio_disabled_flag(self, tmp_path: Path):
        data = {
            "discord": {"webhook_url": "https://discord.com/api/webhooks/x/y"},
            "lm_studio": {"enabled": False},
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data))
        cfg = load_config(p)
        assert cfg.lm_studio.enabled is False