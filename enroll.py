#!/usr/bin/env python3
"""
enroll.py — faceguard enrollment CLI

Commands:
  add <name>         Capture your face and add it to the roster
  remove <name>      Remove a person from the roster
  list               Show all enrolled people
  verify             Test your face against the roster right now
  export <file>      Export roster to a file for backup or sharing
  import <file>      Merge an exported roster into the local roster

Usage:
  python enroll.py add Fady
  python enroll.py list
  python enroll.py verify
  python enroll.py remove Fady
  python enroll.py export ~/Desktop/roster_backup.pkg
  python enroll.py import ~/Desktop/roster_backup.pkg

Design notes:
  - All output goes to stdout (human-readable)
  - All errors print a clear message and exit with code 1
  - No silent failures anywhere
  - enroll.py never touches the guard's PID file or alert system
    it is a pure enrollment tool, nothing more
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn 
# ── Dependency check before anything else ─────────────────────────────────────
# Give a clear install message instead of a raw ImportError buried in a traceback.


def _check_deps() -> None:
    missing = []
    for pkg, import_name in [("face_recognition", "face_recognition"), ("cv2", "opencv-python")]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(import_name)
    if missing:
        print("✗  Missing dependencies. Install with:")
        print(f"   pip install {' '.join(missing)}")
        print()
        print("   Note: face_recognition requires dlib. On macOS:")
        print("   brew install cmake && pip install face_recognition")
        sys.exit(1)

_check_deps()

import cv2
import face_recognition
import numpy as np

# Add project root to path so we can import faceguard package
sys.path.insert(0, str(Path(__file__).parent))

from faceguard.config import load_config, ConfigError
from faceguard.roster import Roster, RosterError
from faceguard.camera import (
    CameraError,
    open_camera,
    capture_frames_burst,
    frames_with_faces,
    save_frame,
)

# ── Constants ──────────────────────────────────────────────────────────────────

ENROLL_SAMPLE_COUNT = 5          # frames captured per enrollment session
ENROLL_FRAME_INTERVAL = 0.5      # seconds between frames
MIN_FACE_SAMPLES = 2             # minimum frames with a detected face to accept enrollment
VERIFY_CAPTURE_COUNT = 3         # frames used during verify command


# ── Helpers ────────────────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 48 - len(title))}")


def _print_ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _print_warn(msg: str) -> None:
    print(f"  ⚠  {msg}")


def _print_err(msg: str) -> None:
    print(f"  ✗  {msg}", file=sys.stderr)


def _exit_err(msg: str) -> NoReturn:
    _print_err(msg)
    sys.exit(1)


def _load_config_and_roster() -> tuple:
    """Load config + roster, exit cleanly on any failure."""
    try:
        cfg = load_config()
    except ConfigError as exc:
        _exit_err(f"Config error: {exc}")

    try:
        roster = Roster.load(cfg.paths.roster_file)
    except RosterError as exc:
        _exit_err(f"Roster error: {exc}")

    return cfg, roster


def _best_encodings(
    frames_and_locations: list[tuple],
    max_samples: int = 5,
) -> list[np.ndarray]:
    """
    Extract face encodings from (frame, locations) tuples.
    We compute encodings for all detected faces and return up to max_samples.

    Why not just use the first frame:
      Multiple encodings from slightly different angles give the guard a better
      chance of matching under varied real-world capture conditions (morning light,
      glasses, slight angle). 3-5 samples is the sweet spot — beyond that you get
      diminishing returns and a slower guard startup.
    """
    encodings = []
    for result, locations in frames_and_locations:
        if len(encodings) >= max_samples:
            break
        # If multiple faces detected in one frame, only use the largest one
        # (closest to the camera = the person being enrolled)
        if len(locations) > 1:
            largest = max(locations, key=lambda loc: (loc[2] - loc[0]) * (loc[1] - loc[3]))
            locations = [largest]

        enc_list = face_recognition.face_encodings(result.frame_rgb, locations)
        if enc_list:
            encodings.append(enc_list[0])

    return encodings


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_add(name: str) -> None:
    """Capture face and add to roster."""
    _print_header(f"Enrolling: {name}")

    cfg, roster = _load_config_and_roster()

    if name in roster:
        print(f"\n  Person '{name}' is already enrolled.")
        answer = input("  Re-enroll and replace existing data? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted.")
            sys.exit(0)

    print(f"\n  Opening camera (index {cfg.recognition.camera_index})...")
    try:
        cap = open_camera(index=cfg.recognition.camera_index)
    except CameraError as exc:
        _exit_err(str(exc))

    print(f"  Camera ready. Capturing {ENROLL_SAMPLE_COUNT} frames...")
    print("  → Look directly at the camera. Slight angle variations are good.")
    print()

    # Short countdown so the person is ready
    for i in (3, 2, 1):
        print(f"     {i}...", end="\r", flush=True)
        time.sleep(0.8)
    print("     Capturing...       ")

    frames = capture_frames_burst(cap, count=ENROLL_SAMPLE_COUNT, interval=ENROLL_FRAME_INTERVAL)
    cap.release()

    print(f"  Captured {len(frames)} frame(s). Detecting faces...")

    frames_with_face = frames_with_faces(frames)

    if len(frames_with_face) < MIN_FACE_SAMPLES:
        _exit_err(
            f"Only {len(frames_with_face)} frame(s) contained a detectable face "
            f"(need at least {MIN_FACE_SAMPLES}).\n"
            f"  Tips:\n"
            f"    - Ensure good lighting (face the light source, not your back to it)\n"
            f"    - Look directly at the camera\n"
            f"    - Remove glasses if detection is failing\n"
            f"    - Run: python enroll.py add {name}  (try again)"
        )

    encodings = _best_encodings(frames_with_face, max_samples=ENROLL_SAMPLE_COUNT)

    if not encodings:
        _exit_err(
            "Faces were located but encodings could not be computed. "
            "This is unusual — try again in better lighting."
        )

    # Save best reference photo (first frame with a face)
    best_frame = frames_with_face[0][0].frame
    safe_name = name.lower().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = cfg.paths.enrolled_dir / f"{safe_name}_{ts}.jpg"
    save_frame(best_frame, photo_path)

    # Persist to roster
    try:
        roster.add(name=name, encodings=encodings, enrolled_photo=str(photo_path))
        roster.save(cfg.paths.roster_file)
    except RosterError as exc:
        _exit_err(f"Failed to save roster: {exc}")

    _print_ok(f"Enrolled '{name}' with {len(encodings)} encoding sample(s)")
    _print_ok(f"Reference photo saved: {photo_path}")
    _print_ok(f"Roster saved: {cfg.paths.roster_file}")
    print()
    print("  Run  python enroll.py verify  to test the match.")
    print()


def cmd_remove(name: str) -> None:
    """Remove a person from the roster."""
    _print_header(f"Remove: {name}")
    cfg, roster = _load_config_and_roster()

    if name not in roster:
        _exit_err(f"'{name}' is not in the roster. Run: python enroll.py list")

    answer = input(f"  Remove '{name}' from the roster? [y/N] ").strip().lower()
    if answer != "y":
        print("  Aborted.")
        sys.exit(0)

    roster.remove(name)
    try:
        roster.save(cfg.paths.roster_file)
    except RosterError as exc:
        _exit_err(f"Failed to save roster after removal: {exc}")

    _print_ok(f"'{name}' removed from roster.")
    print(f"  Enrolled photo (if any) was NOT deleted — find it at: {cfg.paths.enrolled_dir}")
    print()


def cmd_list() -> None:
    """List all enrolled people."""
    _print_header("Enrolled roster")
    cfg, roster = _load_config_and_roster()

    if roster.is_empty():
        print("\n  Roster is empty. Enroll yourself first:")
        print("  python enroll.py add <your-name>")
        print()
        return

    print(f"\n  {'Name':<24} {'Samples':<10} {'Enrolled at'}")
    print(f"  {'─'*24} {'─'*10} {'─'*28}")
    for name in roster.names():
        entry = roster.get(name)
        print(f"  {name:<24} {entry.sample_count:<10} {entry.enrolled_at[:19].replace('T', ' ')}")
    print(f"\n  Total: {len(roster)} person(s)")
    print()


def cmd_verify() -> None:
    """
    Test your face against the roster right now.
    Useful after enrollment to confirm the match works before trusting the guard.
    """
    _print_header("Verify identity")
    cfg, roster = _load_config_and_roster()

    if roster.is_empty():
        _exit_err("Roster is empty. Enroll first: python enroll.py add <name>")

    print(f"\n  Opening camera...")
    try:
        cap = open_camera(index=cfg.recognition.camera_index)
    except CameraError as exc:
        _exit_err(str(exc))

    print(f"  Capturing {VERIFY_CAPTURE_COUNT} frame(s) for verification...")

    for i in (3, 2, 1):
        print(f"     {i}...", end="\r", flush=True)
        time.sleep(0.6)
    print("     Capturing...       ")

    frames = capture_frames_burst(cap, count=VERIFY_CAPTURE_COUNT, interval=0.4)
    cap.release()

    frames_with_face = frames_with_faces(frames)
    if not frames_with_face:
        _exit_err(
            "No face detected in any captured frame. "
            "Check lighting and camera position, then try again."
        )

    known_encodings, known_names = roster.all_encodings()
    tolerance = cfg.recognition.tolerance

    results: dict[str, list[float]] = {}  # name → list of distances (lower = better match)

    for result, locations in frames_with_face:
        enc_list = face_recognition.face_encodings(result.frame_rgb, locations)
        for enc in enc_list:
            distances = face_recognition.face_distance(known_encodings, enc)
            for name, dist in zip(known_names, distances):
                results.setdefault(name, []).append(float(dist))

    print()
    print(f"  {'Name':<24} {'Best distance':<16} {'Result'}")
    print(f"  {'─'*24} {'─'*16} {'─'*10}")

    matched_names = []
    for name in roster.names():
        dists = results.get(name, [])
        if not dists:
            print(f"  {name:<24} {'—':<16} no data")
            continue
        best = min(dists)
        match = best <= tolerance
        indicator = "✓  MATCH" if match else "✗  no match"
        print(f"  {name:<24} {best:<16.4f} {indicator}")
        if match:
            matched_names.append(name)

    print()
    if matched_names:
        _print_ok(f"Identity confirmed: {', '.join(matched_names)}")
        print(f"  (tolerance={tolerance}, lower distance = stronger match)")
    else:
        _print_warn("No match found against any enrolled person.")
        print(f"  If this is unexpected, try re-enrolling: python enroll.py add <name>")
        print(f"  Current tolerance: {tolerance}  (raise to ~0.6 for looser matching)")
    print()


def cmd_export(output_path: str) -> None:
    """Export roster to a file."""
    _print_header("Export roster")
    cfg, roster = _load_config_and_roster()

    if roster.is_empty():
        _exit_err("Roster is empty — nothing to export.")

    out = Path(output_path).expanduser().resolve()
    try:
        out.write_bytes(roster.export_bytes())
    except OSError as exc:
        _exit_err(f"Could not write export file: {exc}")

    _print_ok(f"Roster exported to: {out}")
    _print_ok(f"Contains {len(roster)} person(s): {', '.join(roster.names())}")
    print()


def cmd_import(input_path: str) -> None:
    """Merge an exported roster into the local roster."""
    _print_header("Import roster")
    cfg, roster = _load_config_and_roster()

    src = Path(input_path).expanduser().resolve()
    if not src.exists():
        _exit_err(f"Import file not found: {src}")
   
    try:
        data = src.read_bytes()
        added = roster.merge_from_bytes(data)
    except RosterError as exc:
        _exit_err(f"Import failed: {exc}")

    if not added:
        print("  No new people were added (all names already exist in roster).")
        print("  Import does not overwrite existing entries.")
        print()
        return

    try:
        roster.save(cfg.paths.roster_file)
    except RosterError as exc:
        _exit_err(f"Imported data but failed to save roster: {exc}")

    _print_ok(f"Added {len(added)} new person(s): {', '.join(added)}")
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

USAGE = """
faceguard enrollment tool

Usage:
  python enroll.py add <name>        Enroll a new face
  python enroll.py remove <name>     Remove a person from the roster
  python enroll.py list              Show all enrolled people
  python enroll.py verify            Test your face against the roster
  python enroll.py export <file>     Export roster to a backup file
  python enroll.py import <file>     Merge an exported roster into local roster

First time? Run:
  python setup.py
  python enroll.py add <your-name>
  python enroll.py verify
"""


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "add":
        if len(args) < 2:
            _exit_err("Usage: python enroll.py add <name>")
        cmd_add(" ".join(args[1:]))

    elif cmd == "remove":
        if len(args) < 2:
            _exit_err("Usage: python enroll.py remove <name>")
        cmd_remove(" ".join(args[1:]))

    elif cmd == "list":
        cmd_list()

    elif cmd == "verify":
        cmd_verify()

    elif cmd == "export":
        if len(args) < 2:
            _exit_err("Usage: python enroll.py export <output-file>")
        cmd_export(args[1])

    elif cmd == "import":
        if len(args) < 2:
            _exit_err("Usage: python enroll.py import <input-file>")
        cmd_import(args[1])

    else:
        _exit_err(f"Unknown command: '{cmd}'\n{USAGE}")


if __name__ == "__main__":
    main()
