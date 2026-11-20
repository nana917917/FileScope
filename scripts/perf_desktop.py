"""Compare source GUI startup and synthetic search with an isolated profile.

Launch from the repository root using the same Python for each run. Specify
--source to import a baseline checkout and --ui tk to use its original window.
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--ui", choices=("tk", "qt"), required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--corpus", required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.source)
    profile = Path(args.profile).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    for key in ("LOCALAPPDATA", "TEMP", "TMP"):
        os.environ[key] = str(profile)

    from filescope.config import Settings

    settings = Settings.load()
    settings.index_enabled = False
    settings.autosave_results = False
    settings.defaults.pdf_ocr_mode = "off"
    corpus = str(Path(args.corpus).resolve())
    drains = []
    if args.ui == "qt":
        from PySide6.QtCore import QTimer
        from filescope.ui.qt_window import Window, create_application

        imported = time.perf_counter()
        app = create_application()
        window = Window(settings)
        window.show()
        app.processEvents()
        window.root_input.setEditText(corpus)
        window.query.setText("評価")

        def schedule(fn):
            QTimer.singleShot(40, fn)

        def finished():
            return window.summary

        def seek(fraction):
            index = window.proxy.index(int(fraction * (window.proxy.rowCount() - 1)), 1)
            window.table.scrollTo(index)

        def close():
            window.close()

        update = app.processEvents
        window.timer.timeout.disconnect(window._drain)

        def timed_drain():
            start = time.perf_counter()
            window._drain()
            drains.append((time.perf_counter() - start) * 1000)

        window.timer.timeout.connect(timed_drain)
    else:
        import tkinter as tk

        from filescope.platform.tempfiles import purge_stale
        from filescope.ui.main_window import MainWindow

        imported = time.perf_counter()
        purge_stale()
        root = tk.Tk()
        root.geometry("1260x790")
        window = MainWindow(root, settings=settings)
        root.update()
        window.root_var.set(corpus)
        window.query_var.set("評価")

        def schedule(fn):
            root.after(40, fn)

        def finished():
            return window.session.summary if window.session and not window.session.events.qsize() else None

        def seek(fraction):
            window._on_scroll("moveto", str(fraction))

        close = window.on_close
        update = root.update

    ready = time.perf_counter()
    ticks = []
    last = ready

    def tick():
        nonlocal last
        now = time.perf_counter()
        ticks.append(now - last)
        last = now
        schedule(tick)

    schedule(tick)
    cpu = time.process_time()
    window.start_search()
    call = time.perf_counter()
    deadline = call + 45
    while time.perf_counter() < deadline:
        update()
        if summary := finished():
            break
        time.sleep(0.002)
    else:
        raise RuntimeError("search timed out")
    cpu = time.process_time() - cpu
    from benchmark import ProcessMemory

    seek_ms = []
    for fraction in (0, 0.25, 0.5, 0.75, 1, 0.1):
        stamp = time.perf_counter()
        seek(fraction)
        update()
        seek_ms.append((time.perf_counter() - stamp) * 1000)
    report = {
        "ui": args.ui, "imports_ms": (imported-started)*1000, "ready_ms": (ready-started)*1000,
        "search_callback_ms": (call-ready)*1000, "search_seconds": summary.elapsed, "hits": len(summary.results),
        "search_cpu_seconds": cpu, "working_set_mib": ProcessMemory.peak_bytes()/2**20,
        "tick_p95_ms": sorted(ticks)[int(0.95*(len(ticks)-1))]*1000,
        "tick_max_ms": max(ticks)*1000, "seek_median_ms": statistics.median(seek_ms),
        "drain_max_ms": max(drains, default=0),
    }
    close()
    print(json.dumps(report))


if __name__ == "__main__":
    main()
