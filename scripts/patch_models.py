from pathlib import Path
import sys

# Locate the site-packages for your current venv
site_pkgs = Path(".venv/lib/python3.12/site-packages/face_recognition_models/__init__.py")

if not site_pkgs.exists():
    print(f"Error: Could not find {site_pkgs}")
    sys.exit(1)

content = """from pathlib import Path

_MODELS_DIR = Path(__file__).parent / "models"

def pose_predictor_model_location():
    return str(_MODELS_DIR / "shape_predictor_68_face_landmarks.dat")

def pose_predictor_five_point_model_location():
    return str(_MODELS_DIR / "shape_predictor_5_face_landmarks.dat")

def face_recognition_model_location():
    return str(_MODELS_DIR / "dlib_face_recognition_resnet_model_v1.dat")

def cnn_face_detector_model_location():
    return str(_MODELS_DIR / "mmod_human_face_detector.dat")
"""

site_pkgs.write_text(content)
print(f"Successfully patched {site_pkgs}")