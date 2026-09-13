# Portable / frozen-build smoke test (spec sections 11, 45, 46).
# ASCII-only. Unpacks the RC zip into a fresh TEMP folder, runs it with a
# different working directory and a redirected LOCALAPPDATA, and compares the
# frozen search result with the source build.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\portable_smoke.ps1

param(
    [string]$Zip = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not $Zip) {
    $Zip = (Get-ChildItem (Join-Path $root "dist") -Filter "FileScope-v*-win64.zip" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Zip -or -not (Test-Path $Zip)) { throw "RC zip not found; run scripts\build_rc_zip.ps1 first" }

$python = Join-Path $root ".venv\Scripts\python.exe"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("filescope-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$appData = Join-Path $work "appdata"
$unpack = Join-Path $work "portable"
$outDir = Join-Path $work "out"
New-Item -ItemType Directory -Force -Path $appData, $unpack, $outDir | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $unpack

$exe = Join-Path $unpack "FileScope.exe"
if (-not (Test-Path $exe)) { throw "FileScope.exe missing in the zip" }

$corpus = Join-Path $work "corpus"
& $python -c "import sys; sys.path.insert(0, r'$root'); from tools.make_corpus import build_corpus; build_corpus(r'$corpus', with_ocr_image=False)"

$env:LOCALAPPDATA = $appData
$env:TEMP = Join-Path $work "temp"
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

$results = @()
function Check($name, $ok, $detail) {
    $status = if ($ok) { "PASS" } else { "FAIL" }
    $script:results += [pscustomobject]@{ Check = $name; Status = $status; Detail = $detail }
    Write-Host ("{0}  {1}  {2}" -f $status, $name, $detail)
}

# 1. diagnostics, from a different working directory
Push-Location $outDir
$diag = Join-Path $outDir "diagnostics.txt"
& $exe --diagnostics --out $diag | Out-Null
$diagOk = (Test-Path $diag) -and ((Get-Content $diag -Raw) -match "FileScope")
Check "frozen --diagnostics" $diagOk $diag

# 2. self test
$selfTest = Join-Path $outDir "selftest.txt"
& $exe --self-test --out $selfTest | Out-Null
$selfText = if (Test-Path $selfTest) { Get-Content $selfTest -Raw } else { "" }
$summary = ($selfText -split "`n" | Where-Object { $_ -match "^summary:" } | Select-Object -First 1)
Check "frozen --self-test" ($summary -and $summary -notmatch "FAIL [1-9]") $summary

# 3. headless search, frozen
$frozenJson = Join-Path $outDir "frozen.json"
& $exe --search $corpus --query "AAA&BBB" --json --out $frozenJson | Out-Null
Check "frozen headless search" (Test-Path $frozenJson) $frozenJson

# 4. same search with the source build
$sourceJson = Join-Path $outDir "source.json"
$env:PYTHONPATH = $root
Push-Location $root
& $python -m filescope --search $corpus --query "AAA&BBB" --json --out $sourceJson | Out-Null
Pop-Location

function PathsOf($file) {
    if (-not (Test-Path $file)) { return @() }
    $data = Get-Content $file -Raw | ConvertFrom-Json
    return @($data.results | ForEach-Object { Split-Path $_.path -Leaf } | Sort-Object)
}
$frozenPaths = (PathsOf $frozenJson) -join ","
$sourcePaths = (PathsOf $sourceJson) -join ","
Check "source vs frozen result equivalence" ($frozenPaths -eq $sourcePaths) "frozen=[$frozenPaths] source=[$sourcePaths]"

# 5. settings and index were created inside the redirected LOCALAPPDATA
$settingsFile = Join-Path $appData "FileScope\settings.json"
Check "settings created in LOCALAPPDATA" (Test-Path $settingsFile) $settingsFile

# 6. GUI starts and stays alive
$gui = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
$alive = -not $gui.HasExited
if ($alive) { $gui.Kill() }
Check "frozen GUI starts" $alive "alive after 6s = $alive"

Pop-Location

$failed = @($results | Where-Object { $_.Status -eq "FAIL" }).Count
Write-Host ""
Write-Host ("portable smoke: {0} checks, {1} failed" -f $results.Count, $failed)
if (-not $Keep) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
else { Write-Host "workspace kept: $work" }
exit $failed
