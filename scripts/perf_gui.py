"""GUI-level probe: is the search slow, or is the window update heavy?

Runs one real search through MainWindow (hidden Tk root) and records the time
the UI thread spends in its own update callbacks, the worst stall, and how long
it takes for the first result to become visible.

Run it from the repository root with the project venv:

    .venv\\Scripts\\python.exe scripts\\perf_gui.py --corpus <dir> --appdata <dir> \\
        --label direct-all --index on --query-preset all --json out.json

``--appdata`` is used as the whole LOCALAPPDATA for the run, so settings, logs
and the index stay out of the real user profile.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(os.path.dirname(HERE))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--appdata", required=True)
    parser.add_argument("--temp", default="")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--label", default="run")
    parser.add_argument("--query-preset", default="all", choices=["all", "selective", "none"])
    parser.add_argument("--refine-preset", default="", choices=["", "power", "none"])
    parser.add_argument("--mode", default="standard")
    parser.add_argument("--index", default="on", choices=["on", "off"])
    parser.add_argument("--cancel-after", type=float, default=0.0)
    parser.add_argument("--grace", type=float, default=1.5)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    if args.root not in sys.path:
        sys.path.insert(0, args.root)
    temp = args.temp or os.path.join(os.path.dirname(args.appdata), "temp")
    os.makedirs(args.appdata, exist_ok=True)
    os.makedirs(temp, exist_ok=True)
    os.environ["LOCALAPPDATA"] = args.appdata
    os.environ["TEMP"] = temp
    os.environ["TMP"] = temp

    import tkinter as tk

    from filescope.config import Settings
    from filescope.ui.main_window import MainWindow
    from filescope.ui.preview import PreviewPane  # noqa: F401  (keeps import cost realistic)

    root = tk.Tk()
    root.withdraw()
    settings = Settings()
    settings.index_enabled = args.index == "on"
    settings.autosave_results = False
    window = MainWindow(root, settings=settings)

    timings: dict[str, list[float]] = {}

    def instrument(name: str) -> None:
        original = getattr(window, name)

        def timed(*a, **k):
            start = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                timings.setdefault(name, []).append(time.perf_counter() - start)

        setattr(window, name, timed)

    for method in (
        "_drain_events",
        "_render_results",
        "_fill_window",
        "_visible_rows",
        "_apply_progress",
        "_apply_full_scan_updates",
        "_rebuild_facets",
        "_on_finished",
    ):
        instrument(method)

    tick_gaps: list[float] = []
    last_tick = [time.perf_counter()]

    def tick() -> None:
        now = time.perf_counter()
        tick_gaps.append(now - last_tick[0])
        last_tick[0] = now
        root.after(40, tick)

    tick()
    queries = {"all": "評価", "selective": "評価 & BBB", "none": ""}
    refines = {"": "", "power": "BBB 電源", "none": ""}
    query = queries[args.query_preset]
    refine = refines[args.refine_preset]
    window.root_var.set(args.corpus)
    window.query_var.set(query)
    window._refine_var.set(refine)

    first_visible = None
    finished_at = None
    cancel_at = None
    started = time.perf_counter()
    window.start_search()
    cancelled = False
    while True:
        root.update()
        session = window.session
        if first_visible is None and window.model.all_rows:
            first_visible = time.perf_counter() - started
        if session is not None and session.finished:
            finished_at = time.perf_counter() - started
            break
        if args.cancel_after and not cancelled and time.perf_counter() - started >= args.cancel_after:
            cancelled = True
            window.cancel_search()
            cancel_at = time.perf_counter()
        if time.perf_counter() - started > 600:
            break
        time.sleep(0.002)
    if cancelled and cancel_at is not None:
        while not (window.session and window.session.finished):
            root.update()
            time.sleep(0.002)
        cancel_latency = time.perf_counter() - cancel_at
    else:
        cancel_latency = None

    deadline = time.perf_counter() + args.grace
    while time.perf_counter() < deadline:
        root.update()
        time.sleep(0.002)

    summary = window.session.summary if window.session else None
    report = {
        "label": args.label,
        "query": query,
        "refine": refine,
        "mode": args.mode,
        "index": args.index,
        "files": sum(1 for _ in os.scandir(args.corpus)) if os.path.isdir(args.corpus) else 0,
        "rows": len(window.model.all_rows),
        "visible_rows": len(window.model.visible),
        "tree_rows": len(window.tree.get_children()),
        "first_result_visible_seconds": _round(first_visible),
        "finished_seconds": _round(finished_at),
        "search_seconds": _round(getattr(summary, "elapsed", None)),
        "cancel_latency_seconds": _round(cancel_latency),
        "ui": {
            "drain_total_seconds": _round(sum(timings.get("_drain_events", []))),
            "drain_calls": len(timings.get("_drain_events", [])),
            "drain_max_seconds": _round(_max(timings.get("_drain_events", []))),
            "render_total_seconds": _round(sum(timings.get("_render_results", []))),
            "render_calls": len(timings.get("_render_results", [])),
            "render_max_seconds": _round(_max(timings.get("_render_results", []))),
            "fill_total_seconds": _round(sum(timings.get("_fill_window", []))),
            "fill_calls": len(timings.get("_fill_window", [])),
            "fill_max_seconds": _round(_max(timings.get("_fill_window", []))),
            "visible_rows_total_seconds": _round(sum(timings.get("_visible_rows", []))),
            "progress_calls": len(timings.get("_apply_progress", [])),
            "scan_update_seconds": _round(sum(timings.get("_apply_full_scan_updates", []))),
            "tick_gap_max_seconds": _round(_max(tick_gaps)),
            "tick_gap_p50_seconds": _round(statistics.median(tick_gaps) if tick_gaps else None),
            "tick_gaps_over_100ms": sum(1 for gap in tick_gaps if gap > 0.1),
        },
        "elapsed_to_grace_end": _round(time.perf_counter() - started),
    }
    if summary is not None:
        report["coverage"] = summary.coverage.line()
        report["issue_count"] = len(summary.issues)

    with contextlib.suppress(Exception):
        window.on_close()
    with contextlib.suppress(Exception):
        root.destroy()

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return 0


def _max(values: list[float]) -> float | None:
    return max(values) if values else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


if __name__ == "__main__":
    raise SystemExit(main())
