import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.build_release import ROOT, RUNTIME_FILES, build_release, release_notes
from ytdlp_studio import backend
from ytdlp_studio.version import APP_VERSION


class ReleaseTests(unittest.TestCase):
    def test_release_notes_focus_on_current_version_and_setup(self):
        notes = release_notes()
        self.assertIn(f"Changes In {APP_VERSION}", notes)
        self.assertIn("Setup.cmd", notes)
        self.assertIn(f"yt-dlp-studio-v{APP_VERSION}.zip", notes)
        self.assertNotIn("0.1.0 - Initial Development", notes)
        self.assertNotIn("Unreleased", notes)

    def test_release_notes_require_dated_nonempty_current_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for text in (f"## {APP_VERSION} - Unreleased\n\n- Change\n",
                         "## 0.0.0 - 2026-10-10\n\n- Change\n",
                         f"## {APP_VERSION} - 2026-10-10\n"):
                with self.subTest(changelog=text):
                    (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        release_notes(root)

    @unittest.skipIf(os.name != "nt" and not os.environ.get("DISPLAY"), "GUI check needs a display")
    def test_extracted_package_opens_gui_without_development_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = build_release(base / "release")
            extracted = base / "fresh package"
            with zipfile.ZipFile(archive) as package:
                package.extractall(extracted)
            code = """
from pathlib import Path
from unittest.mock import patch
from ytdlp_studio import backend as core
from ytdlp_studio.gui import StudioApp
package_root = Path.cwd().resolve()
assert Path(core.__file__).resolve().is_relative_to(package_root), (core.__file__, str(package_root))
settings = core.Settings(output_dir=str(Path.cwd() / 'sample library'),
                         watch_clipboard=False, auto_preview=False)
with patch.object(core, 'load_settings', return_value=settings), \\
     patch.object(core, 'save_settings'), \\
     patch.object(core, 'check_environment', return_value=['Package check']):
    app = StudioApp()
    app.withdraw()
    app.update()
    assert app.simple_format_var.get() == 'MP3'
    assert app.simple_one_folder.winfo_exists()
    app.simple_one_folder.invoke()
    assert not app._settings_from_ui().spotify_album_folders
    app.destroy()
print('Extracted source GUI initialized successfully')
"""
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=extracted,
                                    capture_output=True, text=True, timeout=30,
                                    **backend.subprocess_window_kwargs())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("initialized successfully", result.stdout)
            self.assertFalse((extracted / "sample library").exists())

    def test_archive_is_runnable_and_excludes_local_and_development_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = build_release(base / "release", tag=f"v{APP_VERSION}")
            self.assertEqual(archive.with_suffix(".zip.sha256").read_text(encoding="ascii"),
                             f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                self.assertIn("ytdlp_studio/gui.py", names)
                self.assertIn("ytdlp_studio/inspection.py", names)
                self.assertIn("Setup.cmd", names)
                self.assertIn("docs/user-guide.md", names)
                self.assertIn("docs/troubleshooting.md", names)
                self.assertFalse(any(name.startswith((".git", ".venv", "tests/", "tools/", "build/", "dist/")) or "__pycache__" in name for name in names))
                package.extractall(base / "extracted")
            result = subprocess.run([sys.executable, "-B", str(base / "extracted" / "ytdlp_wrapper.py"), "--version"],
                                    cwd=base, capture_output=True, text=True, **backend.subprocess_window_kwargs())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(APP_VERSION, result.stdout)

    def test_private_files_cannot_leak_into_the_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for name in RUNTIME_FILES:
                shutil.copyfile(ROOT / name, root / name)
            shutil.copytree(ROOT / "ytdlp_studio", root / "ytdlp_studio", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "docs", root / "docs")
            private = (".env", "cookies.txt", "settings.json", "download-archive.txt", "spotdl-archive.txt",
                       "music/song.mp3", "_duplicates_review/song.mp3", ".venv/secret.py", ".agents/notes.md",
                       "ytdlp_studio/cookies.json", "ytdlp_studio/__pycache__/private.pyc")
            for name in private:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE-FILE-SENTINEL", encoding="ascii")
            archive = build_release(Path(directory) / "release", root=root)
            with zipfile.ZipFile(archive) as package:
                self.assertFalse(set(private).intersection(package.namelist()))
                self.assertFalse(any(b"PRIVATE-FILE-SENTINEL" in package.read(name) for name in package.namelist()))

    def test_missing_setup_entry_point_is_not_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for name in RUNTIME_FILES:
                if name != "Setup.cmd":
                    shutil.copyfile(ROOT / name, root / name)
            shutil.copytree(ROOT / "ytdlp_studio", root / "ytdlp_studio", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "docs", root / "docs")
            with self.assertRaisesRegex(ValueError, "Setup.cmd"):
                build_release(Path(directory) / "release", root=root)

    def test_wrong_version_tag_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output"
            with self.assertRaisesRegex(ValueError, "does not match"):
                build_release(target, tag="v0.0.0")
            self.assertFalse(target.exists())

    def test_incomplete_runtime_is_not_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "incomplete"
            shutil.copytree(ROOT / "ytdlp_studio", root / "ytdlp_studio", ignore=shutil.ignore_patterns("gui.py", "__pycache__"))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                build_release(Path(directory) / "release", root=root)


if __name__ == "__main__":
    unittest.main()
