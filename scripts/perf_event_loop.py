"""Measure real desktop event loops on an isolated synthetic corpus."""

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path


def main():
    start = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--ui", choices=("qt", "tk"), required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--query", default="評価")
    parser.add_argument("--sort", action="store_true")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--light", action="store_true")
    parser.add_argument("--cancel-ms", type=int)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source).resolve()))
    profile = Path(args.profile).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    for key in ("LOCALAPPDATA", "TEMP", "TMP"):
        os.environ[key] = str(profile)

    from filescope.config import Settings

    settings = Settings.load()
    settings.index_enabled = args.index
    settings.autosave_results = False
    settings.defaults.pdf_ocr_mode = "off"
    loaded = time.perf_counter()
    costs = defaultdict(list)

    def instrument(cls, key):
        original = getattr(cls, key)

        def wrapped(*a, **kw):
            stamp = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                costs[key].append((time.perf_counter() - stamp) * 1000)

        setattr(cls, key, wrapped)

    if args.ui == "qt":
        from PySide6.QtCore import QEvent, QObject, Qt, QTimer

        from filescope.ui.qt_results import ResultTable, ResultView
        from filescope.ui.qt_window import Window, create_application

        if args.trace:
            for cls, keys in (
                (ResultTable, ("extend", "data", "beginInsertRows", "endInsertRows")),
                (ResultView, ("lessThan", "sort_key", "filterAcceptsRow")),
                (Window, ("_drain", "_theme", "_show_text")),
            ):
                for key in keys:
                    instrument(cls, key)
        imported = time.perf_counter()
        app = create_application()
        runtime = time.perf_counter()
        window = Window(settings)
        if args.sort:
            window.table.sortByColumn(1, Qt.AscendingOrder)
        window.root_input.setEditText(args.corpus)
        window.query.setText(args.query)
        window_ready = time.perf_counter()
        shown = [0.0]

        class Filter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Paint and not shown[0]:
                    shown[0] = time.perf_counter()
                return False

        observer = Filter(window)
        window.table.viewport().installEventFilter(observer)
        window.show()
        schedule = QTimer.singleShot
        loop = app.exec
        def finish():
            return window.summary
        close = window.close
    else:
        import tkinter as tk

        from filescope.platform.tempfiles import purge_stale
        from filescope.ui.main_window import MainWindow

        imported = time.perf_counter()
        purge_stale()
        root = tk.Tk()
        if args.light:
            from filescope.ui.tk_light import apply_light

            apply_light(root)
        runtime = time.perf_counter()
        root.geometry("1260x790")
        window = MainWindow(root, settings=settings)
        window.root_var.set(args.corpus)
        window.query_var.set(args.query)
        window_ready = time.perf_counter()
        shown = [0.0]
        root.bind("<Map>", lambda e: shown.__setitem__(0, time.perf_counter()) if e.widget is root else None)
        schedule = root.after
        loop = root.mainloop
        def finish():
            return window.session.summary if window.session and window.session.events.empty() else None
        close = window.on_close
    ticks = []
    outcome = {}

    def begin():
        now = time.perf_counter()
        cpu = time.process_time()
        previous = now
        window.start_search()
        callback = time.perf_counter() - now
        cancelled_at = []

        def cancel():
            cancelled_at.append(time.perf_counter())
            if args.ui == "qt":
                window._cancel()
            else:
                window.cancel_search()

        if args.cancel_ms is not None:
            schedule(args.cancel_ms, cancel)

        def sample():
            nonlocal previous
            current = time.perf_counter()
            ticks.append((current - previous) * 1000)
            previous = current
            summary = finish()
            if summary is None and current - now < 90:
                schedule(40, sample)
                return
            if summary is not None:
                from benchmark import ProcessMemory

                outcome.update(
                    settings_ms=(loaded-start)*1000, imports_ms=(imported-loaded)*1000,
                    runtime_ms=(runtime-imported)*1000, build_ms=(window_ready-runtime)*1000,
                    first_window_ms=(shown[0]-start)*1000 if shown[0] else None,
                    ready_ms=(now-start)*1000, callback_ms=callback*1000,
                    seconds=current-now, engine_seconds=summary.elapsed,
                    hits=len(summary.results), cpu_s=time.process_time()-cpu,
                    peak_mib=ProcessMemory.peak_bytes()/2**20,
                    p50=statistics.median(ticks), p95=sorted(ticks)[int(.95*(len(ticks)-1))],
                    maximum=max(ticks),
                    cancelled=summary.coverage.cancelled,
                    cancel_ms=(current-cancelled_at[0])*1000 if cancelled_at else None,
                )
            close()

        schedule(40, sample)

    schedule(0 if args.ui == "qt" else 1, begin)
    loop()
    if not outcome:
        raise RuntimeError("search failed or closed early")
    outcome["trace"] = {k: [len(v), round(sum(v), 2), round(max(v), 2)] for k, v in costs.items()}
    print(json.dumps(outcome))


if __name__ == "__main__":
    main()
