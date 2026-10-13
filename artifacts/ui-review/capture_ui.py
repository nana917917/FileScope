"""Capture FileScope windows for UI comparison (before/after v5.1 work).

Runs the GUI in-process against a generated corpus with a temporary
LOCALAPPDATA, drives a search, and writes PNGs plus a timing file.

    python artifacts/ui-review/capture_ui.py --out artifacts/ui-review-v51/before --prefix before
    python artifacts/ui-review/capture_ui.py --out artifacts/ui-review-v51/after  --prefix after
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from filescope.config import Settings  # noqa: E402
from filescope.ui.main_window import MainWindow  # noqa: E402
from tools.make_corpus import build_corpus  # noqa: E402

VIEW_SIZES = (
    ("1024x768", "1024x768+20+20"),
    ("1280x720", "1280x720+20+20"),
    ("1920x1080", "1920x1080+0+0"),
)


def capture(widget: tk.Misc, path: Path) -> Path:
    """PrintWindow the top-level frame of ``widget``."""
    import ctypes.wintypes as wintypes

    from PIL import Image

    widget.update_idletasks()
    widget.update()
    time.sleep(0.35)
    handle = int(widget.wm_frame(), 16)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    window_dc = user32.GetWindowDC(handle)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    user32.PrintWindow(handle, memory_dc, 2)  # PW_RENDERFULLCONTENT

    class Header(ctypes.Structure):
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

    header = Header()
    header.biSize = ctypes.sizeof(Header)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(handle, window_dc)
    return path


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.03)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="output directory for the PNGs")
    parser.add_argument("--prefix", default="shot", help="file name prefix")
    parser.add_argument("--workspace", default="", help="workspace dir for LOCALAPPDATA/corpus")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    workspace = Path(args.workspace) if args.workspace else out / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(workspace / "appdata")

    corpus = workspace / "corpus"
    if not (corpus / "report.xlsx").exists():
        build_corpus(corpus, with_ocr_image=True)

    settings = Settings()
    settings.index_enabled = False
    root = tk.Tk()
    root.title("FileScope")
    root.geometry("1280x720+20+20")
    window = MainWindow(root, settings=settings)
    window.root_var.set(str(corpus))
    window.query_var.set("AAA&BBB")

    shots: list[str] = []
    shots.append(str(capture(root, out / f"{args.prefix}-01-before-search-1280x720.png")))

    started = time.perf_counter()
    window.start_search()
    # mid-search shot (enumeration/progress visible)
    pump(root, 0.35)
    shots.append(str(capture(root, out / f"{args.prefix}-02-searching.png")))
    deadline = time.time() + 120
    while time.time() < deadline:
        root.update()
        if window.session is not None and window.session.finished:
            break
        time.sleep(0.03)
    elapsed = time.perf_counter() - started
    pump(root, 0.6)
    shots.append(str(capture(root, out / f"{args.prefix}-03-results.png")))

    rows = window.tree.get_children()
    if rows:
        window.tree.selection_set(rows[0])
        pump(root, 1.4)
        shots.append(str(capture(root, out / f"{args.prefix}-04-preview.png")))

    # PDF page image view (v5.1): pick the PDF row and switch to 画像.
    pdf_row = None
    for row_id in window.tree.get_children():
        row = window.model.row_at(int(row_id))
        if row is not None and row.result.file_kind.value == "PDF":
            pdf_row = row_id
            break
    if pdf_row is not None:
        window.tree.selection_set(pdf_row)
        pump(root, 1.6)
        with contextlib.suppress(Exception):
            window.preview.set_view("image")
            pump(root, 2.5)
        shots.append(str(capture(root, out / f"{args.prefix}-04b-pdf-page.png")))
        with contextlib.suppress(Exception):
            window.preview.change_zoom(1)
            pump(root, 2.0)
        shots.append(str(capture(root, out / f"{args.prefix}-04c-pdf-zoom.png")))
        with contextlib.suppress(Exception):
            window.preview.set_view("text")
            pump(root, 0.4)

    for label, geometry in VIEW_SIZES:
        root.geometry(geometry)
        pump(root, 0.7)
        shots.append(str(capture(root, out / f"{args.prefix}-05-size-{label}.png")))

    root.state("zoomed")
    pump(root, 0.8)
    shots.append(str(capture(root, out / f"{args.prefix}-06-maximised.png")))
    root.state("normal")
    root.geometry("1280x720+20+20")
    pump(root, 0.5)

    from filescope.ui import dialogs

    builder = dialogs.ConditionBuilderDialog(root, initial="AAA&BBB")
    pump(root, 0.4)
    shots.append(str(capture(builder.window, out / f"{args.prefix}-07-condition-builder.png")))
    builder._cancel()
    pump(root, 0.2)

    advanced = dialogs.AdvancedSettingsDialog(root, state=window.state, settings=settings)
    pump(root, 0.4)
    shots.append(str(capture(advanced.window, out / f"{args.prefix}-08-advanced-settings.png")))
    advanced._cancel()
    pump(root, 0.2)

    diagnostics = dialogs.DiagnosticsDialog(root)
    pump(root, 0.4)
    shots.append(str(capture(diagnostics.window, out / f"{args.prefix}-09-diagnostics.png")))
    diagnostics._cancel()

    metrics = {
        "search_seconds": round(elapsed, 3),
        "results": len(window.model.all_rows),
        "coverage": window._coverage_text.get(),
        "status": window._status_text.get(),
        "fonts": _font_report(root) if root.winfo_exists() else {},
        "screenshots": shots,
    }
    (out / f"{args.prefix}-metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    window.on_close()
    return 0


def _font_report(root: tk.Tk) -> dict:
    import tkinter.font as tkfont

    names = set(tkfont.names())
    return {
        name: {
            "family": str(tkfont.nametofont(name).actual("family")),
            "size": tkfont.nametofont(name).actual("size"),
            "weight": str(tkfont.nametofont(name).actual("weight")),
        }
        for name in sorted(n for n in names if n.startswith(("Tk", "TLabel", "TButton", "TkDefault")))
    }


if __name__ == "__main__":
    raise SystemExit(main())
