# faceguard

A macOS login-time face recognition guard. After you enter your password, it captures a photo from your FaceTime camera and checks whether you're on the roster. If you are — silent pass. If not — local siren, Discord alert with the photo, and an AI-generated description of the intruder courtesy of a local vision model.

**Built for macOS.** Requires Python 3.10+.

---

## What it does

```
Login → camera captures your face
              ↓
   face_recognition checks roster
        ↙              ↘
     YOU              NOT YOU
      ↓                   ↓
   silent           siren plays
                    Discord photo + description
                    capture saved locally
```

Every login event is logged locally regardless of outcome. Captured photos are stored in `~/.faceguard/photos/captures/`.

---

## Quick start

### 1. Install

```bash
git clone https://github.com/fady17/faceguard.git
cd faceguard

# Create a virtual environment with uv (recommended)
uv venv .venv

# Install dlib dependency (required by face_recognition)
brew install cmake

# Install Python dependencies
uv pip install -r requirements.txt

# face_recognition_models is a required companion package not listed on PyPI
uv pip install git+https://github.com/ageitgey/face_recognition_models

# Scaffold config and data directory
make setup
```

> **Don't have uv?** `curl -LsSf https://astral.sh/uv/install.sh | sh` — or use `python3 -m venv .venv && pip install -r requirements.txt` if you prefer.

### 2. Configure

Open `~/.faceguard/config.json` and set your Discord webhook URL:

```json
"discord": {
  "webhook_url": "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
}
```

> **How to create a webhook:** In your Discord server → *Server Settings* → *Integrations* → *Webhooks* → *New Webhook*. Takes 30 seconds.

### 3. Enroll your face

```bash
make enroll
```

Sit in the lighting you'll normally be in at login time. The camera captures 5 frames, picks the best encodings, and saves them to your local roster. Run `make verify` to confirm the match works before installing.

### 4. Install (runs at every login)

```bash
make install
```

That's it. The guard fires automatically after your next login.

---

## Verify it's working

```bash
# Dry run — recognition fires, no siren, no Discord
make test

# Live run — siren and Discord will fire if triggered
python face_guard.py --no-delay --verbose

# Check LaunchAgent status
make status

# Watch the live log
make logs
```

---

## Optional: LM Studio intruder description

When the guard detects an unknown face, it can describe the intruder's appearance in the Discord alert ("Male, 30s, dark hair, grey hoodie") using a local vision model.

1. Download [LM Studio](https://lmstudio.ai)
2. In LM Studio: download a vision model (`moondream2` is fastest, `llava-1.5-7b` is more detailed)
3. Load the model and start the local server (Developer tab → Start Server)
4. Set the model name in `~/.faceguard/config.json`:
   ```json
   "lm_studio": {
     "model": "qwen/qwen3.5-9b"
   }
   ```
5. Auto-start LM Studio at login:
   ```bash
   make install-lmstudio
   ```

The guard works without LM Studio. If it's not running at login, the Discord alert sends without the description field.

---

## Uninstall

```bash
# Remove guard from login items (keeps your roster and config)
make uninstall

# Remove everything including LM Studio autostart
make uninstall-all
```

Your enrolled face data and config in `~/.faceguard/` are never touched by uninstall.

---

## Configuration reference

All settings live in `~/.faceguard/config.json`. The file is created from `config.example.json` during `make setup`.

| Setting | Default | Description |
|---|---|---|
| `discord.webhook_url` | — | **Required.** Discord webhook URL |
| `recognition.tolerance` | `0.5` | Match threshold. Lower = stricter. Range: `0.4`–`0.6` |
| `recognition.camera_index` | `0` | Camera index. `0` = built-in FaceTime camera |
| `lm_studio.enabled` | `true` | Enable vision description layer |
| `lm_studio.model` | `moondream2` | Loaded model name, exactly as shown in LM Studio |
| `lm_studio.timeout_seconds` | `10` | Max wait for LM response before sending alert without it |
| `siren.enabled` | `true` | Play local siren on unknown face |
| `siren.sound_file` | `null` | `null` = built-in macOS sound. Absolute path for custom siren |
| `siren.volume` | `1.0` | Volume multiplier (`1.0` = system volume) |
| `siren.repeat` | `3` | Times to play the siren sound |
| `guard.startup_delay_seconds` | `8` | Seconds after login before guard fires |

**Tolerance tuning:** If you get false positives (strangers matching as you), lower to `0.45`. If you fail to match yourself, raise to `0.55` or re-enroll with `make enroll`.

---

## Enrollment commands

```bash
make enroll              # Add your face
make verify              # Test your face against the roster
python enroll.py list    # Show all enrolled people
python enroll.py remove <name>   # Remove someone
python enroll.py export ~/backup.pkg   # Export roster (backup)
python enroll.py import ~/backup.pkg   # Import roster
```

---

## Requirements

- macOS 12+ (Monterey or later recommended)
- Python 3.10+
- Homebrew (`brew install cmake` for dlib)
- A Discord server you control

### Camera permission

macOS requires explicit camera permission per Python binary. The first time you run `make enroll`, a permission dialog will appear. Click **Allow**.

If no dialog appears and you get blank captures, go to:
> System Settings → Privacy & Security → Camera

Find your `.venv/bin/python3` binary and enable it. The venv Python path is the same whether you created it with `uv venv` or `python3 -m venv`.

---

## Data stored locally

Everything is in `~/.faceguard/` and stays on your machine:

```
~/.faceguard/
├── config.json          Your settings (never shared)
├── roster.pkl           Face encodings (never shared)
├── photos/
│   ├── enrolled/        Reference photos from enrollment
│   └── captures/        Login event photos (timestamped)
└── logs/
    └── YYYY-MM-DD.jsonl Structured event log per day
```

No data is sent anywhere except:
- The capture photo → your own Discord webhook (on unknown face)
- The capture image → LM Studio running on `localhost` (if enabled)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).