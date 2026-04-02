# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [1.0.0] — Initial release

### Added
- Face enrollment CLI (`enroll.py`) with `add`, `remove`, `list`, `verify`, `export`, `import` commands
- Multiple encoding samples per person for improved match accuracy across angles and lighting
- Atomic roster save (`roster.pkl.tmp` → rename) to prevent corruption on interrupted writes
- `face_guard.py` entry point with `--dry-run`, `--no-delay`, `--verbose` flags
- PID lockfile with stale-PID detection to prevent duplicate guard instances
- Configurable startup delay (default 8s) for camera driver and LM Studio initialization
- Retry logic for camera open failures (configurable attempts + delay)
- Retry logic for no-face detection (configurable attempts + delay)
- Capture photo saved to `~/.faceguard/photos/captures/` on every run with verdict tag
- Discord webhook alerts with embedded photo, per-face match breakdown, and distance scores
- LM Studio vision layer: AI-generated intruder appearance description appended to Discord embed
- Siren playback via `afplay` in a background daemon thread (non-blocking)
- Alert dispatcher with correct execution order: siren → LM Studio → Discord → join siren
- Structured JSON line logging to `~/.faceguard/logs/YYYY-MM-DD.jsonl`
- `make setup`, `make install`, `make uninstall`, `make test`, `make enroll`, `make verify`, `make logs`, `make status`, `make check` targets
- LaunchAgent plist templates with placeholder substitution for per-machine Python paths
- Optional LM Studio autostart LaunchAgent (`make install-lmstudio`)
- 91 tests across 4 test modules — no camera, no Discord, no LM Studio required
- `README.md` (user), `README.dev.md` (developer), `CONTRIBUTING.md`, `config.example.json`
- MIT license

### Verdict taxonomy
- `KNOWN` — all detected faces matched roster (silent)
- `UNKNOWN` — at least one face did not match (alarm)
- `NO_FACE` — no face detected after retries (alarm — possible obstruction)
- `CAMERA_ERROR` — camera unavailable (alarm)
- `ROSTER_ERROR` — roster missing or corrupt (system fault, Discord notification)
- `CONFIG_ERROR` — config invalid (system fault)

[Unreleased]: https://github.com/yourname/faceguard/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/yourname/faceguard/releases/tag/v1.0.0
