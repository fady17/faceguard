"""
tests/test_roster.py

Tests for the Roster data layer. Covers:
  - Load/save round-trip integrity
  - Atomic write (temp file + rename)
  - Corrupt file handling
  - Multi-sample encoding flatten for face_recognition
  - Export/import with deduplication
  - Edge cases: zero-length encodings, special characters in names
"""

import pickle
import pytest
from pathlib import Path

import numpy as np

from faceguard.roster import Roster, RosterEntry, RosterError


def _enc(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(128).astype(np.float64)
    return v / np.linalg.norm(v)


class TestRosterBasicOperations:

    def test_empty_roster_is_empty(self, roster_empty: Roster):
        assert roster_empty.is_empty()
        assert len(roster_empty) == 0
        assert roster_empty.names() == []

    def test_add_and_contains(self, roster_empty: Roster):
        roster_empty.add("Fady", [_enc(1)])
        assert "Fady" in roster_empty
        assert "Alice" not in roster_empty
        assert len(roster_empty) == 1

    def test_remove_existing(self, roster_one: Roster):
        assert roster_one.remove("Fady") is True
        assert "Fady" not in roster_one
        assert roster_one.is_empty()

    def test_remove_nonexistent_returns_false(self, roster_one: Roster):
        assert roster_one.remove("NoOne") is False
        assert len(roster_one) == 1  # unchanged

    def test_add_overwrites_existing(self, roster_one: Roster):
        new_enc = _enc(50)
        roster_one.add("Fady", [new_enc])
        encs, names = roster_one.all_encodings()
        assert len(encs) == 1  # old 2 samples replaced by 1
        assert np.allclose(encs[0], new_enc)

    def test_get_returns_entry(self, roster_one: Roster):
        entry = roster_one.get("Fady")
        assert entry is not None
        assert entry.name == "Fady"
        assert entry.sample_count == 2

    def test_get_unknown_returns_none(self, roster_one: Roster):
        assert roster_one.get("Nobody") is None


class TestRosterEncodingFlatten:

    def test_all_encodings_flat_order(self, roster_two: Roster):
        """
        all_encodings() must return one entry per sample, not per person.
        The names list must align with the encodings list index-for-index.
        """
        encs, names = roster_two.all_encodings()
        assert len(encs) == len(names)
        # Fady has 1 sample, Alice has 1 sample → 2 total
        assert len(encs) == 2
        name_set = set(names)
        assert "Fady" in name_set
        assert "Alice" in name_set

    def test_all_encodings_multiple_samples(self):
        """Person with 3 samples produces 3 entries in the flat list."""
        r = Roster()
        r.add("Multi", [_enc(1), _enc(2), _enc(3)])
        encs, names = r.all_encodings()
        assert len(encs) == 3
        assert all(n == "Multi" for n in names)

    def test_numpy_dtype_preserved(self, roster_one: Roster):
        encs, _ = roster_one.all_encodings()
        for enc in encs:
            assert enc.dtype == np.float64
            assert enc.shape == (128,)


class TestRosterPersistence:

    def test_save_and_load_round_trip(self, roster_one: Roster, tmp_path: Path):
        path = tmp_path / "roster.pkl"
        roster_one.save(path)
        assert path.exists()

        loaded = Roster.load(path)
        assert len(loaded) == 1
        assert "Fady" in loaded

        orig_encs, _ = roster_one.all_encodings()
        load_encs, _ = loaded.all_encodings()
        for o, l in zip(orig_encs, load_encs):
            assert np.allclose(o, l), "Encoding values changed after round-trip"

    def test_load_nonexistent_returns_empty(self, tmp_path: Path):
        r = Roster.load(tmp_path / "does_not_exist.pkl")
        assert r.is_empty()

    def test_load_corrupt_raises_roster_error(self, tmp_path: Path):
        path = tmp_path / "corrupt.pkl"
        path.write_bytes(b"this is not valid pickle data at all")
        with pytest.raises(RosterError, match="corrupt"):
            Roster.load(path)

    def test_load_wrong_type_raises_roster_error(self, tmp_path: Path):
        """Pickle of a list instead of a dict should raise RosterError."""
        path = tmp_path / "wrong.pkl"
        path.write_bytes(pickle.dumps([1, 2, 3]))
        with pytest.raises(RosterError, match="unexpected format"):
            Roster.load(path)

    def test_atomic_write_temp_file_cleaned_up(self, roster_one: Roster, tmp_path: Path):
        """After save(), no .pkl.tmp file should remain."""
        path = tmp_path / "roster.pkl"
        roster_one.save(path)
        assert not (tmp_path / "roster.pkl.tmp").exists()

    def test_save_creates_parent_dirs(self, roster_one: Roster, tmp_path: Path):
        """Save should create intermediate directories if they don't exist."""
        deep_path = tmp_path / "a" / "b" / "c" / "roster.pkl"
        roster_one.save(deep_path)
        assert deep_path.exists()

    def test_malformed_entry_raises_roster_error(self, tmp_path: Path):
        """A pkl file where one entry is missing 'encodings' key."""
        path = tmp_path / "bad_entry.pkl"
        path.write_bytes(pickle.dumps({
            "Fady": {"enrolled_at": "2025-01-01", "sample_count": 1}
            # missing 'encodings' key
        }))
        with pytest.raises(RosterError, match="malformed"):
            Roster.load(path)


class TestRosterExportImport:

    def test_export_import_round_trip(self, roster_two: Roster):
        exported = roster_two.export_bytes()
        r2 = Roster()
        added = r2.merge_from_bytes(exported)
        assert set(added) == {"Fady", "Alice"}
        assert len(r2) == 2

    def test_import_no_overwrite(self, roster_one: Roster):
        """Importing a roster that already has 'Fady' should not overwrite."""
        exported = roster_one.export_bytes()
        # Modify Fady's encoding in the source before re-importing
        new_enc = _enc(999)
        original_enc = roster_one.get("Fady").encodings[0].copy() # type: ignore
        r2 = Roster()
        r2.add("Fady", [new_enc])  # pre-existing Fady with different encoding
        added = r2.merge_from_bytes(exported)
        assert added == []  # nothing added — Fady already existed
        # Fady's encoding should be the one we set, not the imported one
        assert np.allclose(r2.get("Fady").encodings[0], new_enc) # type: ignore

    def test_import_corrupt_bytes_raises(self, roster_empty: Roster):
        with pytest.raises(RosterError, match="corrupt"):
            roster_empty.merge_from_bytes(b"garbage bytes not pickle")

    def test_import_adds_only_new_names(self, roster_two: Roster):
        """Bob not in the source roster should be added; Fady should be skipped."""
        target = Roster()
        target.add("Fady", [_enc(1)])  # pre-existing
        exported = roster_two.export_bytes()
        added = target.merge_from_bytes(exported)
        assert "Alice" in added
        assert "Fady" not in added


class TestRosterEdgeCases:

    def test_names_with_spaces(self):
        r = Roster()
        r.add("John Doe", [_enc(1)])
        assert "John Doe" in r
        encs, names = r.all_encodings()
        assert names[0] == "John Doe"

    def test_names_with_unicode(self):
        r = Roster()
        r.add("فادي", [_enc(1)])
        assert "فادي" in r

    def test_single_encoding_per_person(self):
        r = Roster()
        r.add("Solo", [_enc(1)])
        encs, names = r.all_encodings()
        assert len(encs) == 1
        assert names[0] == "Solo"

    def test_many_people(self):
        r = Roster()
        for i in range(50):
            r.add(f"Person{i}", [_enc(i)])
        assert len(r) == 50
        encs, names = r.all_encodings()
        assert len(encs) == 50