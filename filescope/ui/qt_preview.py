"""Bounded preview work using the existing extraction and matching pipeline."""

from __future__ import annotations

import io
import os
import re
from dataclasses import replace

from ..core.models import FileEntry
from ..platform import onedrive


def entry_for(result):
    return FileEntry(
        path=result.path, size=result.size, mtime_ns=result.mtime_ns,
        extension=os.path.splitext(result.path)[1].lower(),
        source_type=result.source_type, cloud_state=result.cloud_state,
    )


def can_read(result, config):
    return onedrive.decide(
        entry_for(result), mode=config.mode, policy=config.online_files_policy
    ).read_content


def text_preview(result, config, matcher, cancelled, ocr_cache, ocr_available):
    from ..core.matcher import Outcome
    from ..extractors import ExtractOptions, Sink, extract

    if not can_read(result, config):
        return "取得設定により本文を表示できません。", None
    entry = entry_for(result)
    state = matcher.make_state(entry, name=result.name, directory=result.directory) if matcher else None
    lines = []
    remaining = 120_000

    def consume(chunk):
        nonlocal remaining
        if state:
            state.feed(chunk)
        if remaining > 0 and chunk.text:
            line = f"{chunk.location}  {chunk.text}"[:remaining]
            lines.append(line)
            remaining -= len(line) + 1

    options = ExtractOptions(
        limits=config.limits, ocr_mode=config.pdf_ocr_mode,
        ocr_languages=config.ocr_languages, include_archives=config.include_archives,
        search_formula=config.search_formula,
        ocr_cache=ocr_cache, ocr_available=ocr_available,
    )
    output = extract(entry, result.path, Sink(consume, cancel=cancelled), options)
    updated = None
    if (not cancelled.is_set() and not output.skipped and not output.truncated and not output.warnings
            and state and not result.hit_count_exact and state.finish() is Outcome.ACCEPT):
        updated = replace(
            result, hit_count=state.hit_count, hit_count_exact=True,
            evidence=state.evidence(), matched_terms=state.matched_terms(), displays=state.displays(),
        )
    if remaining <= 0:
        lines.append("…（以降省略）")
    return "\n".join(lines) or output.reason or "本文テキストが見つかりませんでした。", updated


def pdf_preview(result, config, page):
    if not can_read(result, config):
        raise ValueError("取得設定によりページを表示できません。")

    # PyMuPDF is not needed. Render via the Windows candidate's existing
    # PDFium dependency. OCR uses the same PDFium lock.
    import pypdfium2 as pdfium

    from ..extractors.pdf import pdfium_lock

    with pdfium_lock:
        document = pdfium.PdfDocument(result.path)
        try:
            count = len(document)
            if not count:
                raise ValueError("ページがありません。")
            page = min(max(1, page), count)
            pdf_page = document[page - 1]
            try:
                width, height = pdf_page.get_size()
                scale = min(1.5, 1500 / max(width, height))
                bitmap = pdf_page.render(scale=scale)
                try:
                    output = io.BytesIO()
                    bitmap.to_pil().save(output, format="PNG")
                    return output.getvalue(), page, count
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()


def evidence_page(result):
    for item in result.evidence:
        match = re.search(r"(\d+)\s*ページ|(?:p\.?\s*)(\d+)", item.location, re.I)
        if match:
            return int(match.group(1) or match.group(2))
    return 1
