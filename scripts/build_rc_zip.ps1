# Build the portable onedir bundle and package it as the RC zip.
# ASCII-only on purpose (Japanese text in .ps1 has caused encoding damage).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_rc_zip.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_rc_zip.ps1 -SkipBuild -SkipInstall -Measure

param(
    [switch]$SkipBuild,
    [switch]$SkipInstall,
    [switch]$Measure,
    [string]$Version = "5.0.0-rc1",
    [string]$Label = "win64"
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

if (-not $SkipBuild) {
    Write-Host "building onedir bundle"
    & $python -m PyInstaller FileScope.spec --noconfirm --clean --log-level WARN
}

$distDir = Join-Path $root "dist\FileScope"
$exe = Join-Path $distDir "FileScope.exe"
if (-not (Test-Path $exe)) { throw "build failed: $exe not found" }

# Documentation and version metadata travel next to the exe as well, so the zip
# stays understandable when it is unpacked somewhere else.
foreach ($file in @("README.md", "QUICKSTART.md", "THIRD_PARTY_NOTICES.md", "LICENSE.txt", "VERSION.txt", "ARCHITECTURE.md")) {
    $source = Join-Path $root $file
    if (Test-Path $source) { Copy-Item -LiteralPath $source -Destination $distDir -Force }
}
$docsSource = Join-Path $root "docs"
if (Test-Path $docsSource) {
    $docsTarget = Join-Path $distDir "docs"
    New-Item -ItemType Directory -Force -Path $docsTarget | Out-Null
    Copy-Item -Path (Join-Path $docsSource "*") -Destination $docsTarget -Recurse -Force
}

$zipName = "FileScope-v$Version-$Label.zip"
$zipPath = Join-Path $root "dist\$zipName"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Write-Host "creating $zipName"
Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

$bundleSize = (Get-ChildItem $distDir -Recurse | Measure-Object -Property Length -Sum).Sum
$zipSize = (Get-Item $zipPath).Length
Write-Host ("bundle : {0:N1} MB" -f ($bundleSize / 1MB))
Write-Host ("zip    : {0:N1} MB -> {1}" -f ($zipSize / 1MB), $zipPath)

if ($Measure) {
    Write-Host "measuring startup (source vs bundle)"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $python -m filescope --diagnostics | Out-Null
    $sw.Stop()
    Write-Host ("source : {0:N2}s" -f $sw.Elapsed.TotalSeconds)

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $exe --diagnostics | Out-Null
    $sw.Stop()
    Write-Host ("bundle : {0:N2}s" -f $sw.Elapsed.TotalSeconds)
}

Write-Host "done: $zipPath"
