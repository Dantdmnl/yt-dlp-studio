import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.fixtures import ARTIST, client_for, edition, group
from ytdlp_studio import backend, library
from ytdlp_studio.catalogue import artist_discography


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.local, self.nas = self.root / "local", self.root / "nas"
        self.local.mkdir()
        self.nas.mkdir()
        self.catalogue = artist_discography(ARTIST, True, client_for([group(edition(1), edition(2))]))
        for release in self.catalogue["items"]:
            name = f"{backend.album_folder_name(release)} [{release['id']}]"
            for root in (self.local, self.nas):
                folder = root / name
                folder.mkdir()
                (folder / "song.mp3").write_bytes(b"unchanged music fixture")

    def test_both_libraries_get_readable_collision_safe_names_and_identity(self):
        changes = library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        self.assertEqual(len(changes), 4)
        library.apply_renames(changes)
        self.assertEqual({p.name for p in self.local.iterdir()}, {"2025 - Same Album", "2025 - Same Album (edition 2)"})
        for change in changes:
            self.assertFalse(change.source.exists())
            self.assertEqual(library.fingerprint(change.destination), change.files)
        mapped = backend.release_folder_names(self.catalogue, self.local)
        self.assertEqual(set(mapped.values()), {p.name for p in self.local.iterdir()})
        self.assertEqual(library.plan_legacy_renames(self.catalogue, self.local, self.nas), [])

    def test_different_nas_audio_aborts_before_any_rename(self):
        (next(self.nas.iterdir()) / "song.mp3").write_bytes(b"different recording")
        with self.assertRaisesRegex(ValueError, "do not match"):
            library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        self.assertTrue(all("[" in p.name for p in self.local.iterdir()))

    def test_missing_mirror_release_does_not_create_or_copy_audio(self):
        folder = next(self.nas.iterdir())
        (folder / "song.mp3").unlink()
        folder.rmdir()
        changes = library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        self.assertEqual(len(changes), 3)
        library.apply_renames(changes)
        self.assertEqual(len(list(self.nas.iterdir())), 1)

    def test_changed_library_and_occupied_destination_are_rejected(self):
        changes = library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        changes[0].destination.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            library.apply_renames(changes)
        changes[0].destination.rmdir()
        (changes[0].source / "song.mp3").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "changed after"):
            library.apply_renames(changes)
        self.assertTrue(all(change.source.exists() for change in changes))

    def test_failure_rolls_back_preceding_local_and_nas_renames(self):
        changes = library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        rename = Path.rename
        def fail_third(path, destination):
            if path == changes[2].source:
                raise OSError("simulated failure")
            return rename(path, destination)
        with patch.object(Path, "rename", fail_third), self.assertRaises(OSError):
            library.apply_renames(changes)
        self.assertTrue(all(change.source.exists() for change in changes))
        self.assertFalse(any(change.destination.exists() for change in changes))

    def test_unknown_artist_release_and_out_of_root_target_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            library.plan_legacy_renames({**self.catalogue, "items": []}, self.local, self.nas)
        changes = library.plan_legacy_renames(self.catalogue, self.local, self.nas)
        changes[0].destination = self.root / "elsewhere"
        with self.assertRaisesRegex(ValueError, "validated library root"):
            library.apply_renames(changes)

    def test_release_marker_prevents_overwriting_another_edition(self):
        folder = self.root / "new"
        backend.write_release_marker(folder, "A" * 22, "First")
        with self.assertRaisesRegex(ValueError, "another release"):
            backend.write_release_marker(folder, "B" * 22, "Second")

    def test_damaged_identity_aborts_instead_of_creating_duplicate_folders(self):
        folder = self.local / "Readable album"
        folder.mkdir()
        marker = folder / ".studio-release.json"
        for contents in ("[]", "{broken", '{"album_id": "short"}'):
            marker.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid release identity"):
                backend.release_folder_names(self.catalogue, self.local)
            with self.assertRaisesRegex(ValueError, "Invalid release identity"):
                library.plan_legacy_renames(self.catalogue, self.local, self.nas)
