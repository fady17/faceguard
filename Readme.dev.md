# faceguard — Developer README

This document is for anyone setting up a development environment and working directly with the codebase. If you're a user who just wants to install and run the guard, wait for the `Makefile` and follow the user README instead.

---

## What This Is

A macOS login-time face recognition guard. It fires after the login password is entered, captures a photo from the FaceTime camera, runs face recognition against an enrolled roster, and either passes silently or triggers a local siren and a Discord alert with the intruder's photo and an AI-generated description of their appearance.

**Stack at a glance:**
- Face recognition: `face_recognition` (dlib, 128-dimensional face embeddings)
- Camera: `opencv-python`
- Vision description: LM Studio local vision model (moondream2, llava, etc.) via OpenAI-compatible API
- Alert delivery: Discord webhook
- Audio: macOS `afplay` subprocess
- Autostart: macOS `LaunchAgent` plist (Phase 6)

---

## Prerequisites

- **macOS** (Apple Silicon or Intel — both work)
- **Python 3.10+** (`python3 --version`)
- **Homebrew** (for cmake which dlib needs)
- **LM Studio** with a vision-capable model loaded (optional but recommended)
- A **Discord server** you control with a webhook URL

---

## Environment Setup

### 1. Clone and enter the project

```bash
git clone https://github.com/fady17/faceguard.git
cd faceguard
```

### 2. Install uv (if you don't have it)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv` manages Python versions and virtual environments. It's faster than `pip` and doesn't require activating the venv for Makefile usage — the Makefile detects `.venv/bin/python3` directly.

### 3. Create a virtual environment

```bash
uv venv .venv
```

This creates `.venv/` in the project root using your system Python (or a `uv`-managed Python if you prefer a specific version: `uv venv .venv --python 3.12`).

You don't need to activate the venv to use `make` targets — they reference `.venv/bin/python3` directly. Activate when you want to run scripts manually:

```bash
source .venv/bin/activate
```

### 4. Install cmake (required by dlib)

```bash
brew install cmake
```

This is a one-time system step. dlib compiles from source on install and takes 2-4 minutes — that's normal.

### 5. Install Python dependencies

```bash
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt   # test tools: pytest, ruff, mypy

# face_recognition_models is a required companion package that face_recognition
# does not declare as a PyPI dependency — must be installed separately from GitHub.
# Without this you get: "Please install face_recognition_models..."
uv pip install git+https://github.com/ageitgey/face_recognition_models
```

### 6. Scaffold the data directory

```bash
make setup
```

This creates `~/.faceguard/` with the right subdirectory structure and copies `config.example.json` → `~/.faceguard/config.json` if it doesn't already exist. It will not overwrite an existing config.

### 7. Edit your config

```bash
nano ~/.faceguard/config.json
```

Minimum required change: set `discord.webhook_url`. Everything else has sane defaults.

See the [Config Reference](#config-reference) section below for all fields.

### 8. Grant camera permission

This is a macOS security requirement. The permission is per-binary — you need to grant it to the Python interpreter inside your venv, not to Python in general.

Run any command that opens the camera (enroll will trigger it):

```bash
python enroll.py add YourName
# or: source .venv/bin/activate && python enroll.py add YourName
```

macOS will show a permission dialog the first time. Click **Allow**. If the dialog doesn't appear and the camera returns blank frames, go to:

> System Settings → Privacy & Security → Camera

Find your Python binary in the list — it will be at `.venv/bin/python3` relative to the project. The full absolute path shows in the Privacy panel. Enable it.

> **Note:** The camera permission follows the binary path. If you recreate your venv (`rm -rf .venv && uv venv .venv`), you'll need to re-grant the permission for the new binary.

---

## Enrolling a Face

```bash
# Add yourself to the roster
python enroll.py add Fady

# Verify the match works before trusting the guard
python enroll.py verify

# See who's enrolled
python enroll.py list

# Remove someone
python enroll.py remove Fady

# Export roster to a file (backup or share)
python enroll.py export ~/Desktop/roster_backup.pkg

# Import (merge) from an exported file
python enroll.py import ~/Desktop/roster_backup.pkg
```

**Enrollment tips:**
- Sit in normal working conditions — same lighting you'll have at login time
- Move your head slightly between frames to capture multiple angles
- If `verify` gives a low match, re-enroll. 3-5 good samples is all you need
- Default tolerance is `0.5` — lower means stricter. If you get false positives from similar-looking people, lower it to `0.45`. If you get false negatives (yourself not matching), raise it toward `0.6`

---

## Running the Guard Manually

Always test manually before installing the LaunchAgent.

```bash
# Full dry run — recognition fires but no siren, no Discord
python face_guard.py --dry-run --no-delay --verbose

# Full dry run with startup delay (closer to real LaunchAgent conditions)
python face_guard.py --dry-run --verbose

# Live run — siren and Discord will fire if triggered
python face_guard.py --no-delay --verbose
```

**Flag reference:**

| Flag | Effect |
|---|---|
| `--dry-run` | Skip siren and Discord. Recognition still runs fully. |
| `--no-delay` | Skip the 8-second startup delay. Use when running manually. |
| `--verbose` | Print all log events to stderr, not just WARN/ERROR. |

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | KNOWN — identity confirmed, clean exit |
| `1` | UNKNOWN or NO_FACE — alarm condition, alerts fired |
| `2` | System fault (camera error, roster missing, config invalid) |
| `3` | Another guard instance already running (PID lock) |

---

## Project Structure

```
faceguard/
├── face_guard.py            Entry point — startup delay, PID lock, wires everything
├── enroll.py                Face enrollment CLI
├── setup.py                 First-run scaffold (~/.faceguard/ creation)
├── config.example.json      Repo-safe config template (no secrets)
├── requirements.txt
│
└── faceguard/               The package
    ├── config.py            Config loader → typed AppConfig dataclass
    ├── logger.py            Structured JSON logger (daily rotation to ~/.faceguard/logs/)
    ├── result.py            GuardResult + FaceResult + Verdict types
    ├── roster.py            Face encoding storage (roster.pkl read/write)
    ├── camera.py            OpenCV capture utilities (shared by enroll + guard)
    ├── guard_core.py        Pure recognition engine → GuardResult
    ├── pidlock.py           PID-based single-instance lockfile
    ├── vision.py            LM Studio vision layer (intruder description)
    │
    └── alerts/
        ├── __init__.py      Alert dispatcher — routes GuardResult to siren + Discord
        ├── siren.py         afplay-based siren (background thread)
        └── discord.py       Discord webhook sender (with retry)
```

### Dependency graph (no cycles)

```
face_guard.py
  ├── faceguard.config
  ├── faceguard.logger
  ├── faceguard.pidlock
  ├── faceguard.result
  ├── faceguard.guard_core
  │     ├── faceguard.camera
  │     ├── faceguard.config
  │     ├── faceguard.logger
  │     ├── faceguard.result
  │     └── faceguard.roster
  └── faceguard.alerts
        ├── faceguard.vision
        │     ├── faceguard.config
        │     ├── faceguard.logger
        │     └── faceguard.result
        ├── faceguard.alerts.siren
        │     └── faceguard.logger
        └── faceguard.alerts.discord
              ├── faceguard.logger
              └── faceguard.result
```

The guard core (`guard_core.py`) has no knowledge of alerts. The alert dispatcher (`alerts/__init__.py`) has no knowledge of recognition. Adding a new alert channel (Telegram, email, SMS) means adding one file in `alerts/` and one import in `alerts/__init__.py` — nothing else touches.

---

## Module Responsibilities

### `config.py`
Loads `~/.faceguard/config.json` and returns a typed `AppConfig` dataclass. All path `~` expansion happens here and nowhere else. Raises `ConfigError` on any problem — callers catch one exception type, not `KeyError` or `TypeError`.

### `logger.py`
Writes structured JSON lines to `~/.faceguard/logs/YYYY-MM-DD.jsonl`. Each line is a self-contained JSON object: `{timestamp, level, event, ...context}`. WARN and ERROR also go to stderr in human-readable format. Initialize once at startup with `init_logger()`, then get the singleton with `get_logger()` from any module.

### `roster.py`
Manages the `roster.pkl` file. The Roster class is the only thing that reads or writes that file. Saves atomically via `.pkl.tmp` + rename to prevent corruption on interrupted writes. Stores multiple face encodings per person for better match accuracy across angles and lighting.

### `camera.py`
OpenCV wrappers shared between `enroll.py` and `guard_core.py`. `open_camera()` retries on failure because the macOS camera driver needs a few seconds to initialize after login. `capture_frames_burst()` is used by enrollment only. `capture_frame()` is used by the guard.

### `guard_core.py`
The recognition engine. Takes an `AppConfig`, returns a `GuardResult`. Never raises, never prints, never plays sounds, never calls Discord. Every error path returns a `GuardResult` with the appropriate `Verdict`. This design makes it unit-testable with mock cameras and mock rosters without any alert side effects.

### `result.py`
Typed data contracts between the core and the alert layer. `Verdict` enum with `.is_alarm` and `.is_fatal` properties. `GuardResult.to_log_dict()` returns a JSON-serializable dict (numpy arrays stripped).

### `vision.py`
Sends the capture frame to LM Studio for a plain-English appearance description of unknown faces. Returns `None` on any failure (not running, timeout, bad response) — never raises, never blocks beyond `timeout_seconds`. The description is stored on `result.lm_description` and included in the Discord embed if available.

### `alerts/__init__.py`
The dispatcher. One function: `dispatch(result, cfg, dry_run)`. Execution order: fire siren (background thread) → call LM Studio → send Discord → join siren thread. KNOWN verdict is silent. All other verdicts trigger some combination of alerts based on `Verdict.is_alarm`.

### `alerts/siren.py`
Calls `afplay` in a daemon thread. Falls back through a list of built-in macOS sounds if no custom sound is configured. If `afplay` is not found (not on macOS) it logs a warning and continues silently.

### `alerts/discord.py`
Sends a formatted embed with the capture photo attached via multipart POST to the webhook URL. Retries twice on 429 or 5xx. Returns `False` on total failure — the calling dispatcher logs the capture photo path so you can retrieve it manually.

---

## Data Directory

Everything user-specific lives in `~/.faceguard/` and is gitignored.

```
~/.faceguard/
├── config.json                    Your config (never commit this)
├── roster.pkl                     Enrolled face encodings (never commit this)
├── faceguard.pid                  Runtime PID lockfile (auto-managed)
│
├── photos/
│   ├── enrolled/                  Reference photos saved during enrollment
│   │   └── fady_20250115_091432.jpg
│   └── captures/                  Photos taken by the guard at each login
│       ├── 20250115_091500_macbook_known.jpg
│       └── 20250115_091600_macbook_unknown.jpg
│
└── logs/
    ├── 2025-01-15.jsonl           Today's structured log
    └── 2025-01-14.jsonl
```

Log files are JSON Lines format. Each line is a complete event. You can tail and filter them:

```bash
# Watch live
tail -f ~/.faceguard/logs/$(date +%Y-%m-%d).jsonl

# Filter to guard run events only
cat ~/.faceguard/logs/$(date +%Y-%m-%d).jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e.get('event') in ('guard_run_complete', 'guard_result'):
        print(json.dumps(e, indent=2))
"

# See all UNKNOWN verdicts
grep '"verdict": "UNKNOWN"' ~/.faceguard/logs/*.jsonl
```

---

## Config Reference

All fields with their defaults and valid ranges:

```jsonc
{
  "discord": {
    "webhook_url": "",            // REQUIRED — Discord webhook URL
    "alert_channel_name": "security-alerts",  // informational only, not used by code
    "log_known_entries": false    // true = send Discord ping even for successful known logins
  },

  "lm_studio": {
    "enabled": true,              // false = skip vision layer entirely
    "base_url": "http://localhost:1234/v1",   // LM Studio server URL
    "model": "moondream2",        // model name exactly as shown in LM Studio
    "timeout_seconds": 10,        // max seconds to wait for LM response
    "describe_unknown": true      // false = skip vision even when enabled
  },

  "recognition": {
    "tolerance": 0.5,             // face distance threshold — lower=stricter (0.4–0.6)
    "capture_retries": 3,         // camera open attempts before CAMERA_ERROR
    "capture_retry_delay_seconds": 1.5,
    "no_face_retries": 3,         // retry attempts when no face is detected in frame
    "no_face_retry_delay_seconds": 1.5,
    "camera_index": 0             // 0 = built-in FaceTime camera
  },

  "siren": {
    "enabled": true,
    "sound_file": null,           // null = built-in macOS Sosumi. Absolute path for custom.
    "volume": 1.0,                // multiplier: 1.0 = system volume, 2.0 = double
    "repeat": 3                   // times to play the sound file
  },

  "paths": {                      // all support ~ expansion
    "roster_file": "~/.faceguard/roster.pkl",
    "captures_dir": "~/.faceguard/photos/captures",
    "enrolled_dir": "~/.faceguard/photos/enrolled",
    "logs_dir": "~/.faceguard/logs"
  },

  "guard": {
    "startup_delay_seconds": 8,   // seconds to wait after login before running
    "pid_file": "~/.faceguard/faceguard.pid"
  }
}
```

**Tolerance tuning guide:**

| Situation | Adjustment |
|---|---|
| You get false positives (strangers matched as you) | Lower tolerance: `0.45` or `0.4` |
| You get false negatives (you're not matching) | Raise tolerance: `0.55` or `0.6`, or re-enroll with more samples |
| Identical twins on the same machine | Lower to `0.4` and re-enroll both separately |
| Default is working fine | Leave at `0.5` |

---

## LM Studio Setup (Vision Layer)

1. Download and install [LM Studio](https://lmstudio.ai)
2. In the Discover tab, search for and download a vision model. Recommended options:
   - `moondream2` — fastest, 1.8B params, good for basic descriptions
   - `llava-1.5-7b-hgguf` — more detailed descriptions, needs ~6GB VRAM
3. Load the model in the Chat tab
4. Start the local server: Developer tab → Start Server (default port 1234)
5. Set `lm_studio.model` in your config to the exact model identifier shown in LM Studio

The guard will work without LM Studio running. If LM Studio is unavailable at login time, the Discord alert still sends — it just won't have the appearance description field.

---

## Adding a New Alert Channel

The alert system is designed for extension. To add Telegram, email, or any other channel:

1. Create `faceguard/alerts/telegram.py` (mirror the structure of `discord.py`)
2. Implement `send_alert(result: GuardResult, token: str, chat_id: str) -> bool`
3. Add config fields to `config.py` (`TelegramConfig` dataclass)
4. Add `config.example.json` fields
5. Import and call from `alerts/__init__.py` in the `dispatch()` function

No other files change.

---

## Modifying the Enrollment Flow

Enrollment behaviour is in `enroll.py` (the CLI layer) and `roster.py` (the data layer). They are intentionally separate:

- To change how many frames are captured per enrollment session: edit `ENROLL_SAMPLE_COUNT` in `enroll.py`
- To change how encodings are stored or loaded: edit `roster.py`
- To add a new enrollment command: add a `cmd_<name>()` function in `enroll.py` and a branch in `main()`
- The guard never imports from `enroll.py` — safe to modify the enrollment flow without risking guard behaviour

---

## Common Dev Issues

**`face_recognition` install fails with compiler error**
```bash
# Make sure cmake is installed first
brew install cmake
# Then retry
uv pip install face_recognition
```

**Camera opens but returns black/green frames**
This is almost always a macOS camera permission issue. The permission is per-binary. Run `python enroll.py add test` in your terminal — this forces the permission dialog for your current Python binary. Go to System Settings → Privacy & Security → Camera and verify your `.venv/bin/python3` is listed and enabled.

If you recently recreated the venv (`rm -rf .venv && uv venv .venv`), the new binary needs a fresh permission grant — the old path is gone and the new one doesn't inherit it.

**`verify` shows poor match score despite good enrollment**
- Check lighting — recognition degrades significantly in dark or backlit conditions
- Re-enroll: `python enroll.py add YourName` (you'll be prompted to confirm replacement)
- Temporarily raise tolerance to `0.6` in config to test if that's the issue

**LM Studio returns HTTP 500**
The model is not loaded. Open the LM Studio app, go to the Chat tab, and load your vision model. The guard will work fine without it — vision is a soft dependency.

**Guard exits with code 2 at every login**
Most common cause: the camera permission was granted to the terminal Python but not to the venv Python the LaunchAgent uses. Verify the Python binary path in the plist matches the one that has camera permission. Run `make status` — it shows the Python path that was embedded in the plist.

**PID lock prevents guard from starting after a crash**
```bash
rm ~/.faceguard/faceguard.pid
```
The guard handles stale PIDs automatically (checks if the process is actually alive), but if something unusual happened you can delete it manually.

**uv not found after install**
```bash
# Reload your shell after installing uv
source ~/.zshrc   # or ~/.bashrc
# Verify
uv --version
```

---

## Phase Roadmap (Build Status)

| Phase | Module(s) | Status |
|---|---|---|
| 1 — Scaffold & config | `config.py`, `logger.py`, `setup.py` | ✅ Done |
| 2 — Enrollment | `roster.py`, `camera.py`, `enroll.py` | ✅ Done |
| 3 — Guard core | `result.py`, `pidlock.py`, `guard_core.py`, `face_guard.py` | ✅ Done |
| 4 — Alert layer | `alerts/siren.py`, `alerts/discord.py`, `alerts/__init__.py` | ✅ Done |
| 5 — LM Studio vision | `vision.py` | ✅ Done |
| 6 — LaunchAgent & Makefile | `scripts/`, `Makefile` | 🔄 Next |
| 7 — Hardening | Retry tuning, edge cases, test suite | ⬜ Pending |
| 8 — Open source packaging | `README.md`, `CONTRIBUTING.md`, release | ⬜ Pending |