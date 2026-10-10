param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$appScript = Join-Path $PSScriptRoot "ytdlp_wrapper.py"
$installer = Join-Path $PSScriptRoot "install.ps1"

function Write-LaunchStep {
    param([string]$Message)
    if ($Foreground) {
        Write-Host "==> $Message" -ForegroundColor Cyan
    }
}

function Show-LaunchError {
    param([string]$Message)
    if ($Foreground) {
        Write-Host ""
        Write-Host $Message -ForegroundColor Red
    } else {
        try {
            $shell = New-Object -ComObject WScript.Shell
            $shell.Popup($Message, 0, "yt-dlp Studio", 16) | Out-Null
        } catch {
            Write-Host $Message
        }
    }
}

if (-not (Test-Path $appScript) -or -not (Test-Path (Join-Path $PSScriptRoot "ytdlp_studio\gui.py"))) {
    Show-LaunchError "The app files are incomplete.`n`nExtract the whole release zip, including the ytdlp_studio folder, into one directory.`n`nProject:`n$PSScriptRoot"
    exit 1
}

if (-not (Test-Path $venvPython)) {
    if (-not (Test-Path $installer)) {
        Show-LaunchError "yt-dlp Studio is not installed yet, and install.ps1 was not found.`n`nExpected path:`n$installer"
        exit 1
    }

    Write-LaunchStep "Project virtual environment not found. Running setup first."
    & $installer -NoPause
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Show-LaunchError "Setup did not finish successfully. Double-click Setup.cmd in the app folder and review the visible setup result.`n`nSee docs\troubleshooting.md for repair steps."
        exit 1
    }
}

$appArgument = "`"$appScript`""

if ($Foreground) {
    Write-LaunchStep "Starting yt-dlp Studio in foreground mode"
    & $venvPython $appScript
    exit $LASTEXITCODE
} elseif (Test-Path $venvPythonw) {
    Write-LaunchStep "Starting yt-dlp Studio"
    $process = Start-Process -FilePath $venvPythonw -ArgumentList $appArgument -WindowStyle Hidden -PassThru
} else {
    Write-LaunchStep "pythonw.exe was not found; using python.exe hidden"
    $process = Start-Process -FilePath $venvPython -ArgumentList $appArgument -WindowStyle Hidden -PassThru
}

if (-not $Foreground) {
    Start-Sleep -Milliseconds 1000
    $process.Refresh()
    if ($process.HasExited -and $process.ExitCode -ne 0) {
        Show-LaunchError "The app stopped during startup.`n`nTry .\run-gui.ps1 -Foreground for details, or inspect %APPDATA%\ytdlp-studio\startup-error.log.`n`nRun .\install.ps1 to repair missing dependencies."
        exit 1
    }
}
