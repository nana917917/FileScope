# Build the portable onedir bundle and package it as a release zip.
# ASCII-only on purpose (Japanese text in .ps1 has caused encoding damage).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_release_zip.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_release_zip.ps1 -SkipBuild -SkipInstall -Measure

param(
    [switch]$SkipBuild,
    [switch]$SkipInstall,
    [switch]$Measure,
    [string]$Version = "5.1.1",
    [string]$OutputDirectory = "dist",
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
    if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }
}

$outputDir = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $root $OutputDirectory
}
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

if (-not $SkipBuild) {
    $workDir = Join-Path $root ("_scratch\build-" + $Version)
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null
    Write-Host "building onedir bundle into $outputDir"
    & $python -m PyInstaller FileScope.spec --noconfirm --clean --log-level WARN --distpath $outputDir --workpath $workDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

$distDir = Join-Path $outputDir "FileScope"
$exe = Join-Path $distDir "FileScope.exe"
if (-not (Test-Path $exe)) { throw "build failed: $exe not found" }

$qtVersion = & $python -c "from PySide6 import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0 -or $qtVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Could not determine Qt version" }
$qtNotices = Join-Path $distDir "licenses\qt"
New-Item -ItemType Directory -Force -Path $qtNotices | Out-Null
foreach ($license in @("LGPL-3.0-only.txt", "GPL-3.0-only.txt")) {
    Invoke-WebRequest "https://raw.githubusercontent.com/qt/qtbase/v$qtVersion/LICENSES/$license" -OutFile (Join-Path $qtNotices $license)
}
@"
Qt and Qt for Python $qtVersion are distributed as separate, replaceable dynamic libraries.
Copyright The Qt Company Ltd. and contributors. Distributed under LGPL-3.0.
Upstream corresponding source (unmodified):
https://download.qt.io/official_releases/qt/$($qtVersion.Substring(0, $qtVersion.LastIndexOf('.')))/$qtVersion/submodules/
https://github.com/pyside/pyside-setup/tree/v$qtVersion
Library notices:
https://doc.qt.io/qt-6/licenses-used-in-qt.html
https://doc.qt.io/qtforpython-6/licenses.html
Users may replace compatible library binaries in _internal and may reverse engineer this
application for debugging modifications to the LGPL libraries. These rights are not
restricted by the FileScope application license.
"@ | Set-Content -LiteralPath (Join-Path $qtNotices "NOTICE.txt") -Encoding utf8

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
$zipPath = Join-Path $outputDir $zipName
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
