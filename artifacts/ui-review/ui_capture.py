"""Capture real FileScope windows for the UI audit.

Runs the GUI in-process against a generated corpus (a temp LOCALAPPDATA is used
so the real settings/index are untouched), drives a search, and writes PNGs to
artifacts/ui-review/. Used by the release UI audit; not part of the product.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from filescope import paths  # noqa: E402
from filescope.config import Settings  # noqa: E402
from filescope.ui.main_window import MainWindow  # noqa: E402
from tools.make_corpus import build_corpus  # noqa: E402

OUT = Path(__file__).resolve().parent


def capture(root: tk.Tk, name: str) -> str:
    """PrintWindow the top-level frame; falls back to a screen copy."""
    import ctypes.wintypes as wintypes

    root.update_idletasks()
    root.update()
    time.sleep(0.4)
    hwnd = int(root.wm_frame(), 16)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    import tkinter  # noqa: F401
    from PIL import Image

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    # PW_RENDERFULLCONTENT (0x2) captures DWM-composited windows.
    user32.PrintWindow(hwnd, memory_dc, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # top-down
    header.biPlanes = 1
    header.biBitCount = 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    pixels = image.convert("RGB")
    path = OUT / name
    pixels.save(path)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return str(path)


def main() -> int:
    workspace = OUT / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(workspace)
    corpus = workspace / "corpus"
    root_dir = workspace / "corpus"
    if not (root_dir / "report.xlsx").exists():
        build_corpus(root_dir, with_ocr_image=False)

    settings = Settings()
    settings.index_enabled = False
    window_root = tk.Tk()
    window_root.title("FileScope 5.0.0-rc1")
    window_root.geometry("1280x820+40+20")
    window = MainWindow(window_root, settings=settings)
    window.root_var.set(str(corpus))
    window.query_var.set("AAA&BBB")
    window.start_search()

    deadline = time.time() + 60
    while time.time() < deadline:
        window_root.update()
        time.sleep(0.05)
        if window.session is not None and window.session.finished:
            break
    for _ in range(20):
        window_root.update()
        time.sleep(0.05)

    shots: list[str] = []
    shots.append(capture(window_root, "01-results-1280x820.png"))
    rows = window.tree.get_children()
    if rows:
        window.tree.selection_set(rows[0])
        for _ in range(30):
            window_root.update()
            time.sleep(0.05)
        shots.append(capture(window_root, "02-preview-selected.png"))
    window_root.geometry("1920x1080+0+0")
    for _ in range(20):
        window_root.update()
        time.sleep(0.05)
    shots.append(capture(window_root, "03-maximised-1920x1080.png"))
    window_root.geometry("1024x640+20+20")
    for _ in range(20):
        window_root.update()
        time.sleep(0.05)
    shots.append(capture(window_root, "04-small-1024x640.png"))

    # Condition builder dialog
    from filescope.ui import dialogs

    dialog = dialogs.ConditionBuilderDialog(window_root, initial="AAA&BBB")
    dialog.window.update()
    dialog.all_of.insert("1.0", "\nCCC")
    dialog._preview()
    time.sleep(0.3)
    shots.append(capture(dialog.window, "05-condition-builder.png"))
    dialog._cancel()
    window_root.update()

    diagnostics = dialogs.DiagnosticsDialog(window_root)
    diagnostics.window.update()
    time.sleep(0.4)
    shots.append(capture(diagnostics.window, "06-diagnostics.png"))
    diagnostics._cancel()

    window.on_close()
    for shot in shots:
        print("captured:", shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
