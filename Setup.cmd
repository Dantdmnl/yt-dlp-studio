@echo off
setlocal
title yt-dlp Studio setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Interactive -Shortcuts -NoPause
set "setupExitCode=%errorlevel%"
if not "%setupExitCode%"=="0" (
    echo.
    echo Setup failed. The details are shown above.
)
echo.
pause
exit /b %setupExitCode%
