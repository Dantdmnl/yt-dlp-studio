param(
    [switch]$Recreate,
    [switch]$Shortcuts,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$venv = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$launcher = Join-Path $PSScriptRoot "yt-dlp-studio.vbs"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Detail {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor DarkGray
}

function Write-Done {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Stop-WithHelp {
    param([string]$Message)
    Write-Host ""
    Write-Host "Setup could not continue." -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    Write-Host ""
    Write-Host "If this keeps happening, run this command from the project folder:" -ForegroundColor Yellow
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Recreate" -ForegroundColor Yellow
    Wait-BeforeExit
    exit 1
}

function Wait-BeforeExit {
    if ($NoPause) {
        return
    }
    Write-Host ""
    Write-Host "Press Enter to close this window..." -ForegroundColor DarkGray
    try {
        [void](Read-Host)
    } catch {
    }
}

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
        Write-Detail "Checking Python near yt-dlp: $fromYtdlp"
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
        Write-Detail "Checking Python from PATH: $($pythonCommand.Source)"
        $candidates.Add($pythonCommand.Source)
    }

    foreach ($candidate in $candidates) {
        if (Test-Python $candidate) {
            return $candidate
        }
    }

    return $null
}

function New-AppShortcut {
    param(
        [string]$Path,
        [string]$Target
    )

    $folder = Split-Path $Path -Parent
    if (-not (Test-Path $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = "Launch yt-dlp Studio"
    $shortcut.Save()
}

try {
    Write-Host ""
    Write-Host "yt-dlp Studio setup" -ForegroundColor White
    Write-Host "Project: $PSScriptRoot" -ForegroundColor DarkGray

    if (-not (Test-Path $requirements)) {
        Stop-WithHelp "requirements.txt was not found at $requirements"
    }

    Write-Step "Checking virtual environment"
    $venvHealthy = Test-Venv
    if ((Test-Path $venv) -and ($Recreate -or -not $venvHealthy)) {
        if ($Recreate) {
            Write-Detail "Recreate was requested."
        } else {
            Write-Detail "Existing .venv is missing or points at a Python install that no longer exists."
        }
        Write-Detail "Removing $venv"
        Remove-Item -LiteralPath $venv -Recurse -Force
        $venvHealthy = $false
    }

    if (-not (Test-Path $venvPython)) {
        Write-Step "Finding Python 3.10+"
        $python = Find-BasePython
        if (-not $python) {
            Stop-WithHelp "Python 3.10+ was not found. Install Python from https://www.python.org/downloads/windows/ and run setup again."
        }

        Write-Done "Using $python"
        Write-Step "Creating project virtual environment"
        & $python -m venv $venv
        if ($LASTEXITCODE -ne 0) {
            Stop-WithHelp "Python failed to create the virtual environment."
        }
        Write-Done "Created $venv"
    } else {
        Write-Done "Using existing .venv"
    }

    Write-Step "Validating virtual environment"
    if (-not (Test-Venv)) {
        Stop-WithHelp "The project virtual environment could not be created with a working Python executable."
    }
    Write-Done "Python executable is $venvPython"

    Write-Step "Installing Python packages"
    Write-Detail "Upgrading pip"
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Stop-WithHelp "pip upgrade failed."
    }

    Write-Detail "Installing requirements from $requirements"
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Stop-WithHelp "Dependency installation failed."
    }
    Write-Done "Dependencies installed"

    if ($Shortcuts) {
        Write-Step "Creating Windows shortcuts"
        if (-not (Test-Path $launcher)) {
            Stop-WithHelp "Launcher not found: $launcher"
        }

        $desktop = [Environment]::GetFolderPath("Desktop")
        $startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "yt-dlp Studio"
        New-AppShortcut -Path (Join-Path $desktop "yt-dlp Studio.lnk") -Target $launcher
        New-AppShortcut -Path (Join-Path $startMenu "yt-dlp Studio.lnk") -Target $launcher
        Write-Done "Shortcuts created on Desktop and in the Start Menu"
    }

    Write-Host ""
    Write-Host "Setup complete." -ForegroundColor Green
    Write-Host "Start the app with:" -ForegroundColor White
    Write-Host "  .\run-gui.ps1" -ForegroundColor White
    if (-not $Shortcuts) {
        Write-Host ""
        Write-Host "Optional: run .\install.ps1 -Shortcuts to add Desktop and Start Menu shortcuts." -ForegroundColor DarkGray
    }
    Wait-BeforeExit
} catch {
    Stop-WithHelp $_.Exception.Message
}
