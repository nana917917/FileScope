# Third-party notices

FileScope itself is developed for internal use. It depends on the components
below. **Nothing is bundled into this repository**: everything is installed with
`pip`, and the licenses below are the upstream ones. This file records the
licence review that the V5 specification asked for (sections 70 and 96).

| Component | Purpose | License | Bundled? |
| --- | --- | --- | --- |
| openpyxl | .xlsx/.xlsm/.xltx/.xltm reading | MIT | no (pip) |
| xlrd | .xls reading | BSD-3-Clause | no (pip) |
| pyxlsb | .xlsb reading | MIT | no (pip) |
| pypdf | PDF text extraction | BSD-3-Clause | no (pip) |
| pypdfium2 | PDF page rendering (optional OCR) | Apache-2.0 / BSD-3-Clause (PDFium: BSD-3-Clause) | no (pip) |
| python-docx | .docx reading | MIT | no (pip) |
| python-pptx | .pptx reading | MIT | no (pip) |
| Pillow | image objects for OCR | MIT-CMU (HPND) | no (pip) |
| pytesseract | Tesseract binding (optional) | Apache-2.0 | no (pip) |
| pywin32 | Excel cell jump, shell integration (optional) | PSF-2.0 | no (pip) |
| Tesseract OCR | OCR engine (optional, external install) | Apache-2.0 | **not bundled** |
| Leptonica | Tesseract's image library | BSD-2-Clause | **not bundled** |
| tessdata (jpn, eng) | Tesseract language data | Apache-2.0 (language data: see tessdata LICENSE) | **not bundled** |
| pytest | development/testing | MIT | no (dev only) |
| ruff | development/linting | MIT | no (dev only) |
| xlwt | generates .xls test fixtures | BSD-3-Clause | no (dev only) |

Only the Python standard library is used for the UI (tkinter), the index
(sqlite3 with FTS5) and the pipeline (threading, queue).

## Tesseract bundling decision

Tesseract is **not** shipped with FileScope at this time. A portable bundle would
require redistributing the engine, Leptonica, the language data and their licence
texts, and adds roughly 100-200 MB depending on languages. FileScope detects an
existing installation instead (see README) and degrades to "OCR: 未導入" without
failing normal PDF, Office or text search.

If a bundle is ever considered, the review must confirm: the exact Tesseract and
Leptonica versions, the tessdata licence for each language, the source offer
obligations of the chosen Windows build, and antivirus behaviour of the packaged
binaries.

## Packaging

`pyproject.toml` declares runtime dependencies, an `ocr` extra (pytesseract,
pypdfium2) and a `dev` extra (pytest, ruff, xlwt). A PyInstaller build is
described in `scripts/build_portable.ps1`; onedir is preferred over onefile for
startup time, antivirus false positives and easier troubleshooting, and the
build has not been executed in this environment (see the final report).
