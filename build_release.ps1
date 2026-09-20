param(
    [string]$TesseractPath = "",
    [switch]$Console,
    [switch]$RunTests,
    [switch]$KeepWork
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Step([string]$Message) {
    Write-Host ""
    Write-Host ("=== " + $Message + " ===") -ForegroundColor Cyan
}

function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    throw "Python 3 was not found. Install 64-bit Python 3 first."
}

function Find-TesseractDir([string]$RequestedPath) {
    if ($RequestedPath) {
        $resolved = (Resolve-Path $RequestedPath -ErrorAction Stop).Path
        $item = Get-Item $resolved

        if ($item.PSIsContainer) {
            if (Test-Path (Join-Path $item.FullName "tesseract.exe")) {
                return $item.FullName
            }
        }
        elseif ($item.Name -ieq "tesseract.exe") {
            return $item.Directory.FullName
        }

        throw ("Invalid TesseractPath: " + $RequestedPath)
    }

    $default = "C:\Program Files\Tesseract-OCR"
    if (Test-Path (Join-Path $default "tesseract.exe")) {
        return $default
    }

    $cmd = Get-Command tesseract.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return (Split-Path -Parent $cmd.Source)
    }

    throw "Tesseract OCR was not found. Install it or use -TesseractPath C:\path\to\Tesseract-OCR"
}

# -----------------------------------------------------------------------------
# Project paths
# -----------------------------------------------------------------------------
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectRoot) {
    $ProjectRoot = (Get-Location).Path
}
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

$RequiredFiles = @(
    "launcher_gui.py",
    "game_automation.py",
    "boss_detector.py",
    "ocr_service.py",
    "requirements.txt",
    "boss\enter_boss.py",
    "boss\enter_boss.js"
)

foreach ($rel in $RequiredFiles) {
    $path = Join-Path $ProjectRoot $rel
    if (-not (Test-Path $path)) {
        throw ("Missing required file: " + $path)
    }
}

$WorkRoot = Join-Path $ProjectRoot ".release_work"
$BuildVenv = Join-Path $ProjectRoot ".venv-build"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$ReleaseApp = Join-Path $ReleaseRoot "TMLH_Bot"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ZipPath = Join-Path $ReleaseRoot ("TMLH_Bot_" + $Timestamp + ".zip")

# -----------------------------------------------------------------------------
# Clean previous temporary output
# -----------------------------------------------------------------------------
Step "Clean old build workspace"
foreach ($path in @($WorkRoot, $ReleaseApp)) {
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null

# -----------------------------------------------------------------------------
# Copy a clean source tree into .release_work
# -----------------------------------------------------------------------------
Step "Copy clean source"

$ExcludeDirs = @(
    ".git",
    ".github",
    ".venv",
    ".venv-build",
    ".release_work",
    "build",
    "dist",
    "release",
    "Game",
    "debug",
    "__pycache__",
    ".pytest_cache",
    ".pytest-tmp2",
    ".pytest-tmp5",
    ".pytest-tmp6",
    ".superpowers",
    "docs",
    "tests"
)

$ExcludeFiles = @(
    "profiles.json",
    "settings.json",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.temp",
    ".tmlh_profile_auth.bin"
)

$RoboArgs = @(
    $ProjectRoot,
    $WorkRoot,
    "/E",
    "/R:1",
    "/W:1",
    "/NFL",
    "/NDL",
    "/NJH",
    "/NJS",
    "/NP",
    "/XD"
)

foreach ($dir in $ExcludeDirs) {
    $RoboArgs += (Join-Path $ProjectRoot $dir)
}

$RoboArgs += "/XF"
$RoboArgs += $ExcludeFiles

& robocopy @RoboArgs | Out-Null
$RoboCode = $LASTEXITCODE
if ($RoboCode -ge 8) {
    throw ("Robocopy failed. Exit code=" + $RoboCode)
}

# -----------------------------------------------------------------------------
# Frozen entry point
# launcher_gui currently derives writable root from launcher_gui.__file__.
# Only patch the copied build at runtime; original source is untouched.
# -----------------------------------------------------------------------------
Step "Create frozen entry point"

$BuildEntry = @'
import sys
from pathlib import Path
import launcher_gui

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        launcher_gui.__file__ = str(Path(sys.executable).resolve())
    launcher_gui.LauncherApp().mainloop()
'@

Set-Content -Path (Join-Path $WorkRoot "build_entry.py") -Value $BuildEntry -Encoding UTF8

# -----------------------------------------------------------------------------
# Bundle Tesseract
# -----------------------------------------------------------------------------
Step "Bundle Tesseract"

$TessSource = Find-TesseractDir $TesseractPath
$VendorRoot = Join-Path $WorkRoot "vendor"
$TessDest = Join-Path $VendorRoot "Tesseract-OCR"
New-Item -ItemType Directory -Force -Path $VendorRoot | Out-Null
Copy-Item -Path $TessSource -Destination $TessDest -Recurse -Force

if (-not (Test-Path (Join-Path $TessDest "tesseract.exe"))) {
    throw ("Tesseract copy failed: " + $TessDest)
}

$RuntimeHook = @'
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    tess = root / "tesseract"
    if tess.is_dir():
        os.environ["PATH"] = str(tess) + os.pathsep + os.environ.get("PATH", "")
        os.environ.setdefault("TESSDATA_PREFIX", str(tess / "tessdata"))
'@

Set-Content -Path (Join-Path $WorkRoot "runtime_tesseract.py") -Value $RuntimeHook -Encoding UTF8

# -----------------------------------------------------------------------------
# Create PyInstaller spec without an expandable PowerShell here-string.
# This avoids quote/parser issues in Windows PowerShell 5.1.
# -----------------------------------------------------------------------------
Step "Create PyInstaller spec"

$SpecTemplate = @'
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
frida_datas, frida_binaries, frida_hiddenimports = collect_all("frida")
ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")

datas = [
    (str(root / "assets"), "assets"),
    (str(root / "boss" / "enter_boss.js"), "boss"),
    (str(root / "vendor" / "Tesseract-OCR"), "tesseract"),
]
datas += frida_datas
datas += ctk_datas

a = Analysis(
    [str(root / "build_entry.py")],
    pathex=[str(root)],
    binaries=frida_binaries + ctk_binaries,
    datas=datas,
    hiddenimports=frida_hiddenimports + ctk_hiddenimports + [
        "win32api",
        "win32con",
        "win32gui",
        "win32process",
        "win32crypt",
        "winreg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "runtime_tesseract.py")],
    excludes=["pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TMLH_Bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=__CONSOLE__,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TMLH_Bot",
)
'@

$ConsoleLiteral = "False"
if ($Console) {
    $ConsoleLiteral = "True"
}
$Spec = $SpecTemplate.Replace("__CONSOLE__", $ConsoleLiteral)
Set-Content -Path (Join-Path $WorkRoot "TMLH_Bot.spec") -Value $Spec -Encoding UTF8

# -----------------------------------------------------------------------------
# Build environment
# -----------------------------------------------------------------------------
Step "Prepare build environment"

$Launcher = Find-Python
$LauncherExe = $Launcher[0]
$LauncherExtra = @()
if ($Launcher.Count -gt 1) {
    $LauncherExtra = $Launcher[1..($Launcher.Count - 1)]
}

$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
if (-not (Test-Path $BuildPython)) {
    & $LauncherExe @LauncherExtra -m venv $BuildVenv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv-build"
    }
}

& $BuildPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed"
}

& $BuildPython -m pip install -r (Join-Path $WorkRoot "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed"
}

# -----------------------------------------------------------------------------
# Compile and optional tests
# -----------------------------------------------------------------------------
Step "Compile check"
& $BuildPython -m compileall -q $WorkRoot
if ($LASTEXITCODE -ne 0) {
    throw "compileall failed"
}

if ($RunTests) {
    $TestsPath = Join-Path $ProjectRoot "tests"
    if (Test-Path $TestsPath) {
        Step "Run tests"
        Push-Location $ProjectRoot
        try {
            & $BuildPython -m pytest -p no:cacheprovider tests -q
            if ($LASTEXITCODE -ne 0) {
                throw "Tests failed; release build stopped"
            }
        }
        finally {
            Pop-Location
        }
    }
}

# -----------------------------------------------------------------------------
# PyInstaller onedir build
# -----------------------------------------------------------------------------
Step "Build TMLH_Bot.exe"
Push-Location $WorkRoot
try {
    & $BuildPython -m PyInstaller --clean --noconfirm (Join-Path $WorkRoot "TMLH_Bot.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed"
    }
}
finally {
    Pop-Location
}

$BuiltDir = Join-Path $WorkRoot "dist\TMLH_Bot"
$BuiltExe = Join-Path $BuiltDir "TMLH_Bot.exe"
if (-not (Test-Path $BuiltExe)) {
    throw ("Build completed but EXE was not found: " + $BuiltExe)
}

# -----------------------------------------------------------------------------
# Clean portable release
# -----------------------------------------------------------------------------
Step "Create portable release"
if (Test-Path $ReleaseApp) {
    Remove-Item $ReleaseApp -Recurse -Force
}
Copy-Item -Path $BuiltDir -Destination $ReleaseApp -Recurse -Force

$DeployReadme = @'
TMLH BOT - PORTABLE BUILD
=========================

1. Extract the whole ZIP before running it.
2. Run TMLH_Bot.exe.
3. Select the original game folder that contains ThienMenhLacHong_Launcher.exe.
4. Create profiles and log in to each account on the destination Windows machine.

Do not transfer old profiles.json, Game\profiles, or .tmlh_profile_auth.bin to another Windows user/machine.
Profile auth is protected with Windows DPAPI.

Recommended install location:
  C:\TMLH_Bot
or
  D:\TMLH_Bot

Avoid Program Files because the app writes runtime data beside the EXE.
'@

Set-Content -Path (Join-Path $ReleaseApp "README_DEPLOY.txt") -Value $DeployReadme -Encoding UTF8

foreach ($runtimePath in @(
    (Join-Path $ReleaseApp "profiles.json"),
    (Join-Path $ReleaseApp "settings.json"),
    (Join-Path $ReleaseApp "Game"),
    (Join-Path $ReleaseApp "debug")
)) {
    if (Test-Path $runtimePath) {
        Remove-Item $runtimePath -Recurse -Force
    }
}

# -----------------------------------------------------------------------------
# ZIP
# -----------------------------------------------------------------------------
Step "Create ZIP"
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}
Compress-Archive -Path $ReleaseApp -DestinationPath $ZipPath -CompressionLevel Optimal

# -----------------------------------------------------------------------------
# Cleanup temp workspace
# -----------------------------------------------------------------------------
if (-not $KeepWork) {
    Step "Clean temporary workspace"
    Remove-Item $WorkRoot -Recurse -Force
}

Write-Host ""
Write-Host "BUILD SUCCESS" -ForegroundColor Green
Write-Host ("EXE folder : " + $ReleaseApp) -ForegroundColor Green
Write-Host ("ZIP deploy : " + $ZipPath) -ForegroundColor Green
Write-Host ""
Write-Host "Run:" -ForegroundColor Yellow
Write-Host ("  " + (Join-Path $ReleaseApp "TMLH_Bot.exe"))