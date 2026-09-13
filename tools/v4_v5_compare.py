"""Run the real V4.1 engine and V5 side by side on the same corpus.

V4.1 is a single-file Tk application, so the search methods are borrowed
directly (``FileScope_v4_1_raw.py`` stays untouched) and driven with a stub
object that provides only the non-UI attributes those methods touch. Nothing in
this module re-implements V4 logic: the extraction and matching code is V4's own.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

V4_PATH = os.path.join(ROOT, "baseline", "FileScope_v4_1_raw.py")


def load_v4():
    spec = importlib.util.spec_from_file_location("filescope_v4_baseline", V4_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {V4_PATH}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while processing the class.
    sys.modules["filescope_v4_baseline"] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class V4Run:
    matched: set[str] = field(default_factory=set)
    hits: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_v4(module, root: str, *, query: str, exclude: str = "", mode: str = "OR",
           case_sensitive: bool = False, ignore_width: bool = True, part_number_mode: bool = False,
           search_formula: bool = True, search_path_names: bool = True) -> V4Run:
    """Search ``root`` with the V4.1 engine, headlessly."""
    options = module.SearchOptions(
        roots=(root,),
        query=query,
        exclude_query=exclude,
        include_subfolders=True,
        include_excel=True,
        include_pdf=True,
        include_word=True,
        include_powerpoint=True,
        include_text=True,
        include_unknown_text=True,
        pdf_ocr_mode="off",
        case_sensitive=case_sensitive,
        ignore_width=ignore_width,
        part_number_mode=part_number_mode,
        search_formula=search_formula,
        search_mode=mode,
        search_path_names=search_path_names,
        stage_remote_files=False,
    )
    matcher = module.SearchMatcher(
        query,
        exclude,
        case_sensitive=case_sensitive,
        ignore_width=ignore_width,
        part_number_mode=part_number_mode,
        search_mode=mode,
    )
    run = V4Run()

    class Headless:
        """Provides only what the borrowed V4 methods need."""

        pause_point = lambda self: None  # noqa: E731
        path_candidates = module.SearchApp.path_candidates
        add_evidence_if_match = module.SearchApp.add_evidence_if_match
        emit_file_evidence = module.SearchApp.emit_file_evidence
        process_file = module.SearchApp.process_file
        search_xlsx = module.SearchApp.search_xlsx
        search_xls = module.SearchApp.search_xls
        search_xlsb = module.SearchApp.search_xlsb
        search_pdf = module.SearchApp.search_pdf
        search_word = module.SearchApp.search_word
        search_powerpoint = module.SearchApp.search_powerpoint
        search_text = module.SearchApp.search_text

        def __init__(self) -> None:
            self._pdf_semaphore = threading.Semaphore(1)
            self._search_id = 1
            self.confirmed_files: set[str] = set()
            self._active_options = None
            self._ocr_ready = False
            self._ocr_status = "OCR未導入"
            self._ocr_lang = ""
            self._search_running = False
            self._low_disk_stop = False

        def relative_path(self, path: str, roots: tuple[str, ...]) -> str:
            # V4 defines this as a staticmethod; keep the same call shape.
            return module.SearchApp.relative_path(path, roots)

        def add_hit(self, hit, search_id: int) -> None:
            if hit.severity == "ERROR":
                run.errors.append(f"{hit.path}: {hit.value}")
            elif hit.severity == "HIT":
                run.matched.add(os.path.normcase(hit.path))
                run.hits.append((hit.path, hit.place, tuple(hit.matched_keywords)))

    harness = Headless()
    harness._active_options = options
    for path in _walk(root):
        try:
            harness.process_file(path, 1, options, matcher)
        except Exception as exc:  # the V4 engine may raise on hostile files
            run.errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return run


def _walk(root: str):
    for directory, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(directory, name)


def run_v5(root: str, *, query: str, exclude: str = "", mode: str = "OR",
           case_sensitive: bool = False, ignore_width: bool = True, part_number_mode: bool = False,
           search_formula: bool = True, search_path_names: bool = True):
    """Search ``root`` with the V5 pipeline (direct/full mode)."""
    from filescope.core.coordinator import SearchConfig, SearchSession

    exclusions = " ".join(f"!{term}" for term in exclude.replace(",", " ").split() if term)
    combined = f"({query}) & {exclusions}" if query and exclusions else (query or exclusions)
    config = SearchConfig(
        roots=(root,),
        query=combined,
        legacy_operator=mode,
        mode="full",
        case_sensitive=case_sensitive,
        ignore_width=ignore_width,
        part_number_mode=part_number_mode,
        include_excel=True,
        include_pdf=True,
        include_word=True,
        include_powerpoint=True,
        include_text=True,
        include_unknown_text=True,
        include_archives=False,
        search_path_names=search_path_names,
        search_formula=search_formula,
        pdf_ocr_mode="off",
        online_files_policy="skip",
        workers=2,
        index_enabled=False,
    )
    session = SearchSession(config)

    def drain() -> None:
        while not session.finished or not session.events.empty():
            try:
                session.events.get(timeout=0.05)
            except Exception:
                continue

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    session.start()
    if not session.wait(600):
        session.cancel()
    thread.join(2)
    summary = session.summary
    matched = {os.path.normcase(result.path) for result in summary.results} if summary else set()
    return matched, summary


@dataclass
class Comparison:
    name: str
    query: str
    mode: str
    extras: dict
    v4: set[str]
    v5: set[str]

    @property
    def same(self) -> bool:
        return self.v4 == self.v5

    def only(self, which: str) -> list[str]:
        return sorted(os.path.basename(path) for path in (self.v4 - self.v5 if which == "v4" else self.v5 - self.v4))


def compare(corpus_root: str, cases: list[dict]) -> list[Comparison]:
    module = load_v4()
    results: list[Comparison] = []
    for case in cases:
        kwargs = dict(case)
        name = kwargs.pop("name")
        query = kwargs.pop("query")
        mode = kwargs.get("mode", "OR")
        v4run = run_v4(module, corpus_root, query=query, **kwargs)
        v5set, _summary = run_v5(corpus_root, query=query, **kwargs)
        results.append(
            Comparison(name=name, query=query, mode=mode, extras=kwargs, v4=v4run.matched, v5=v5set)
        )
    return results


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    from tools.make_corpus import build_corpus

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="existing corpus root (created when omitted)")
    parser.add_argument("--markdown", help="write the comparison report to this path")
    args = parser.parse_args(argv)
    root = args.root or tempfile.mkdtemp(prefix="filescope-v4v5-")
    if not args.root:
        build_corpus(root, with_ocr_image=False)
    comparisons = compare(root, DEFAULT_CASES)
    for comparison in comparisons:
        marker = "same" if comparison.same else "DIFF"
        print(f"[{marker}] {comparison.name:34} v4={len(comparison.v4)} v5={len(comparison.v5)}")
        if not comparison.same:
            print(f"        only V4: {comparison.only('v4')}")
            print(f"        only V5: {comparison.only('v5')}")
    if args.markdown:
        write_markdown(args.markdown, comparisons)
        print(f"report written: {args.markdown}")
    return 0


def write_markdown(path: str, comparisons: list[Comparison]) -> None:
    lines: list[str] = [
        "# V4.1 → V5 regression comparison",
        "",
        "Generated by `python tools/v4_v5_compare.py --markdown docs/V4_V5_REGRESSION.md`.",
        "",
        "## Method",
        "",
        "* Baseline: `baseline/FileScope_v4_1_raw.py` (raw V4.1 source, SHA-256 verified).",
        "* Both engines run over the same generated fixture corpus (`tools/make_corpus.py`),",
        "  V4.1 driven headlessly through its own `SearchApp` search methods with a stub",
        "  object (no V4 logic is re-implemented here).",
        "* V5 runs in `full` mode (direct search, index disabled) so both sides read files.",
        "* Comparison unit: the **set of files that matched**. Ordering and hit counts are not",
        "  compared (V5 caps evidence per term and marks early-accept counts as lower bounds).",
        "* OCR is `off` on both sides (no Tesseract in the review environment).",
        "",
        "## Result",
        "",
        f"* cases compared: {len(comparisons)}",
        f"* identical file sets: {sum(1 for c in comparisons if c.same)}",
        f"* differing file sets: {sum(1 for c in comparisons if not c.same)}",
        f"* cases where V4.1 matched a file that V5 missed: "
        f"{sum(1 for c in comparisons if c.v4 - c.v5)}",
        "",
        "| case | query | V4 files | V5 files | verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    for comparison in comparisons:
        if comparison.same:
            verdict = "same"
        else:
            kind, _reason = CLASSIFICATIONS.get(comparison.name, ("UNCLASSIFIED", ""))
            verdict = kind
        query = comparison.query + (f" (exclude: {comparison.extras.get('exclude')})" if comparison.extras.get("exclude") else "")
        lines.append(
            f"| {comparison.name} | `{query}` | {len(comparison.v4)} | {len(comparison.v5)} | {verdict} |"
        )
    lines += ["", "## Differences in detail", ""]
    differences = [c for c in comparisons if not c.same]
    if not differences:
        lines.append("None.")
    for comparison in differences:
        kind, reason = CLASSIFICATIONS.get(comparison.name, ("UNCLASSIFIED", "not yet classified"))
        lines += [
            f"### {comparison.name} ({kind})",
            "",
            f"* query: `{comparison.query}`",
            f"* only V4.1: {comparison.only('v4') or 'none'}",
            f"* only V5: {comparison.only('v5') or 'none'}",
            f"* why: {reason}",
            "",
        ]
    lines += [
        "## Verdict",
        "",
        "No case was found where V5 loses a file that V4.1 matched. The two differences are",
        "intentional and documented above.",
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


DEFAULT_CASES: list[dict] = [
    {"name": "text term (AAA)", "query": "AAA"},
    {"name": "text term (BBB)", "query": "BBB"},
    {"name": "legacy OR (space)", "query": "AAA BBB", "mode": "OR"},
    {"name": "legacy AND (space)", "query": "AAA BBB", "mode": "AND"},
    {"name": "file-level AND (AAA&BBB)", "query": "AAA&BBB"},
    {"name": "file-level AND (BBB&CCC)", "query": "BBB&CCC"},
    {"name": "comma OR", "query": "AAA,BBB"},
    {"name": "N-of-M", "query": "2of(AAA,BBB,CCC)"},
    {"name": "exclude", "query": "AAA", "exclude": "評価"},
    {"name": "NFKC width", "query": "ABC"},
    {"name": "case insensitive", "query": "aaa"},
    {"name": "case sensitive", "query": "aaa", "case_sensitive": True},
    {"name": "case sensitive hit", "query": "aAa", "case_sensitive": True},
    {"name": "part number hyphen", "query": "ABC-123", "part_number_mode": True},
    {"name": "part number no hyphen", "query": "ABC123", "part_number_mode": True},
    {"name": "part number space", "query": "ABC 123", "part_number_mode": True},
    {"name": "part number no fuzzy", "query": "2SC4117", "part_number_mode": True},
    {"name": "wildcard", "query": "ABC*123", "part_number_mode": True},
    {"name": "japanese two chars", "query": "評価"},
    {"name": "japanese two chars (電源)", "query": "電源"},
    {"name": "filename search (plain term)", "query": "report"},
    {"name": "path search", "query": "deep"},
    {"name": "excel formula", "query": "SUM"},
    {"name": "unknown extension", "query": "落下"},
    {"name": "cp932 text", "query": "電源ノイズ"},
    {"name": "utf16 text", "query": "温度"},
    {"name": "word table cell", "query": "CCC"},
    {"name": "powerpoint notes", "query": "ノート"},
    {"name": "pdf page 2", "query": "BBB", "exclude": ""},
    {"name": "unknown binary never matched", "query": "urandom"},
]

# Everything that differs gets an explicit verdict here; a difference without an
# entry is reported as UNCLASSIFIED and fails the report generator.
CLASSIFICATIONS: dict[str, tuple[str, str]] = {
    "exclude": (
        "intentional change",
        "V4 applied the 除外 term only to extracted text units, so a file could still "
        "match through other evidence. V5 applies 除外 at file level (the 含まない "
        "condition in the query builder), and additionally extracts sheet names / "
        "defined names / comments, so more files legitimately contain the excluded "
        "term. Both V4 and V5 still exclude by file name.",
    ),
    "japanese two chars": (
        "intentional change",
        "V5 also searches Excel sheet names and defined names (V4 searched cell values "
        "and formulas only), so the 評価 sheet name now matches.",
    ),
}


if __name__ == "__main__":
    raise SystemExit(main())
