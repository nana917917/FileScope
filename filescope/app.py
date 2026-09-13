"""Application entry point: settings, crash handling, CLI modes, Tk window."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from contextlib import suppress

from . import diagnostics, paths
from .config import Settings
from .logging_setup import get_logger, setup_logging
from .version import __version__

log = get_logger("app")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.log_level:
        settings.log_level = args.log_level
    setup_logging(settings.log_level)
    install_crash_handler()

    if args.diagnostics:
        text = diagnostics.collect().as_text()
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
        return 0
    if args.self_test:
        from .selftest import main as selftest_main

        argv = ["--keep-workspace"] if args.keep_workspace else []
        if args.quiet:
            argv.append("--quiet")
        if args.out:
            argv += ["--out", args.out]
        return selftest_main(argv)
    if args.search:
        return run_headless_search(args)
    return run_gui(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="FileScope", description="Offline document search for Windows")
    parser.add_argument("--version", action="version", version=f"FileScope {__version__}")
    parser.add_argument("--diagnostics", action="store_true", help="print environment diagnostics and exit")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the release self test (TEMP only, never reads user documents)",
    )
    parser.add_argument("--keep-workspace", action="store_true", help="keep the self-test TEMP folder")
    parser.add_argument("--quiet", action="store_true", help="print only the self-test summary")
    parser.add_argument("--search", metavar="ROOT", help="run a search without the GUI")
    parser.add_argument("--query", default="", help="query text for --search")
    parser.add_argument("--mode", default="standard", choices=("fast", "standard", "full"))
    parser.add_argument("--json", action="store_true", help="emit search results as JSON")
    parser.add_argument("--out", help="also write the JSON/report to this file")
    parser.add_argument("--limit", type=int, default=0, help="limit printed results")
    parser.add_argument("--log-level", default="", help="DEBUG / INFO / WARNING")
    return parser


# ------------------------------------------------------------------ GUI mode


def run_gui(settings: Settings) -> int:
    import tkinter as tk

    from .platform import tesseract as tesseract_module
    from .platform.tempfiles import purge_stale
    from .ui.main_window import MainWindow

    purge_stale()
    ocr_status = tesseract_module.probe()
    log.info("starting FileScope %s (OCR: %s)", __version__, ocr_status.message or "unknown")

    root = tk.Tk()
    root.title(f"FileScope {__version__}")
    root.geometry(settings.window.get("geometry", "1280x820"))
    root.minsize(900, 600)
    if settings.theme == "dark":
        with suppress(tk.TclError):
            root.tk.call("ttk::style", "theme", "use", "clam")

    window = MainWindow(root, settings=settings, ocr_status=ocr_status)
    root.protocol("WM_DELETE_WINDOW", window.on_close)

    def on_configure(event) -> None:
        if event.widget is root:
            settings.window["geometry"] = root.geometry()

    root.bind("<Configure>", on_configure)
    if settings.restore_last_session and settings.defaults.query and settings.defaults.root:
        window._apply_state_to_form()
    root.mainloop()
    return 0


# --------------------------------------------------------------- headless


def run_headless_search(args) -> int:
    from .core.coordinator import SearchConfig, SearchSession
    from .index.database import IndexDatabase
    from .ocr_cache import OcrCache
    from .platform import tesseract as tesseract_module

    settings = Settings.load()
    database = None
    if settings.index_enabled and settings.index_max_bytes > 0:
        try:
            database = IndexDatabase(paths.index_path(), max_bytes=settings.index_max_bytes)
        except Exception as exc:
            print(f"index unavailable: {exc}", file=sys.stderr)
    defaults = settings.defaults
    config = SearchConfig(
        roots=(args.search,),
        query=args.query or defaults.query,
        legacy_operator=defaults.legacy_operator,
        mode=args.mode,
        case_sensitive=defaults.case_sensitive,
        ignore_width=defaults.ignore_width,
        part_number_mode=defaults.part_number_mode,
        include_subfolders=defaults.include_subfolders,
        include_excel=defaults.include_excel,
        include_pdf=defaults.include_pdf,
        include_word=defaults.include_word,
        include_powerpoint=defaults.include_powerpoint,
        include_text=defaults.include_text,
        include_unknown_text=defaults.include_unknown_text,
        include_archives=defaults.include_archives,
        search_path_names=defaults.search_path_names,
        search_formula=defaults.search_formula,
        pdf_ocr_mode=defaults.pdf_ocr_mode,
        online_files_policy=defaults.online_files_policy,
        workers=defaults.workers,
        confirmed=frozenset(settings.confirmed_set()),
        limits=settings.limits,
        index_enabled=settings.index_enabled,
    )
    session = SearchSession(
        config,
        database=database,
        ocr_cache=OcrCache(),
        ocr_available=tesseract_module.probe().ready,
    )
    started = time.time()
    session.start()

    def drain() -> None:
        while not session.finished or not session.events.empty():
            try:
                session.events.get(timeout=0.05)
            except Exception:
                continue

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    if not session.wait(3600):
        session.cancel()
        print("search timed out", file=sys.stderr)
        return 2
    thread.join(2)
    summary = session.summary
    if summary is None:
        print("search did not produce a summary", file=sys.stderr)
        return 2
    elapsed = time.time() - started
    if args.json:
        payload = json.dumps(
            {
                    "query": config.query,
                    "mode": config.mode,
                    "elapsed": round(elapsed, 3),
                    "coverage": summary.coverage.__dict__,
                    "results": [
                        {
                            "path": result.path,
                            "kind": result.file_kind.value,
                            "hits": result.hit_count,
                            "displays": list(result.displays),
                            "from_index": result.from_index,
                        }
                        for result in summary.results[: args.limit or len(summary.results)]
                    ],
            },
            ensure_ascii=False,
            indent=2,
        )
        print(payload)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(payload)
    else:
        lines = [summary.coverage.line()]
        lines.extend(
            f"{result.hit_count:5}  {result.file_kind.value:9} {result.path}"
            for result in summary.results[: args.limit or len(summary.results)]
        )
        print("\n".join(lines))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
    if database is not None:
        database.close()
    # Persist settings (recent roots, index state) even in headless mode, which
    # also proves the data directory is writable on a fresh machine.
    settings.remember_root(args.search)
    settings.save()
    return 0


# ------------------------------------------------------------------ crash


def install_crash_handler() -> None:
    """Write a local crash report; never block the next start (spec section 59)."""

    def handle(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        write_crash_report(exc_type, exc_value, exc_tb)
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "FileScope エラー",
                f"予期しないエラーが発生しました。\n{exc_type.__name__}: {exc_value}\n\n"
                f"詳細ログ: {paths.crash_dir()}",
            )
        except Exception:
            pass

    sys.excepthook = handle

    def thread_hook(args) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        write_crash_report(args.exc_type, args.exc_value, args.exc_traceback)

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


def write_crash_report(exc_type, exc_value, exc_tb) -> str:
    try:
        directory = paths.crash_dir()
        path = os.path.join(directory, f"crash-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"FileScope {__version__}\n{time.ctime()}\n\n")
            handle.write(diagnostics.collect().as_text())
            handle.write("\n\n--- traceback ---\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=handle)
        log.error("crash report written: %s", path)
        return path
    except OSError:
        return ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
