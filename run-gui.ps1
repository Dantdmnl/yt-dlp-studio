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

if (-not (Test-Path $appScript)) {
    Show-LaunchError "yt-dlp Studio could not start because ytdlp_wrapper.py was not found.`n`nExpected path:`n$appScript"
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
        Show-LaunchError "Setup did not finish successfully. Open PowerShell in the app folder and run:`n`n.\install.ps1 -Recreate"
        exit 1
    }
}

$appArgument = "`"$appScript`""

if ($Foreground) {
    Write-LaunchStep "Starting yt-dlp Studio in foreground mode"
    & $venvPython $appScript
} elseif (Test-Path $venvPythonw) {
    Write-LaunchStep "Starting yt-dlp Studio"
    Start-Process -FilePath $venvPythonw -ArgumentList $appArgument -WindowStyle Hidden
} else {
    Write-LaunchStep "pythonw.exe was not found; using python.exe hidden"
    Start-Process -FilePath $venvPython -ArgumentList $appArgument -WindowStyle Hidden
}
