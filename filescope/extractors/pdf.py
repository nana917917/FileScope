"""PDF extraction: native text first, local Tesseract OCR for scanned pages.

v4 behaviour kept: one PDF at a time (the pipeline gives PDFs their own lane),
page-at-a-time processing, native text preferred, rendered images released
immediately, OCR timeout.
"""

from __future__ import annotations

from ..core.models import Chunk, ChunkKind, FileEntry
from ..core.paths import normalized_key
from ..errors import Issue, Severity
from .base import ExtractContext, ExtractOptions, ExtractResult, Sink

NATIVE_TEXT_MIN_CHARS = 40        # v4 threshold for "this page is a scan"
MAX_PAGE_OCR_SECONDS = 30
RENDER_SCALE = 2.0


class PdfExtractor:
    name = "pdf"
    version = "5.0"

    def supports(self, entry: FileEntry, options: ExtractOptions) -> bool:
        return entry.extension == ".pdf"

    def extract(self, context: ExtractContext, sink: Sink) -> ExtractResult:
        result = ExtractResult()
        limit = context.options.limits.pdf_max_bytes
        if context.entry.size > limit:
            result.skipped = True
            result.reason = f"PDF上限（{limit // (1024 * 1024)}MB）を超えています"
            return result

        try:
            from pypdf import PdfReader
        except ImportError:
            return _missing_dependency(context, result, "pypdf")

        try:
            reader = PdfReader(context.path)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        raise ValueError("password required")
                except Exception:
                    result.skipped = True
                    result.reason = "暗号化PDFのため検索できません"
                    result.warnings.append(
                        Issue(
                            path=context.entry.path,
                            code="pdf-encrypted",
                            message=result.reason,
                            severity=Severity.WARN,
                        )
                    )
                    return result
            page_count = len(reader.pages)
        except Exception as exc:
            result.skipped = True
            result.reason = f"PDFを開けませんでした: {type(exc).__name__}"
            result.warnings.append(
                Issue(path=context.entry.path, code="pdf-open", message=result.reason, severity=Severity.WARN)
            )
            return result

        ocr_ready = bool(context.options.ocr_available)
        warned_no_ocr = False
        for index in range(page_count):
            sink.check()
            page_label = f"{index + 1}ページ"
            text = ""
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception as exc:
                result.warnings.append(
                    Issue(
                        path=context.entry.path,
                        code="pdf-page",
                        message=f"{page_label} の文字抽出に失敗しました（{type(exc).__name__}）",
                        severity=Severity.WARN,
                    )
                )
            if text.strip():
                sink.add(Chunk(text=text, kind=ChunkKind.PAGE, location=page_label))

            if context.options.ocr_mode == "off":
                continue
            needs_ocr = context.options.ocr_mode == "all" or (
                context.options.ocr_mode == "auto" and len(text.strip()) < NATIVE_TEXT_MIN_CHARS
            )
            if not needs_ocr:
                continue
            if not ocr_ready:
                if not warned_no_ocr:
                    warned_no_ocr = True
                    result.warnings.append(
                        Issue(
                            path=context.entry.path,
                            code="ocr-unavailable",
                            message="OCR未導入のためスキャンページを検索できません（通常PDF検索は利用できます）",
                            severity=Severity.SKIP,
                        )
                    )
                continue
            if self._ocr_page(context, index, page_label, sink, result):
                result.ocr_pages += 1
        return result

    def _ocr_page(
        self, context: ExtractContext, index: int, page_label: str, sink: Sink, result: ExtractResult
    ) -> str:
        cache = context.options.ocr_cache
        key = ""
        if cache is not None:
            key = cache.make_key(
                normalized_key(context.entry.path),
                context.entry.size,
                context.entry.mtime_ns,
                index,
                context.options.ocr_languages,
                RENDER_SCALE,
            )
            cached = cache.get(key)
            if cached is not None:
                sink.add(Chunk(text=cached, kind=ChunkKind.OCR, location=page_label))
                return cached

        image = None
        try:
            image = render_page(context.path, index, RENDER_SCALE)
        except Exception as exc:
            result.warnings.append(
                Issue(
                    path=context.entry.path,
                    code="pdf-render",
                    message=f"{page_label} の画像化に失敗しました（{type(exc).__name__}）",
                    severity=Severity.WARN,
                )
            )
            return ""

        text = ""
        try:
            import pytesseract

            text = pytesseract.image_to_string(
                image, lang=context.options.ocr_languages, timeout=MAX_PAGE_OCR_SECONDS
            )
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            result.warnings.append(
                Issue(
                    path=context.entry.path,
                    code="ocr-timeout",
                    message=f"{page_label} のOCRがタイムアウトしました",
                    severity=Severity.WARN,
                    detail=str(exc),
                )
            )
        except Exception as exc:
            result.warnings.append(
                Issue(
                    path=context.entry.path,
                    code="ocr-error",
                    message=f"{page_label} のOCRに失敗しました（{type(exc).__name__}）",
                    severity=Severity.WARN,
                )
            )
        finally:
            close = getattr(image, "close", None)
            if callable(close):
                close()

        text = (text or "").strip()
        if text:
            sink.add(Chunk(text=text, kind=ChunkKind.OCR, location=page_label))
            if cache is not None and key:
                cache.put(key, normalized_key(context.entry.path), index, text)
        return text


def render_page(path: str, index: int, scale: float):
    """Render one PDF page to a PIL image (pypdfium2, freed by the caller)."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    page = document[index]
    try:
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil()
        finally:
            bitmap.close()
    finally:
        page.close()
        document.close()


def _missing_dependency(context: ExtractContext, result: ExtractResult, name: str) -> ExtractResult:
    result.skipped = True
    result.reason = f"{name} が未導入です"
    result.warnings.append(
        Issue(path=context.entry.path, code="pdf-dependency", message=result.reason, severity=Severity.WARN)
    )
    return result
