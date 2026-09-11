# FileScope V5 architecture

This document describes how a search actually runs, so the next person (or
agent) can change one part without breaking the others.

## 1. Pipeline

```
discovery            core/scanner.py      os.scandir walk, stat only (no file open)
   |
metadata pre-filter  core/coordinator.py extension flags, size limits, cloud policy
   |
cloud/local check    platform/onedrive.py attribute-based policy per search mode
   |
index / cache        index/search.py      candidate set from FTS or LIKE scan
   |
extraction queue     core/coordinator.py  bounded queues, general lane + PDF lane
   |
matcher              core/matcher.py      streaming AST evaluation, JIT stop
   |
result aggregator    core/models.py       FileResult + Coverage + Issue streams
   |
UI event queue       core/events.py       bounded queue, drained by the UI timer
```

The enumerator pushes `FileEntry` objects into two bounded queues (general, PDF).
Workers never talk to Tk; they publish events and the UI drains them every
`DRAIN_MS` (200 ms).

## 2. Thread and process model

| Thread | Count | Responsibility |
| --- | --- | --- |
| discovery | 1 | enumerate entries, route to lanes, clean stale index rows per root |
| general worker | `workers` (default 4) | Office/text/archive extraction + matching |
| PDF worker | 1 | PDF parsing and OCR, so one huge scan cannot block everything |
| monitor | 1 | join workers, finalize summary |
| UI | 1 | Tk main loop, never performs file IO |
| preview | short-lived | extracts only the selected file for the preview pane |

There is no thread-per-file and no unbounded future pool. Queues are bounded, so
back-pressure is explicit: a slow consumer slows the producers instead of
allocating memory.

Cancellation is a `threading.Event`; `Sink.check()` raises `StopExtraction`
inside extractors so a long PDF stops within one page. Pause is a second event
that workers wait on.

PDF/OCR isolated in a subprocess is deliberately **not** implemented: it needs a
frozen-build-safe entry point and a second IPC protocol, and the same protection
(timeouts, one-at-a-time, resource release) is already provided in-process. The
extractor interface leaves room to add it later without touching the pipeline.

## 3. Query AST (`filescope/core/query.py`)

One recursive-descent parser produces:

```
Term(text, phrase)            Meta(field, op, value)
Not(child)                    And(children) / Or(children)
NOf(count, children)          Near(left, right, distance)
```

Precedence: `NOT` > `NEAR`/atoms > `AND` > `OR`, with parentheses. Legacy v4
syntax is reproduced by explicit rules rather than a second parser:

* a space is AND, except when the query contains no explicit operator at all and
  the UI operator setting is OR (the v4 "A B follows the toggle" rule),
* `,`/`;` are OR, `&` is AND, so `AAA BBB,CCC DDD` is `(AAA&BBB)|(CCC&DDD)`,
* `2of(A,B,C)` is a file-level `NOf`,
* `かつ`/`または` and full-width `＆，；｜` fold to the ASCII operators,
* `-` is NOT only when the remaining token is not part-number shaped, so
  `-123` and `-ABC-123` still search literally.

Errors (`QuerySyntaxError`) carry a character position and are shown in the
search bar before any file is touched.

## 4. Matcher (`filescope/core/matcher.py`)

* Terms are evaluated **file level**: `AAA&BBB` is satisfied when the two terms
  appear anywhere in the same file (separate sheets, pages or slides).
* `NEAR` is evaluated per chunk (one cell / page / paragraph / slide) and is
  latched forever once a chunk satisfies it.
* `Not` and the `confirmed:`/`ocr:` filters are non-monotone, so those queries
  always read to end-of-file. Everything else stops as soon as the condition is
  provably satisfied (JIT), which is why a direct search can return a file after
  a handful of rows instead of the whole document.
* A conservative `_can_still_match` check stops reading when an `NOf` can no
  longer reach its threshold.
* Evidence is capped per term (default 3) so memory does not grow with hits.
* `score()` is deterministic: coverage, phrase bonus, filename/path bonus, hit
  count. Nothing is AI-judged.

## 5. Index (`filescope/index/`)

SQLite in WAL mode at `%LOCALAPPDATA%\FileScope\index\filescope-index.sqlite3`.

```
files(id, path_key, display_path, source_type, cloud_state, extension, size,
      mtime_ns, extractor_version, ocr_version, ocr_language, indexed_at,
      status, chunk_count, text_chars)
chunks(id, file_id, chunk_index, kind, location, text, norm_text, part_text)
chunks_fts(norm_text)       FTS5 trigram, external content on chunks
chunks_fts_part(part_text)  FTS5 trigram, part-number canonical form
meta(key, value)
```

* `norm_text` is NFKC + casefold so a case-insensitive search is expressible;
  `part_text` has separators removed so `ABC123` finds `ABC-123`.
* Candidate generation: FTS for terms of 3+ characters, `LIKE` scan over the
  cached text for 1-2 characters (Japanese short terms) and for `*` wildcards.
  The scan is slower but never silently misses.
* **Verification always runs the same `QueryMatcher` over the stored chunks**, so
  direct and indexed searches cannot disagree. `tests/test_index.py` asserts
  that for a fixture corpus across AND/OR/N-of-M/NOT/NEAR/phrase/metadata/part
  number and Japanese 2-character queries.
* `status` is `ok`, `partial` (text truncated), `too_large`, or `empty`
  (unsupported/unreadable). `partial`/`too_large`/`empty` are never served from
  the index: those files are re-read, so the index can never claim "no content".
* Differential update keys: path, size, mtime_ns, extractor version, OCR version
  and OCR language. A change in any of them re-extracts that file only.
* Capacity (`index_max_bytes`) stops *new* indexing when exceeded; existing rows
  are never deleted to make room.
* `PRAGMA quick_check` runs on open; on damage the index raises
  `IndexUnavailable`, the UI disables it, records the reason in settings and
  searches directly. The application still starts.

Index rows for deleted files are removed after a complete enumeration of that
root (`_cleanup_index`).

## 6. Extractors (`filescope/extractors/`)

An ordered registry, first `supports()` wins -- no factories, no plugin
framework. Each extractor streams `Chunk(text, kind, location)` into the sink:

| Extractor | Sources of text |
| --- | --- |
| excel | openpyxl / xlrd / pyxlsb values, formulas (+ cached values pass), sheet names, defined names, comments, hyperlinks, text boxes, chart titles |
| word | python-docx paragraphs/tables + headers, footers, footnotes, endnotes, comments, `w:txbxContent` |
| powerpoint | shapes, tables, notes, hidden slides, comments, chart titles, SmartArt |
| pdf | pypdf text layer per page; pypdfium2 render + pytesseract OCR when needed |
| text | encoding sniffing (UTF-8/BOM/CP932/Shift-JIS/UTF-16), line streaming |
| archive | zip members re-dispatched through the same registry, depth/entry/size/ratio guards |
| unknown | 64 KiB probe, binary sniff, then the text extractor |

Every chunk carries a human location (`評価!D52`, `12ページ [OCR]`, `段落 3`,
`archive.zip > inner.txt / 1行`) which is what the UI shows as evidence.

## 7. OCR

* Discovery: `TESSERACT_CMD`, `PATH`, Program Files, LocalAppData, app folder.
* Missing Tesseract is a *skip*, never an error: FileScope starts, normal PDFs
  still search, and diagnostics explains it.
* Modes: off / auto (native text under 40 characters -> OCR) / all.
* One page at a time, 30 s timeout, rendered image released immediately.
* Results are cached in `%LOCALAPPDATA%\FileScope\index\ocr-cache.sqlite3` keyed
  by path, size, mtime, page, language, OCR version and render scale. The cache
  is trimmed LRU under its own byte budget; images are never stored.
* OCR-derived hits are labelled `[OCR]` and filterable with `ocr:true`.

## 8. Cloud state and staging

`platform/windows.py` reads Windows file attributes only:

```
FILE_ATTRIBUTE_RECALL_ON_OPEN / RECALL_ON_DATA_ACCESS -> Online only
FILE_ATTRIBUTE_OFFLINE                                -> Online only
FILE_ATTRIBUTE_PINNED (without UNPINNED)              -> Always available
UNC or DRIVE_REMOTE                                   -> SMB
```

`platform/onedrive.py` turns that plus the search mode and the user policy into
a `read_content` decision. `platform/tempfiles.py` stages SMB/Office files into
`%TEMP%\FileScope-staging`, tracking every copy for cleanup on success, failure,
cancel and crash (`purge_stale` on the next start).

## 9. Reliability

* Settings: atomic write (temp + fsync + `os.replace`), schema version, corrupt
  file quarantined and defaults restored.
* Logging: rotating file in `%LOCALAPPDATA%\FileScope\logs`, no document text.
* Crash handling: `sys.excepthook` and `threading.excepthook` write a local
  report (`crashes\`) plus diagnostics; the next start is unaffected.
* Issues are classified `skip` / `warn` / `error` and shown separately from
  results, so "0 hits" and "could not be read" are never conflated.
* Files that change while being read are detected by size+mtime before/after and
  retried once, then reported as "updated during search".

## 10. UI (`filescope/ui/`)

Tkinter was kept: PySide6 would add ~150 MB to a portable build, an LGPL
packaging review, and a second styling system, while the two UI problems here
(large result sets, preview) are solved by the model below.

* `results.ResultModel` holds all rows; the `Treeview` only ever materialises
  `WINDOW_ROWS` (300) of them, with the scrollbar mapped onto model positions.
* Refine, facets and confirmed filters operate on the in-memory result set;
  a re-search is never triggered and remote files are never re-read.
* `preview.PreviewPane` extracts only the selected file, in a worker thread, and
  hands results to the UI thread through a queue (no Tk calls off-thread).
* Search modes, coverage counters, index status and diagnostics are all visible
  from the main window.

## 11. Known limitations

* A query whose terms match almost every file gives the index little to prune,
  so indexed and direct search cost about the same (see `bench_results.json`).
* 1-2 character terms use a `LIKE` scan over cached text; correct, but linear in
  the cached text size.
* Executable/unknown binary formats are skipped by design; there is no IFilter
  bridge (the extractor registry is the extension point for one).
* `.xlsb` fixtures cannot be generated by the test tooling, so that path is
  covered by code review only.
