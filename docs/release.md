# Release Checklist

The prepared version is `0.2.2`. No commit, tag or public release is created by changing local files or building a zip. Use a Git checkout for development; source release zips intentionally omit tests and build tools.

**Current hold:** 0.2.2 is a private prerelease containing setup, runtime detection and launcher feedback fixes. The workflow publishes prereleases; do not remove that flag, make the repository public or publish a stable build until fresh artist runs, stopping/retrying, both layouts and optional NAS mirroring have passed user acceptance. Automated catalogue/matching checks are not proof that every recording can be downloaded.

1. Set `APP_VERSION` in `ytdlp_studio/version.py`; keep README and changelog aligned.
2. Run the lint, unit/GUI, coverage, and generated-audio checks in [development.md](development.md).
3. Run `.\.venv\Scripts\python.exe -B -m ytdlp_studio --version` and the legacy launcher with `--version`; both must agree.
4. Open `.\run-gui.ps1 -Foreground`; check previews, format/quality choices, search, artist release review, queue recovery, and optional mirroring using temporary/sample data.
5. Verify silent launching and first-time setup on a Windows test machine. Check incomplete-package and stale-environment feedback; do not recreate an environment unless necessary.
6. Build the source zip with `.\.venv\Scripts\python.exe -B tools/build_release.py --output dist --tag v0.2.2` (use the intended version tag).
7. Extract the zip into a clean directory; verify version, installer, and launcher there. Do not redistribute `.venv`, caches, user settings, music, or download archives.
8. Review `git status --short --untracked-files=all` and the complete staged diff. Run `git ls-files --cached --ignored --exclude-standard`; it should be empty. Ignore rules do not untrack files already in Git or recognize every possible secret filename. Do not stage cookies, private logs, credentials, settings, music or personal agent configuration.
9. Date the changelog section, commit the intended changes, create the matching `v*` tag, and push only when publishing is intended. Retain a private repository until you deliberately choose otherwise.
10. Review the GitHub workflow and uploaded assets after publication. The release waits for the reusable Windows test matrix; packaging must not run if checks fail.

The release workflow uses the same builder as local checks. Only runtime Python modules, launcher/setup scripts (including `Setup.cmd`), runtime requirement files, license, README, changelog, and Markdown documentation are packaged. A version/tag mismatch, missing setup entry point, missing user guide or missing runtime module fails packaging. The distribution is a source zip requiring Python and setup, not a standalone Windows executable or signed installer.

The builder also writes `yt-dlp-studio-v0.2.2.zip.sha256`. Both files are uploaded as release assets. Compare the digest with `Get-FileHash .\dist\yt-dlp-studio-v0.2.2.zip -Algorithm SHA256` after building or downloading. A checksum detects corruption; it is not an authenticity signature.

Pass `--notes dist/RELEASE_NOTES.md` to generate focused release notes from the current version's dated changelog section, with source-installation steps. Publishing rejects an undated, missing or empty section. The workflow uses these notes instead of posting the entire historical changelog, verifies the pushed tag exists and serializes runs for the same tag.

First-run acceptance on a clean Windows machine: extract the whole zip, double-click Setup.cmd, select core/Spotify support, check the setup result remains visible, and launch from a generated shortcut without a lingering console. Verify a permitted sample download with and without a temporary mirror. Dependency installation requires internet access; JavaScript runtimes remain an explicit system prerequisite.
