"""How the indexed search path behaves as worker threads increase.

Reports wall clock and process CPU time so serialisation shows up as wall time
growing while CPU time stays flat.

    .venv\\Scripts\\python.exe scripts\\perf_scaling.py --corpus <dir> --appdata <dir>
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(os.path.dirname(HERE))


def run_once(corpus: str, query: str, *, mode: str, database, ocr_cache, workers: int) -> dict:
    from filescope.core.coordinator import SearchConfig, SearchSession

    config = SearchConfig(
        roots=(corpus,),
        query=query,
        mode=mode,
        workers=workers,
        index_enabled=database is not None,
        include_unknown_text=True,
    )
    session = SearchSession(config, database=database, ocr_cache=ocr_cache, ocr_available=False)
    first: list[float] = []
    cpu_before = time.process_time()
    started = time.perf_counter()

    def drain() -> None:
        while not session.finished or not session.events.empty():
            try:
                event = session.events.get(timeout=0.02)
            except Exception:
                continue
            if type(event).__name__ == "ResultAdded" and not first:
                first.append(time.perf_counter() - started)

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    session.start()
    session.wait(1800)
    wall = time.perf_counter() - started
    cpu = time.process_time() - cpu_before
    thread.join(5)
    return {
        "wall": round(wall, 4),
        "cpu": round(cpu, 4),
        "first": round(first[0], 4) if first else None,
        "hits": len(session.results),
        "indexed": session.coverage.indexed,
        "read": session.coverage.read,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--appdata", required=True)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--workers", default="1,2,4,8")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--query-preset", default="all", choices=["all", "selective"])
    parser.add_argument("--mode", default="standard", choices=["standard", "direct", "fast"])
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    if args.root not in sys.path:
        sys.path.insert(0, args.root)
    temp = os.path.join(os.path.dirname(args.appdata), "temp")
    os.makedirs(args.appdata, exist_ok=True)
    os.makedirs(temp, exist_ok=True)
    os.environ["LOCALAPPDATA"] = args.appdata
    os.environ["TEMP"] = temp
    os.environ["TMP"] = temp

    from filescope.index.database import IndexDatabase
    from filescope.ocr_cache import OcrCache

    query = {"all": "評価", "selective": "評価 & BBB"}[args.query_preset]
    database = None if args.mode == "direct" else IndexDatabase(
        os.path.join(args.appdata, "FileScope", "index", "filescope-index.sqlite3")
    )
    ocr_cache = None if database is None else OcrCache(
        os.path.join(args.appdata, "FileScope", "index", "ocr-cache.sqlite3")
    )
    report: dict[str, object] = {"query": query, "mode": args.mode, "runs": {}}
    runs: dict[str, object] = report["runs"]  # type: ignore[assignment]
    try:
        if database is not None:
            warm = run_once(args.corpus, query, mode="standard", database=database,
                            ocr_cache=ocr_cache, workers=4)
            report["warmup"] = warm
        for workers in [int(value) for value in args.workers.split(",") if value]:
            samples = [
                run_once(args.corpus, query, mode=args.mode, database=database,
                         ocr_cache=ocr_cache, workers=workers)
                for _ in range(args.repeat)
            ]
            runs[str(workers)] = {
                "wall_median": round(statistics.median([s["wall"] for s in samples]), 4),
                "cpu_median": round(statistics.median([s["cpu"] for s in samples]), 4),
                "wall_samples": [s["wall"] for s in samples],
                "cpu_samples": [s["cpu"] for s in samples],
                "hits": samples[-1]["hits"],
                "indexed": samples[-1]["indexed"],
                "read": samples[-1]["read"],
            }
        if args.profile:
            profiler = cProfile.Profile()
            profiler.enable()
            run_once(args.corpus, query, mode=args.mode, database=database,
                     ocr_cache=ocr_cache, workers=1)
            profiler.disable()
            buffer = io.StringIO()
            pstats.Stats(profiler, stream=buffer).sort_stats("tottime").print_stats(22)
            report["profile"] = buffer.getvalue()
    finally:
        if database is not None:
            database.close()
        if ocr_cache is not None:
            ocr_cache.close()

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(json.dumps({k: v for k, v in report.items() if k != "profile"},
                     ensure_ascii=False, indent=2, sort_keys=True))
    if "profile" in report:
        print(report["profile"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
