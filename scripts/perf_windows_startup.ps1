# Measure window discovery from Process.Start. Use a disposable root in _scratch.
param(
    [Parameter(Mandatory=$true)][string]$Baseline,
    [Parameter(Mandatory=$true)][string]$Candidate,
    [Parameter(Mandatory=$true)][string]$Scratch,
    [int]$Trials = 3
)
$ErrorActionPreference = 'Stop'
$paths = @((Resolve-Path $Baseline).Path, (Resolve-Path $Candidate).Path)
$root = (New-Item -ItemType Directory -Path $Scratch -Force).FullName
foreach ($trial in 1..$Trials) {
    foreach ($i in 0,1) {
        $profile = (New-Item -ItemType Directory -Force -Path (Join-Path $root "$i-$trial")).FullName
        $info = [System.Diagnostics.ProcessStartInfo]::new($paths[$i])
        $info.WorkingDirectory = Split-Path $paths[$i]
        $info.UseShellExecute = $false
        foreach ($key in 'LOCALAPPDATA','TEMP','TMP') { $info.Environment[$key] = $profile }
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        $p = [System.Diagnostics.Process]::Start($info)
        try {
            do {
                Start-Sleep -Milliseconds 10
                $p.Refresh()
            } until ($p.MainWindowHandle -ne 0 -or $p.HasExited -or $timer.ElapsedMilliseconds -gt 10000)
            if ($p.MainWindowHandle -eq 0) { throw "FileScope did not create a discoverable top-level window" }
            $ms = $timer.Elapsed.TotalMilliseconds
            [pscustomobject]@{ trial=$trial; ui=@('tk','qt')[$i]; first_window_ms=[math]::Round($ms,1) } | ConvertTo-Json -Compress
            if (-not $p.CloseMainWindow() -or -not $p.WaitForExit(15000)) { throw "FileScope failed to exit normally" }
        }
        finally {
            if (-not $p.HasExited) { $p.Kill() }
            $p.Dispose()
        }
    }
}
