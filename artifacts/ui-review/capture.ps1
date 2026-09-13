Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$env:LOCALAPPDATA = "E:\07_Program (プログラム)\FileScope\artifacts\ui-review\appdata"
$p = Start-Process -FilePath ".\.venv\Scripts\pythonw.exe" -ArgumentList "FileScope.py" -PassThru
Start-Sleep -Seconds 8
$p.Refresh()
$h = $p.MainWindowHandle
"handle: $h  exited: $($p.HasExited)"
if ($h -ne 0) {
  $rect = New-Object Win+RECT
  [void][Win]::GetWindowRect($h, [ref]$rect)
  $w = $rect.Right - $rect.Left; $hh = $rect.Bottom - $rect.Top
  "window: ${w}x${hh} at $($rect.Left),$($rect.Top)"
  $bmp = New-Object System.Drawing.Bitmap($w, $hh)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc()
  $ok = [Win]::PrintWindow($h, $hdc, 2)
  $g.ReleaseHdc($hdc)
  $bmp.Save("E:\07_Program (プログラム)\FileScope\artifacts\ui-review\window-1280x820.png", [System.Drawing.Imaging.ImageFormat]::Png)
  "printwindow: $ok"
  $bmp.Dispose(); $g.Dispose()
}
$p.Kill()
