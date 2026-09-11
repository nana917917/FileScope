# Build a portable Windows bundle (onedir) with PyInstaller.
# ASCII-only on purpose: Japanese text in .ps1/.cmd has caused encoding damage.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Measure

param(
    [switch]$Measure,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "creating virtual environment"
    python -m venv .venv
}

if (-not $SkipInstall) {
    Write-Host "installing dependencies"
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r requirements.txt pyinstaller
}

Write-Host "building with PyInstaller (onedir)"
& $python -m PyInstaller FileScope.spec --noconfirm --clean

$dist = Join-Path $root "dist\FileScope\FileScope.exe"
if (-not (Test-Path $dist)) {
    throw "build failed: $dist not found"
}

$size = (Get-ChildItem (Join-Path $root "dist\FileScope") -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host ("bundle: {0} ({1:N1} MB)" -f $dist, ($size / 1MB))

if ($Measure) {
    Write-Host "measuring startup (source vs bundle)"
    $sourceArgs = @("-m", "filescope", "--diagnostics")
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $python @sourceArgs | Out-Null
    $sw.Stop()
    Write-Host ("source  : {0:N2}s" -f $sw.Elapsed.TotalSeconds)

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $dist --diagnostics | Out-Null
    $sw.Stop()
    Write-Host ("bundle  : {0:N2}s" -f $sw.Elapsed.TotalSeconds)
}

Write-Host "done: dist\FileScope\FileScope.exe"
