# faceguard

macOS login-time face recognition guard. Captures a photo after password entry, checks it against an enrolled face roster, and either passes silently (known face) or triggers a local siren + Discord alert with the photo and an AI-generated description (unknown face). Runs as a macOS LaunchAgent.

**macOS only.** Do not suggest Linux paths, `systemd`, Docker, or cross-platform abstractions.

---

## Stack

- **Python 3.10+** managed with **uv** (`uv venv .venv`, `uv pip install`)
- **face_recognition** (dlib) — biometric face matching
- **opencv-python** — camera capture
- **requests** — Discord webhook HTTP
- **LM Studio** — local vision model via OpenAI-compatible API at `localhost:1234`
- **afplay** — macOS system binary for siren audio
- **launchctl** — macOS LaunchAgent management

---

## Commands

```bash
# Install deps (always use uv, never bare pip)
uv pip install -r requirements.txt
uv pip install git+https://github.com/ageitgey/face_recognition_models

# Run tests (no camera, no network, no Discord required — all mocked)
uv run python -m pytest tests/ -v --tb=short

# Run tests for a specific module
uv run python -m pytest tests/test_roster.py -v

# Dry-run the guard (recognition fires, no siren, no Discord, no startup delay)
uv run python face_guard.py --dry-run --no-delay --verbose

# Enroll a face
uv run python enroll.py add <name>

# Verify enrollment
uv run python enroll.py verify

# Scaffold ~/.faceguard/ and config
uv run python setup.py

# Check environment health (no prereqs)
make diagnose

# Install LaunchAgent
make install
```

---

## Project layout

```
face_guard.py          Entry point — startup delay, PID lock, wires modules
enroll.py              Face enrollment CLI
setup.py               First-run scaffold for ~/.faceguard/

faceguard/
  config.py            Config loader → AppConfig (typed dataclasses)
  logger.py            Structured JSON logger → ~/.faceguard/logs/YYYY-MM-DD.jsonl
  result.py            GuardResult, FaceResult, Verdict (shared types)
  roster.py            Roster PKL read/write — face encoding storage
  camera.py            OpenCV capture utils (shared by enroll + guard)
  guard_core.py        Recognition engine → GuardResult (no alerts, no I/O)
  pidlock.py           PID lockfile with stale-PID detection
  vision.py            LM Studio vision layer (intruder description)
  alerts/
    __init__.py        Dispatcher: siren → LM Studio → Discord → join siren
    discord.py         Webhook sender with retry
    siren.py           afplay siren in daemon thread

scripts/
  *.plist.template     LaunchAgent templates (placeholders substituted by make install)

tests/
  conftest.py          Shared fixtures — all tests use these
  test_config.py
  test_roster.py
  test_guard_core.py   Requires face_recognition installed; camera and FR are mocked
  test_alerts.py
  test_vision.py
```

---

## Architecture invariants — never violate these

**1. `guard_core.py` has zero knowledge of alerts.**
It imports nothing from `alerts/`. It never plays sounds, sends webhooks, or calls LM Studio.
It takes `AppConfig` and returns `GuardResult`. That's its entire contract.

**2. `alerts/` consumes `GuardResult` — nothing else from the core.**
The dispatcher reads `result.verdict`, `result.faces`, `result.capture_path`, `result.frame_bgr`.
It does not re-run recognition or re-open the camera.

**3. `guard_core.run()` never raises.**
Every exception path returns a `GuardResult` with the appropriate `Verdict`.
The alert layer always receives a typed result regardless of what failed internally.

**4. All alert functions never raise.**
`send_alert()`, `play_siren()`, `describe_intruder()` — all return typed values or `None`.
Exceptions are caught at the boundary, logged, and the function returns gracefully.

**5. All path `~` expansion happens in `config.py` only.**
No `Path("~").expanduser()` anywhere else in the codebase.

**6. Config validation happens in `config.py` only.**
No `cfg.get("key", default)` patterns scattered through other modules.
Other modules receive typed `AppConfig` attributes — never raw dicts.

---

## Patterns to follow

**Logging:** Use `get_logger()` from `faceguard.logger`. Never use `print()` in library code (`faceguard/` package). `face_guard.py` and `enroll.py` (CLI layer) may print to stdout for user-facing output.

```python
from faceguard.logger import get_logger
log = get_logger()
log.info("event_name", key="value", count=3)
log.warn("something_wrong", detail=str(exc))
log.exception("unexpected_error", exc, context="guard_core.run")
```

**Error taxonomy:** Use the exception classes that already exist — `ConfigError`, `RosterError`, `CameraError`, `LockError`. Don't raise `ValueError` or `RuntimeError` for domain errors.

**Result objects:** Add fields to `GuardResult` in `result.py` if a new layer needs to pass data downstream. Don't add parameters to `dispatch()` — everything flows through the result object.

**Tests:** Use fixtures from `conftest.py`. Don't create temp directories manually — use `tmp_path` (pytest built-in) or the `tmp` fixture. Mock at the module boundary:
- Camera: `patch("faceguard.guard_core.open_camera")`
- face_recognition: `patch("face_recognition.face_locations")`
- Discord: `patch("faceguard.alerts.discord.requests.post")`
- LM Studio: `patch("faceguard.vision.requests.post")`

**Roster encodings:** Always `np.float64`, shape `(128,)`, unit-normalised. `_make_encoding(seed=N)` in conftest produces correct fake encodings.

**Dispatcher execution order:** siren (background thread) → LM Studio description → Discord send → join siren thread. This order is intentional — don't reorder.

---

## User data and secrets

`~/.faceguard/` is the user data directory. It is gitignored and must never be committed.

| Path | Contents |
|---|---|
| `~/.faceguard/config.json` | Webhook URL, thresholds — contains secrets |
| `~/.faceguard/roster.pkl` | Face encodings — personal biometric data |
| `~/.faceguard/photos/` | Capture and enrolled photos |
| `~/.faceguard/logs/` | Structured JSON line logs |

**Never suggest hardcoding secrets.** If a value comes from config, read it from `AppConfig`. If a test needs a webhook URL, use `"https://discord.com/api/webhooks/test/token"`.

---

## LaunchAgent notes

The installed plist lives at `~/Library/LaunchAgents/com.faceguard.guard.plist`. It is generated by `make install` from `scripts/com.faceguard.guard.plist.template` via `sed` substitution. **Never edit the installed plist directly** — always edit the template and reinstall.

The plist uses the absolute path to `.venv/bin/python3`. If the venv is recreated, run `make install` again to update the embedded path.

---

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `make enroll` fails with `Error 1` silently | Config webhook not set or JSON malformed | `make diagnose` |
| `Please install face_recognition_models` | Missing GitHub-only companion package | `uv pip install git+https://github.com/ageitgey/face_recognition_models` |
| Guard exits code 2 at every login | Wrong Python path in LaunchAgent plist | `make install` to regenerate plist |
| Camera returns blank frames | Camera permission not granted to `.venv/bin/python3` | System Settings → Privacy → Camera |
| LM Studio returns None | Model not loaded or server not running | Soft dependency — alert still sends without description |
| Stale PID blocks startup | Previous crash left `~/.faceguard/faceguard.pid` | `rm ~/.faceguard/faceguard.pid` |

---

## What not to do

- **Do not** add `print()` statements to anything inside `faceguard/` — use `get_logger()`
- **Do not** add upper-bound version pins to `requirements.txt` without a specific reason in a comment
- **Do not** add new runtime dependencies without updating `requirements.txt` and discussing in an issue first (current surface: `face_recognition`, `opencv-python`, `requests`, `numpy`)
- **Do not** modify `roster.pkl` directly — always use `Roster.load()` / `roster.save()`
- **Do not** call `open_camera()` in tests — mock it
- **Do not** make `guard_core.run()` raise an exception — return a `GuardResult` with a fault `Verdict` instead
- **Do not** put business logic in `face_guard.py` — it is a thin entry point only (startup delay, PID lock, config init, call core, call dispatch, exit)
- **Do not** suggest activating the venv with `source .venv/bin/activate` in make targets — Makefile uses `$(PYTHON)` (absolute path) directly
- **Do not** suggest `python3 -m venv` — this project uses `uv venv`
- **Do not** edit plist files in `~/Library/LaunchAgents/` directly — use `make install` / `make uninstall`
