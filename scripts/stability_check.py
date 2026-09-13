"""Startup/shutdown and long-run stability check (spec sections 42-44, 61).

Cycles the real application (GUI objects created and closed, searches run,
cancelled, resumed, previewed) while watching RSS, thread count, handle count
and the TEMP staging folder for unbounded growth. Everything runs against a
temporary corpus; no user document is opened.

    python scripts/stability_check.py --cycles 10 --search-rounds 40
    python scripts/stability_check.py --minutes 30
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "filescope-stability"))

from filescope import paths  # noqa: E402
from filescope.platform import tempfiles  # noqa: E402
from tools.make_corpus import build_corpus  # noqa: E402


class Memory:
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    @staticmethod
    def working_set() -> int:
        counts = Memory.Counters()
        counts.cb = ctypes.sizeof(counts)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        function = ctypes.windll.psapi.GetProcessMemoryInfo
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(Memory.Counters), ctypes.c_ulong]
        function.restype = ctypes.c_int
        if function(handle, ctypes.byref(counts), counts.cb):
            return int(counts.WorkingSetSize)
        return 0

    @staticmethod
    def handles() -> int:
        count = ctypes.c_ulong(0)
        function = ctypes.windll.kernel32.GetProcessHandleCount
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        function.restype = ctypes.c_int
        if function(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(count)):
            return int(count.value)
        return 0


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def make_corpus(base: Path, *, files: int = 120) -> Path:
    root = base / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    build_corpus(root, with_ocr_image=False)
    extra = root / "many"
    extra.mkdir(exist_ok=True)
    for index in range(files):
        (extra / f"doc{index:04d}.txt").write_text(
            f"AAA 評価 {index}\nBBB 電源\n耐久 試験\n", encoding="utf-8"
        )
    return root


def sample() -> tuple[int, int, int, int]:
    gc.collect()
    return (
        Memory.working_set(),
        threading.active_count(),
        Memory.handles(),
        tempfiles.staging_bytes(),
    )


def run_cycles(corpus: Path, *, cycles: int, search_rounds: int, deadline: float) -> list[tuple]:
    import tkinter as tk

    from filescope.config import Settings
    from filescope.ui.main_window import MainWindow

    samples: list[tuple] = []
    for cycle in range(cycles):
        root = tk.Tk()
        root.withdraw()
        settings = Settings()
        settings.index_enabled = True
        settings.index_max_bytes = 64 * 1024 * 1024
        window = MainWindow(root, settings=settings)
        window.root_var.set(str(corpus))
        for round_index in range(search_rounds):
            if time.time() > deadline:
                break
            window.query_var.set("AAA,BBB" if round_index % 2 else "耐久&BBB")
            window.start_search()
            finish = time.time() + 30
            while time.time() < finish:
                root.update()
                if window.session is not None and window.session.finished:
                    break
                time.sleep(0.02)
            rows = window.tree.get_children()
            if rows:
                window.tree.selection_set(rows[0])
                for _ in range(4):
                    root.update()
                    time.sleep(0.02)
            if round_index % 5 == 3:
                window.start_search()
                window.cancel_search()
                for _ in range(10):
                    root.update()
                    time.sleep(0.02)
        window.on_close()
        for _ in range(5):
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(0.05)
        samples.append(sample())
        print(
            f"cycle {cycle + 1:2}/{cycles}: rss={human(samples[-1][0])}"
            f" threads={samples[-1][1]} handles={samples[-1][2]} temp={human(samples[-1][3])}"
        )
        if time.time() > deadline:
            break
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=8, help="GUI start/stop cycles")
    parser.add_argument("--search-rounds", type=int, default=6, help="searches per cycle")
    parser.add_argument("--minutes", type=float, default=4.0, help="hard time limit")
    args = parser.parse_args(argv)

    base = Path(tempfile.mkdtemp(prefix="filescope-stability-"))
    corpus = make_corpus(base)
    print(f"workspace: {base}")
    print(f"staging  : {paths.staging_dir()}")
    before = sample()
    print(f"baseline : rss={human(before[0])} threads={before[1]} handles={before[2]}")

    deadline = time.time() + args.minutes * 60
    run_cycles(corpus, cycles=args.cycles, search_rounds=args.search_rounds, deadline=deadline)
    after = sample()
    print(
        f"final    : rss={human(after[0])} threads={after[1]} handles={after[2]}"
        f" temp={human(after[3])}"
    )

    leaked_threads = after[1] - before[1]
    leaked_handles = after[2] - before[2]
    leaked_temp = after[3]
    failures: list[str] = []
    if leaked_threads > 3:
        failures.append(f"thread leak: +{leaked_threads}")
    if leaked_handles > 60:
        failures.append(f"handle growth: +{leaked_handles}")
    if leaked_temp > 0:
        failures.append(f"staging leftovers: {human(leaked_temp)}")
    print(f"threads delta: {leaked_threads:+d}  handles delta: {leaked_handles:+d}")
    if failures:
        print("stability: FAIL -> " + "; ".join(failures))
        return 1
    print("stability: PASS (no unbounded growth observed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
