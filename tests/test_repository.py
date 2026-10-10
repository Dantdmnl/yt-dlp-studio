import re
import shutil
import subprocess
import unittest
from urllib.parse import unquote, urlsplit

from tools.build_release import ROOT
from ytdlp_studio import backend
from ytdlp_studio.version import APP_VERSION


class RepositoryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "Requires a Git checkout")
    def test_gitignore_covers_private_artifacts_without_hiding_source(self):
        ignored = (
            ".venv/Scripts/python.exe", ".venv-test/secret.txt", "dist/release.zip", "build/ui-check/view.png",
            "__pycache__/module.pyc", ".coverage", ".ruff_cache/data", "cookies.txt", "youtube-cookies.txt",
            "private.cookies.json", ".env", ".env.local", "credentials.json", "settings.json",
            "startup-error.log", "downloads/cover.jpg", "music/cover.webp", "song.mp3", "song.flac",
            "video.webm", "video.mp4", "video.part", "video.part-Frag1", "video.ytdl", "copy.sync-temp",
            "Artist/download-archive.txt", "Artist/spotdl-archive.txt", "Artist/_duplicates_review/song.mp3",
            ".agents/notes.md", ".codex/config.toml", ".vscode/settings.json", "Desktop.ini",
        )
        public = ("README.md", "Setup.cmd", "install.ps1", "requirements-spotify.txt", ".env.example",
                  "ytdlp_studio/gui.py", "tests/fixtures.py", "docs/user-guide.md", ".github/workflows/test.yml",
                  "assets/icon.png", "tests/fixtures/catalogue.json", "LICENSE", "pyproject.toml")
        result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=ROOT,
                                input="\0".join(ignored + public) + "\0", capture_output=True, text=True,
                                **backend.subprocess_window_kwargs())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(filter(None, result.stdout.split("\0"))), set(ignored))

    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "Requires a Git checkout")
    def test_no_ignored_artifacts_are_already_tracked(self):
        result = subprocess.run(["git", "ls-files", "--cached", "--ignored", "--exclude-standard"], cwd=ROOT,
                                capture_output=True, text=True, **backend.subprocess_window_kwargs())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", "Ignored artifacts already tracked by Git need explicit review")

    def test_documentation_local_links_exist(self):
        for document in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                target = urlsplit(link.strip("<>"))
                if target.scheme or target.netloc or not target.path:
                    continue
                with self.subTest(document=document.name, link=link):
                    self.assertTrue((document.parent / unquote(target.path)).is_file())

    def test_quick_start_is_short_and_version_aligned(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(readme.splitlines()), 80)
        self.assertIn(f"**{APP_VERSION}**", readme)
        self.assertIn("Setup.cmd", readme)
        self.assertIn(f"## {APP_VERSION}", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("powershell.exe"), "Requires Windows PowerShell")
    def test_powershell_scripts_parse(self):
        for name in ("install.ps1", "run-gui.ps1"):
            path = str(ROOT / name).replace("'", "''")
            command = ("$tokens = $null; $errors = $null; "
                       f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', "
                       "[ref]$tokens, [ref]$errors); if ($errors.Count) { $errors | Out-String | "
                       "Write-Output; exit 1 }; exit 0")
            with self.subTest(script=name):
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                        capture_output=True, text=True, timeout=30,
                                        **backend.subprocess_window_kwargs())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
