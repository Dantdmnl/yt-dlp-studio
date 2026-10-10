"""Compatibility entry point for media-tools setup."""

from ytdlp_studio.media_tools import find_installed_tools, install_tools, main, verify_tools

__all__ = ["find_installed_tools", "install_tools", "main", "verify_tools"]

if __name__ == "__main__":
    raise SystemExit(main())
