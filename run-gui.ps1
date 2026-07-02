param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$appScript = Join-Path $PSScriptRoot "ytdlp_wrapper.py"

if (-not (Test-Path $venvPython)) {
    Write-Host "Project virtual environment not found. Creating it now..."
    & "$PSScriptRoot\install.ps1"
}

$appArgument = "`"$appScript`""

if ($Foreground) {
    & $venvPython $appScript
} elseif (Test-Path $venvPythonw) {
    Start-Process -FilePath $venvPythonw -ArgumentList $appArgument -WindowStyle Hidden
} else {
    Start-Process -FilePath $venvPython -ArgumentList $appArgument -WindowStyle Hidden
}
