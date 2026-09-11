"""FileScope benchmark (spec sections 78-80).

Measures enumeration, first-result latency, throughput, repeated searches,
index hits, index updates, PDF/OCR cost, peak RSS and cache size. V4.1 cannot be
run for comparison (its committed payload is corrupt -- see baseline/README.md),
so the numbers reported are V5 direct search vs V5 indexed search.

Usage:
    python scripts/benchmark.py --root <folder> [--count 2000] [--json out.json]
    python scripts/benchmark.py --generate --count 2000
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filescope.core.coordinator import SearchConfig, SearchSession  # noqa: E402
from filescope.core.scanner import iter_entries  # noqa: E402
from filescope.index.database import IndexDatabase  # noqa: E402
from filescope.ocr_cache import OcrCache  # noqa: E402
from filescope.platform import tesseract as tesseract_module  # noqa: E402
from tools.make_corpus import build_bench_corpus, build_corpus  # noqa: E402


class ProcessMemory:
    """Peak working set for this process (Windows; 0 elsewhere)."""

    class _Counters(ctypes.Structure):
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
    def peak_bytes() -> int:
        if os.name != "nt":
            return 0
        try:
            counts = ProcessMemory._Counters()
            counts.cb = ctypes.sizeof(counts)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            function = ctypes.windll.psapi.GetProcessMemoryInfo
            function.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ProcessMemory._Counters),
                ctypes.c_ulong,
            ]
            function.restype = ctypes.c_int
            if function(process, ctypes.byref(counts), counts.cb):
                return int(counts.PeakWorkingSetSize)
        except (AttributeError, OSError):
            return 0
        return 0


def measure_enumerations(root: str, repeats: int = 3) -> dict:
    best = None
    files = 0
    for _ in range(repeats):
        start = time.perf_counter()
        count = 0
        for entry in iter_entries(root):
            if isinstance(entry, object) and hasattr(entry, "path"):
                count += 1
        elapsed = time.perf_counter() - start
        files = count
        best = elapsed if best is None else min(best, elapsed)
    return {
        "files": files,
        "seconds": round(best or 0.0, 4),
        "files_per_second": round(files / best, 1) if best else 0.0,
    }


def run_search(root: str, query: str, *, mode: str, database=None, ocr_cache=None, ocr_ready=False) -> dict:
    config = SearchConfig(
        roots=(root,),
        query=query,
        mode=mode,
        workers=4,
        index_enabled=database is not None,
        include_unknown_text=True,
    )
    session = SearchSession(config, database=database, ocr_cache=ocr_cache, ocr_available=ocr_ready)
    first_result: list[float] = []
    started = time.perf_counter()

    def drain() -> None:
        while not session.finished or not session.events.empty():
            try:
                event = session.events.get(timeout=0.05)
            except Exception:
                continue
            if getattr(event, "result", None) is not None and not first_result:
                first_result.append(time.perf_counter() - started)

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    session.start()
    if not session.wait(1800):
        session.cancel()
    thread.join(2)
    elapsed = time.perf_counter() - started
    summary = session.summary
    coverage = summary.coverage if summary else None
    discovered = coverage.discovered if coverage else 0
    return {
        "seconds": round(elapsed, 3),
        "results": len(summary.results) if summary else 0,
        "hits": summary.total_hits if summary else 0,
        "discovered": discovered,
        "files_per_second": round(discovered / elapsed, 1) if elapsed else 0.0,
        "first_result_seconds": round(first_result[0], 4) if first_result else None,
        "coverage": coverage.__dict__ if coverage else {},
        "index_used": summary.index_used if summary else False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="folder to benchmark (created when --generate is used)")
    parser.add_argument("--count", type=int, default=2000, help="files for --generate")
    parser.add_argument("--generate", action="store_true", help="generate a synthetic corpus first")
    parser.add_argument("--json", help="write the raw measurements to this file")
    parser.add_argument("--include-documents", action="store_true", help="also time Office/PDF search")
    args = parser.parse_args(argv)

    root = args.root or tempfile.mkdtemp(prefix="filescope-bench-")
    if args.generate:
        print(f"generating {args.count} files under {root} ...")
        build_bench_corpus(root, count=args.count)
    if not os.path.isdir(root):
        print(f"root not found: {root}", file=sys.stderr)
        return 2

    ocr_status = tesseract_module.probe()
    results: dict = {
        "root": root,
        "python": sys.version.split()[0],
        "ocr": {"ready": ocr_status.ready, "message": ocr_status.message},
    }
    print(f"root              : {root}")
    print(f"OCR               : {ocr_status.message or 'unknown'}")

    enumeration = measure_enumerations(root)
    results["enumeration"] = enumeration
    print(
        f"enumeration       : {enumeration['files']:,} files in {enumeration['seconds']}s "
        f"({enumeration['files_per_second']:,}/s)"
    )

    index_path = os.path.join(tempfile.mkdtemp(prefix="filescope-bench-index-"), "index.sqlite3")
    database = IndexDatabase(index_path, max_bytes=2 * 1024 * 1024 * 1024)
    ocr_cache = OcrCache(os.path.join(os.path.dirname(index_path), "ocr.sqlite3"))

    direct_first = run_search(root, "AAA 評価", mode="full", ocr_ready=ocr_status.ready)
    results["direct_first"] = direct_first
    print(
        f"direct (first)    : {direct_first['seconds']}s  {direct_first['results']:,} results  "
        f"first-result {direct_first['first_result_seconds']}s  "
        f"{direct_first['files_per_second']:,} files/s"
    )

    indexed_first = run_search(
        root, "AAA 評価", mode="standard", database=database, ocr_cache=ocr_cache, ocr_ready=ocr_status.ready
    )
    results["index_build"] = indexed_first
    print(
        f"index build run   : {indexed_first['seconds']}s  {indexed_first['results']:,} results  "
        f"(read {indexed_first['coverage'].get('read', 0):,} / indexed {indexed_first['coverage'].get('indexed', 0):,})"
    )

    indexed_repeat = run_search(
        root, "AAA 評価", mode="standard", database=database, ocr_cache=ocr_cache, ocr_ready=ocr_status.ready
    )
    results["index_repeat"] = indexed_repeat
    print(
        f"index repeat      : {indexed_repeat['seconds']}s  {indexed_repeat['results']:,} results  "
        f"first-result {indexed_repeat['first_result_seconds']}s  "
        f"(read {indexed_repeat['coverage'].get('read', 0):,} / indexed {indexed_repeat['coverage'].get('indexed', 0):,})"
    )

    short_term = run_search(
        root, "評価", mode="standard", database=database, ocr_cache=ocr_cache, ocr_ready=ocr_status.ready
    )
    results["index_short_term"] = short_term
    print(f"2-char JP term    : {short_term['seconds']}s  {short_term['results']:,} results")

    selective_direct = run_search(root, "BBB", mode="full")
    selective_index = run_search(
        root, "BBB", mode="standard", database=database, ocr_cache=ocr_cache, ocr_ready=ocr_status.ready
    )
    results["selective_direct"] = selective_direct
    results["selective_index"] = selective_index
    print(
        f"selective (BBB)   : direct {selective_direct['seconds']}s / "
        f"indexed {selective_index['seconds']}s  ({selective_index['results']:,} results)"
    )

    status = database.status()
    results["index"] = {
        "files": status.files,
        "chunks": status.chunks,
        "size_bytes": status.size_bytes,
        "trigram": status.trigram,
    }
    print(f"index             : {status.files:,} files / {status.chunks:,} chunks / {human(status.size_bytes)}")
    results["ocr_cache_size"] = ocr_cache.size_bytes()

    if args.include_documents:
        docs_root = tempfile.mkdtemp(prefix="filescope-bench-docs-")
        build_corpus(docs_root, with_ocr_image=True)
        docs_direct = run_search(docs_root, "AAA&BBB", mode="full", ocr_ready=ocr_status.ready)
        results["documents_direct"] = docs_direct
        print(f"documents direct  : {docs_direct['seconds']}s  {docs_direct['results']} results")
        docs_index = run_search(
            docs_root, "AAA&BBB", mode="standard", database=database, ocr_ready=ocr_status.ready
        )
        results["documents_index"] = docs_index
        print(f"documents indexed : {docs_index['seconds']}s  {docs_index['results']} results")

    results["peak_rss_bytes"] = ProcessMemory.peak_bytes()
    print(f"peak RSS          : {human(results['peak_rss_bytes'])}")

    database.close()
    ocr_cache.close()
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2)
        print(f"raw measurements  : {args.json}")
    return 0


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


if __name__ == "__main__":
    raise SystemExit(main())
