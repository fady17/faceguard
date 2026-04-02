"""
faceguard/roster.py

Pure data layer for the face encoding roster.
Knows nothing about cameras, CLI, or the guard — only storage and lookup.

Storage format:
  roster.pkl is a pickled dict with this shape:
  {
    "name": {
      "encodings": [ [128-float list], ... ],   # one per enrolled sample
      "enrolled_at": "2025-01-15T09:00:00+00:00",
      "enrolled_photo": "/path/to/enrolled/name_0.jpg",
      "sample_count": 3
    }
  }

Why pickle and not JSON:
  face_recognition encodings are numpy arrays. Pickle round-trips them
  losslessly with no serialization overhead. The roster is local, personal,
  never sent over the wire — pickle's "don't load untrusted files" caveat
  doesn't apply here.

Why store multiple encodings per person:
  One photo = one angle, one lighting condition. Storing 3-5 samples from
  slightly different angles dramatically improves match rate without tuning
  the tolerance down (which increases false positives).
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np


class RosterError(Exception):
    """Raised on any roster read/write failure."""


@dataclass
class RosterEntry:
    name: str
    encodings: list[np.ndarray]
    enrolled_at: str
    enrolled_photo: str          # path to best reference photo saved on disk
    sample_count: int


@dataclass
class Roster:
    """
    In-memory representation of the roster.
    Call save() to persist. All mutations go through methods — no direct
    dict access — so the file is never left in a partial state.
    """
    _entries: dict[str, RosterEntry] = field(default_factory=dict)

    # ── Persistence ────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "Roster":
        """
        Load roster from disk.
        Returns an empty Roster if the file doesn't exist yet (first run).
        Raises RosterError if the file exists but is corrupt.
        """
        if not path.exists():
            return cls()

        try:
            raw = pickle.loads(path.read_bytes())
        except Exception as exc:
            raise RosterError(
                f"Roster file at {path} is corrupt or unreadable: {exc}\n"
                f"If you have a backup, restore it. Otherwise delete the file "
                f"and re-enroll all faces."
            ) from exc

        if not isinstance(raw, dict):
            raise RosterError(
                f"Roster file has unexpected format (got {type(raw).__name__}, expected dict). "
                f"Delete {path} and re-enroll."
            )

        entries: dict[str, RosterEntry] = {}
        for name, data in raw.items():
            try:
                entries[name] = RosterEntry(
                    name=name,
                    encodings=data["encodings"],
                    enrolled_at=data["enrolled_at"],
                    enrolled_photo=data.get("enrolled_photo", ""),
                    sample_count=data.get("sample_count", len(data["encodings"])),
                )
            except (KeyError, TypeError) as exc:
                raise RosterError(
                    f"Roster entry for '{name}' is malformed: {exc}. "
                    f"Re-enroll this person with: python enroll.py add {name}"
                ) from exc

        return cls(_entries=entries)

    def save(self, path: Path) -> None:
        """
        Atomically write roster to disk.
        Uses a temp file + rename to avoid partial writes corrupting the roster
        if the process is killed mid-write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pkl.tmp")

        raw = {
            name: {
                "encodings": entry.encodings,
                "enrolled_at": entry.enrolled_at,
                "enrolled_photo": entry.enrolled_photo,
                "sample_count": entry.sample_count,
            }
            for name, entry in self._entries.items()
        }

        try:
            tmp.write_bytes(pickle.dumps(raw))
            tmp.replace(path)   # atomic on POSIX
        except OSError as exc:
            raise RosterError(f"Failed to save roster to {path}: {exc}") from exc

    # ── Mutations ──────────────────────────────────────────────────────────────

    def add(
        self,
        name: str,
        encodings: list[np.ndarray],
        enrolled_photo: str = "",
    ) -> None:
        """Add or replace a person's encodings."""
        self._entries[name] = RosterEntry(
            name=name,
            encodings=encodings,
            enrolled_at=datetime.now(timezone.utc).isoformat(),
            enrolled_photo=enrolled_photo,
            sample_count=len(encodings),
        )

    def remove(self, name: str) -> bool:
        """Remove a person. Returns True if they existed, False if not found."""
        if name in self._entries:
            del self._entries[name]
            return True
        return False

    # ── Queries ────────────────────────────────────────────────────────────────

    def all_encodings(self) -> tuple[list[np.ndarray], list[str]]:
        """
        Return (encodings_flat, names_flat) suitable for passing directly to
        face_recognition.compare_faces() and face_recognition.face_distance().

        Each encoding gets its own entry in both lists so the index alignment
        face_recognition expects is maintained even when a person has multiple samples.
        """
        encodings: list[np.ndarray] = []
        names: list[str] = []
        for name, entry in self._entries.items():
            for enc in entry.encodings:
                encodings.append(enc)
                names.append(name)
        return encodings, names

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def get(self, name: str) -> Optional[RosterEntry]:
        return self._entries.get(name)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    # ── Export / Import ────────────────────────────────────────────────────────

    def export_bytes(self) -> bytes:
        """Serialize to bytes for export. Same format as the .pkl file."""
        return pickle.dumps({
            name: {
                "encodings": entry.encodings,
                "enrolled_at": entry.enrolled_at,
                "enrolled_photo": entry.enrolled_photo,
                "sample_count": entry.sample_count,
            }
            for name, entry in self._entries.items()
        })

    def merge_from_bytes(self, data: bytes) -> list[str]:
        """
        Import encodings from exported bytes.
        Existing entries are NOT overwritten — import only adds new names.
        Returns list of names that were actually added.
        """
        try:
            raw = pickle.loads(data)
        except Exception as exc:
            raise RosterError(f"Import data is corrupt: {exc}") from exc

        added = []
        for name, entry_data in raw.items():
            if name not in self._entries:
                self._entries[name] = RosterEntry(
                    name=name,
                    encodings=entry_data["encodings"],
                    enrolled_at=entry_data["enrolled_at"],
                    enrolled_photo=entry_data.get("enrolled_photo", ""),
                    sample_count=entry_data.get("sample_count", len(entry_data["encodings"])),
                )
                added.append(name)
        return added
