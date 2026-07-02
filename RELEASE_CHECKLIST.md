# Release Checklist

Use this for the first GitHub release and future tagged releases.

1. Run `.\install.ps1 -Recreate` if the local `.venv` is stale.
2. Run `.\.venv\Scripts\python.exe -m py_compile .\ytdlp_wrapper.py`.
3. Run `.\.venv\Scripts\python.exe .\ytdlp_wrapper.py --version`.
4. Open the GUI with `.\run-gui.ps1 -Foreground` and check the environment banner.
5. Run a duplicate dry scan on a known library folder.
6. Run `.\install.ps1 -Shortcuts` once on a Windows test machine and confirm the shortcuts launch silently.
7. Update `APP_VERSION` in `ytdlp_wrapper.py`.
8. Move the matching section in `CHANGELOG.md` from `Unreleased` to the release date.
9. Commit the changes.
10. Tag the commit, for example `git tag v0.1.0`.
11. Push the branch and tag to GitHub.

The GitHub workflow creates a zip release asset from the repository files and excludes local folders such as `.venv`, `.git`, `__pycache__`, and build output.
