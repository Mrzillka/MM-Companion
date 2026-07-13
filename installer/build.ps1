<#
.SYNOPSIS
    Build the shareable MM-Companion Windows installer.

.DESCRIPTION
    One command that:
      1. reads the single-sourced version from mm_companion.__version__,
      2. freezes the app with PyInstaller twice (one-folder + one-file portable),
      3. wraps both into installer\output\MM-Companion-Setup-<version>.exe with
         Inno Setup.

    Run from the repo root inside the project's virtualenv:
        pwsh installer\build.ps1
    Requires: pip install pyinstaller ; and Inno Setup 6 (ISCC.exe) installed.
#>
[CmdletBinding()]
param(
    # Path to the Inno Setup compiler; auto-detected if omitted.
    [string]$Iscc = "",
    # Python interpreter to freeze with; defaults to whatever "python" resolves to.
    # The build-installer skill passes its own interpreter so the venv always matches.
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

# Repo root = parent of this script's folder.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> Reading version from mm_companion.__version__" -ForegroundColor Cyan
$Version = & $PythonExe -c "import mm_companion, sys; sys.stdout.write(mm_companion.__version__)"
if (-not $Version) { throw "Could not read mm_companion.__version__ (is the package importable?)" }
Write-Host "    version = $Version"

Write-Host "==> Cleaning previous build artifacts" -ForegroundColor Cyan
foreach ($d in @("build", "dist", "installer\output")) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}

$Spec = "installer\mm_companion.spec"

Write-Host "==> PyInstaller: one-folder build" -ForegroundColor Cyan
Remove-Item Env:MMC_ONEFILE -ErrorAction SilentlyContinue
& $PythonExe -m PyInstaller --noconfirm --clean $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (one-folder) failed." }

Write-Host "==> PyInstaller: one-file portable build" -ForegroundColor Cyan
$env:MMC_ONEFILE = "1"
& $PythonExe -m PyInstaller --noconfirm $Spec
Remove-Item Env:MMC_ONEFILE
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (one-file) failed." }

if (-not (Test-Path "dist\MM-Companion\MM-Companion.exe")) {
    throw "Expected dist\MM-Companion\MM-Companion.exe was not produced."
}
if (-not (Test-Path "dist\MM-Companion-portable.exe")) {
    throw "Expected dist\MM-Companion-portable.exe was not produced."
}

# Locate the Inno Setup compiler.
if (-not $Iscc) {
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $Iscc = $found.Source }
    else { $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1 }
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php or pass -Iscc <path>."
}

Write-Host "==> Inno Setup: building installer" -ForegroundColor Cyan
& $Iscc "/DAppVersion=$Version" "installer\mm_companion.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$Output = "installer\output\MM-Companion-Setup-$Version.exe"
if (Test-Path $Output) {
    Write-Host "==> Done: $Output" -ForegroundColor Green
} else {
    throw "Inno Setup reported success but $Output is missing."
}
