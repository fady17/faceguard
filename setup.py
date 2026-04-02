"""
setup.py — First-run scaffold for faceguard.

Run once after cloning:
    python setup.py

What it does:
  1. Creates ~/.faceguard/ directory tree
  2. Copies config.example.json → ~/.faceguard/config.json if not already present
  3. Creates a blank roster placeholder
  4. Prints next steps

Why a separate setup script rather than auto-creating on first guard run:
  The guard runs unattended at login. If config is missing it should FAIL LOUDLY,
  not silently create a blank config and run with no webhook configured.
  Explicit setup = explicit intent.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FACEGUARD_DIR = Path("~/.faceguard").expanduser()
SCRIPT_DIR = Path(__file__).parent.resolve()

SIREN_SOURCE = SCRIPT_DIR / "assets" / "siren" / "dragon-studio-police-siren-397963.mp3"
SIREN_TARGET_DIR = FACEGUARD_DIR / "siren"
SIREN_TARGET_FILE = SIREN_TARGET_DIR / "siren.mp3"
DIRS = [
    FACEGUARD_DIR,
    FACEGUARD_DIR / "photos" / "captures",
    FACEGUARD_DIR / "photos" / "enrolled",
    FACEGUARD_DIR / "logs",
    SIREN_TARGET_DIR,
]

CONFIG_EXAMPLE = SCRIPT_DIR / "config.example.json"
CONFIG_TARGET = FACEGUARD_DIR / "config.json"


# def patch_face_recognition_models():
#     """Patches face_recognition_models to remove pkg_resources dependency."""
#     print("\n── compatibility patch ───────────────────────────────")
#     # Identify the venv path relative to this script
#     venv_site_packages = SCRIPT_DIR / ".venv" / "lib" / "python3.12" / "site-packages"
#     target_file = venv_site_packages / "face_recognition_models" / "__init__.py"

#     if not target_file.exists():
#         print(f"  ⚠  Could not find {target_file} to patch.")
#         return

#     content = """from pathlib import Path

# _MODELS_DIR = Path(__file__).parent / "models"

# def pose_predictor_model_location():
#     return str(_MODELS_DIR / "shape_predictor_68_face_landmarks.dat")

# def pose_predictor_five_point_model_location():
#     return str(_MODELS_DIR / "shape_predictor_5_face_landmarks.dat")

# def face_recognition_model_location():
#     return str(_MODELS_DIR / "dlib_face_recognition_resnet_model_v1.dat")

# def cnn_face_detector_model_location():
#     return str(_MODELS_DIR / "mmod_human_face_detector.dat")
# """
#     try:
#         target_file.write_text(content)
#         print(f"  ✓  Patched {target_file} for Python 3.12")
#     except Exception as e:
#         print(f"  ✗  Failed to patch: {e}")

def main() -> None:
    print("── faceguard setup ───────────────────────────────────")

    # 1. Create directory tree
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  ✓  {d}")

    # 2. Copy example config if not present
    if CONFIG_TARGET.exists():
        print(f"\n  ℹ  Config already exists at {CONFIG_TARGET} — not overwriting.")
    else:
        if not CONFIG_EXAMPLE.exists():
            print(f"\n  ✗  config.example.json not found at {CONFIG_EXAMPLE}")
            print("     Clone the full repo and run setup.py from the project root.")
            sys.exit(1)
        shutil.copy(CONFIG_EXAMPLE, CONFIG_TARGET)
        print(f"\n  ✓  Config created at {CONFIG_TARGET}")
        print("     Open it and fill in your Discord webhook URL.")
    # 3. Add this logic after the config copy section:
    print("\n── assets setup ──────────────────────────────────────")
    if SIREN_SOURCE.exists():
        shutil.copy(SIREN_SOURCE, SIREN_TARGET_FILE)
        print(f"  ✓  Siren copied to {SIREN_TARGET_FILE}")
    else:
        print(f"  ⚠  Source siren not found at {SIREN_SOURCE}")
    # 4. Check dependencies and warn about face_recognition_models
    print("\n── dependency check ──────────────────────────────────")
    missing = []
    for pkg in ["face_recognition", "cv2", "requests", "numpy"]:
        try:
            __import__(pkg)
            print(f"  ✓  {pkg}")
        except ImportError:
            print(f"  ✗  {pkg}  (not installed)")
            missing.append(pkg)

    # face_recognition_models is not importable directly but face_recognition
    # will throw a clear error if it's missing — check for it explicitly
    frm_ok = False
    try:
        import face_recognition_models
        frm_ok = True
        print("  ✓  face_recognition_models")
    except ImportError:
        print("  ✗  face_recognition_models  (not installed — see below)")

    # 4. Print next steps
    print("\n── next steps ────────────────────────────────────────")

    if missing or not frm_ok:
        print("  0. Install missing dependencies:")
        if missing:
            install_names = {"cv2": "opencv-python"}
            pkgs = " ".join(install_names.get(p, p) for p in missing) # type: ignore
            print(f"     uv pip install {pkgs}")
        if not frm_ok:
            print("     uv pip install git+https://github.com/ageitgey/face_recognition_models")
        print()

    print("  1. Edit ~/.faceguard/config.json")
    print("     → Set discord.webhook_url")
    print("     → Set lm_studio.model to your loaded vision model name")
    print("     → Adjust recognition.tolerance if needed (default 0.5 is good)")
    print()
    print("  2. Enroll your face:")
    print("     make enroll")
    print()
    print("  3. Test the guard manually:")
    print("     make test")
    print()
    print("  4. Install the LaunchAgent:")
    print("     make install")
    print()
    print("  If something looks wrong:  make diagnose")
    # patch_face_recognition_models()
    print("─────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()