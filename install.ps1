param(
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$venv = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

function Test-Python {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path $Path)) {
        return $false
    }

    & $Path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-Venv {
    if (-not (Test-Path $venvPython)) {
        return $false
    }

    $config = Join-Path $venv "pyvenv.cfg"
    if (Test-Path $config) {
        $executableLine = Get-Content $config | Where-Object { $_ -match "^executable\s*=" } | Select-Object -First 1
        if ($executableLine) {
            $basePython = ($executableLine -split "=", 2)[1].Trim()
            if (-not (Test-Path $basePython)) {
                return $false
            }
        }
    }

    return Test-Python $venvPython
}

function Find-BasePython {
    $candidates = New-Object System.Collections.Generic.List[string]

    $ytdlpCommand = Get-Command yt-dlp -ErrorAction SilentlyContinue
    if ($ytdlpCommand) {
        $fromYtdlp = Join-Path (Split-Path (Split-Path $ytdlpCommand.Source -Parent) -Parent) "python.exe"
        $candidates.Add($fromYtdlp)
    }

    $localPrograms = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $localPrograms) {
        Get-ChildItem -Path $localPrograms -Directory -Filter "Python*" |
            Sort-Object Name -Descending |
            ForEach-Object { $candidates.Add((Join-Path $_.FullName "python.exe")) }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates.Add($pythonCommand.Source)
    }

    foreach ($candidate in $candidates) {
        if (Test-Python $candidate) {
            return $candidate
        }
    }

    return $null
}

if ((Test-Path $venv) -and ($Recreate -or -not (Test-Venv))) {
    Write-Host "Existing .venv is broken. Recreating it..."
    Remove-Item -LiteralPath $venv -Recurse -Force
}

if (-not (Test-Path $venvPython)) {
    $python = Find-BasePython
    if (-not $python) {
        Write-Error "Python 3.10+ was not found. Install Python 3.10+ first."
    }

    & $python -m venv $venv
}

if (-not (Test-Venv)) {
    Write-Error "The project virtual environment could not be created with a working Python executable."
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r "$PSScriptRoot\requirements.txt"

Write-Host "Installed into $venv"
