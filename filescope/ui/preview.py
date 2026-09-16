"""Preview pane: evidence for the selected file, built on demand.

Phase 2 of the JIT design: extraction happens only for the selected file, and
only the page the user is looking at is rendered. Nothing is generated for
files the user never selects. v5.1 adds the page-image view for PDFs
(spec sections 14-15 of the v5.1 brief).
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from tkinter import ttk

from ..core.models import CloudState, FileEntry, FileKind, FileResult
from ..extractors import extract
from ..extractors.base import ExtractOptions, Sink
from ..logging_setup import get_logger

log = get_logger("preview")

MAX_PREVIEW_CHARS = 40_000
CACHE_LIMIT = 8
MAX_PIXELS = 12_000_000          # one page stays well under ~48MB
PAGE_CACHE_LIMIT = 3
ZOOM_STEPS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


@dataclass
class PreviewContent:
    title: str
    lines: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    note: str = ""
    pages: int = 0


class PreviewPane(ttk.Frame):
    def __init__(self, master, *, on_status=None) -> None:
        super().__init__(master)
        self.on_status = on_status
        self._cache: OrderedDict[tuple, PreviewContent] = OrderedDict()
        self._page_cache: OrderedDict[tuple[str, int, int], object] = OrderedDict()
        self._match_indexes: list[str] = []
        self._match_index = 0
        self._token = 0
        self._current: FileResult | None = None
        self._config = None
        self._search_text = ""
        self._on_chunks = None
        self._pending: queue.Queue = queue.Queue()
        self._pending_pages: queue.Queue = queue.Queue()
        self._view = "text"
        self._page = 1
        self._pages = 0
        self._zoom = 1.0
        self._photo = None
        self._page_job = 0

        self._build_header()
        self._build_view_switch()
        self._build_text_view()
        self._build_image_view()
        self._poll_job = self.after(120, self._poll_pending)
        self._set_text("結果を選ぶと、ここに根拠（セル・ページ・段落）が表示されます。")

    # ------------------------------------------------------------ building
    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", padx=4, pady=(4, 2))
        self.title_var = tk.StringVar(value="プレビュー")
        ttk.Label(header, textvariable=self.title_var, font=("", 10, "bold")).pack(side="left", anchor="w")
        self.match_var = tk.StringVar(value="")
        ttk.Button(header, text="次の一致 ▶", width=11, command=lambda: self.step(1)).pack(side="right", padx=2)
        ttk.Button(header, text="◀ 前の一致", width=11, command=lambda: self.step(-1)).pack(side="right")
        ttk.Label(header, textvariable=self.match_var, foreground="#555").pack(side="right", padx=6)
        self.info_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info_var, foreground="#666").pack(anchor="w", padx=5)

    def _build_view_switch(self) -> None:
        self.view_bar = ttk.Frame(self)
        self.text_button = ttk.Button(self.view_bar, text="文字", width=6, command=lambda: self.set_view("text"))
        self.text_button.pack(side="left")
        self.image_button = ttk.Button(self.view_bar, text="画像", width=6, command=lambda: self.set_view("image"))
        self.image_button.pack(side="left", padx=3)
        self.page_prev = ttk.Button(
            self.view_bar, text="◀", width=3, command=lambda: self.change_page(-1), state="disabled"
        )
        self.page_prev.pack(side="left", padx=(8, 0))
        self.page_var = tk.StringVar(value="")
        ttk.Label(self.view_bar, textvariable=self.page_var).pack(side="left", padx=4)
        self.page_next = ttk.Button(
            self.view_bar, text="▶", width=3, command=lambda: self.change_page(1), state="disabled"
        )
        self.page_next.pack(side="left")
        ttk.Button(self.view_bar, text="拡大", width=5, command=lambda: self.change_zoom(1)).pack(
            side="left", padx=(10, 2)
        )
        ttk.Button(self.view_bar, text="縮小", width=5, command=lambda: self.change_zoom(-1)).pack(side="left")
        self.zoom_var = tk.StringVar(value="")
        ttk.Label(self.view_bar, textvariable=self.zoom_var, foreground="#666").pack(side="left", padx=4)
        self.view_bar.pack_forget()          # only shown for documents with pages

    def _build_text_view(self) -> None:
        self.text_frame = ttk.Frame(self)
        self.text_frame.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 4))
        self.text = tk.Text(
            self.text_frame, wrap="word", height=20, width=46, undo=False, font=("Consolas", 9)
        )
        scroll = ttk.Scrollbar(self.text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.tag_configure("hit", background="#ffe680")
        self.text.tag_configure("current", background="#ffb347")
        self.text.tag_configure("location", foreground="#0b6b3a", font=("Consolas", 9, "bold"))
        self.text.tag_configure("note", foreground="#777777")

    def _build_image_view(self) -> None:
        self.image_frame = ttk.Frame(self)
        self.canvas = tk.Canvas(self.image_frame, background="#f3f3f3", highlightthickness=0)
        hbar = ttk.Scrollbar(self.image_frame, orient="horizontal", command=self.canvas.xview)
        vbar = ttk.Scrollbar(self.image_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        self.image_frame.rowconfigure(0, weight=1)
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.pack_forget()

    # ------------------------------------------------------------- public
    def show(self, result: FileResult, config, *, search_text: str = "", on_chunks=None) -> None:
        self._current = result
        self._config = config
        self._search_text = search_text
        self._on_chunks = on_chunks
        self._token += 1
        token = self._token
        key = (result.path, result.mtime_ns)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._render(cached, result)
            return
        self.title_var.set(result.name)
        self.info_var.set("読み込み中…")
        self._set_text("")
        options = self._extract_options(config)
        threading.Thread(
            target=self._load,
            args=(token, key, self._entry_for(result), options, on_chunks, result),
            name="filescope-preview",
            daemon=True,
        ).start()

    def clear(self) -> None:
        self._current = None
        self.title_var.set("プレビュー")
        self.match_var.set("")
        self.info_var.set("")
        self.view_bar.pack_forget()
        self._set_text("結果を選ぶと、ここに根拠（セル・ページ・段落）が表示されます。")
        self._show_text_view()

    def step(self, direction: int) -> None:
        if not self._match_indexes:
            return
        self._match_index = (self._match_index + direction) % len(self._match_indexes)
        self._focus_current()

    def stop(self) -> None:
        """Cancel pending timers so shutdown stays quiet."""
        with suppress(tk.TclError):
            self.after_cancel(self._poll_job)
            if self._page_job:
                self.after_cancel(self._page_job)

    # ------------------------------------------------------------ page view
    def set_view(self, view: str) -> None:
        if view == "image" and (not self._current or self._pages <= 0):
            return
        self._view = view
        if view == "image":
            self._show_image_view()
            self._request_page()
        else:
            self._show_text_view()

    def _show_text_view(self) -> None:
        self.image_frame.pack_forget()
        self.text_frame.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 4))

    def _show_image_view(self) -> None:
        self.text_frame.pack_forget()
        self.image_frame.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 4))

    def change_page(self, delta: int) -> None:
        if self._pages <= 0:
            return
        target = min(max(1, self._page + delta), self._pages)
        if target == self._page:
            return
        self._page = target
        self.set_view("image")

    def change_zoom(self, direction: int) -> None:
        try:
            index = ZOOM_STEPS.index(self._zoom)
        except ValueError:
            index = 2
        index = min(max(0, index + direction), len(ZOOM_STEPS) - 1)
        self._zoom = ZOOM_STEPS[index]
        if self._view == "image":
            self._request_page()

    def _update_page_controls(self) -> None:
        if self._pages > 0 and self._current and self._current.file_kind is FileKind.PDF:
            self.view_bar.pack(fill="x", padx=4, pady=(2, 2), before=self.text_frame)
            self.page_var.set(f"{self._page} / {self._pages}ページ")
            self.zoom_var.set(f"{self._zoom * 100:.0f}%")
            self.page_prev.configure(state="normal" if self._page > 1 else "disabled")
            self.page_next.configure(state="normal" if self._page < self._pages else "disabled")
            self.image_button.configure(state="normal" if self._pdf_renderable() else "disabled")
            self.text_button.configure(state="normal")
        else:
            self.view_bar.pack_forget()

    def _pdf_renderable(self) -> bool:
        """Respect the search's cloud policy: never hydrate just for a preview."""
        if self._current is None:
            return False
        if self._current.cloud_state is not CloudState.ONLINE_ONLY:
            return True
        from ..platform import onedrive

        policy = getattr(self._config, "online_files_policy", "auto")
        mode = getattr(self._config, "mode", "standard")
        return onedrive.decide(
            self._entry_for(self._current), mode=mode, policy=policy
        ).read_content

    def _request_page(self) -> None:
        if self._current is None or self._pages <= 0:
            return
        if not self._pdf_renderable():
            self._draw_note("オンライン専用ファイルのため画像表示は行いません（検索時の取得設定に従います）")
            return
        if self._page_job:
            with suppress(tk.TclError):
                self.after_cancel(self._page_job)
            self._page_job = 0
        key = (self._current.path, self._page, int(self._zoom * 100))
        cached = self._page_cache.get(key)
        if cached is not None:
            self._page_cache.move_to_end(key)
            self._show_photo(cached)
            return
        self._draw_note("ページを読み込んでいます…")
        threading.Thread(
            target=self._render_page,
            args=(self._token, key, self._current.path, self._page, self._zoom),
            name="filescope-pdf-page",
            daemon=True,
        ).start()

    def _render_page(self, token: int, key: tuple, path: str, page: int, zoom: float) -> None:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(path)
            try:
                pdf_page = document[page - 1]
                try:
                    bitmap = pdf_page.render(scale=zoom * 1.5)
                    try:
                        image = bitmap.to_pil()
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            finally:
                document.close()
            if image.width * image.height > MAX_PIXELS:
                ratio = (MAX_PIXELS / (image.width * image.height)) ** 0.5
                image = image.resize(
                    (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
                )
        except Exception as exc:  # rendering must never break the pane
            self._pending_pages.put(
                (token, key, None, f"ページ画像を作成できませんでした（{type(exc).__name__}）")
            )
            return
        self._pending_pages.put((token, key, image, ""))

    def _show_photo(self, image) -> None:
        from PIL import ImageTk
        if image is None:
            self._draw_note("ページ画像を作成できませんでした")
            return
        self._photo = ImageTk.PhotoImage(image)   # keep a reference for Tk
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, image.width, image.height))
        self.canvas.yview_moveto(0)

    def _draw_note(self, text: str) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(12, 12, anchor="nw", text=text, fill="#666", width=320)
        self.canvas.configure(scrollregion=(0, 0, 340, 80))

    # ------------------------------------------------------------ loading
    def _entry_for(self, result: FileResult) -> FileEntry:
        return FileEntry(
            path=result.path,
            size=result.size,
            mtime_ns=result.mtime_ns,
            extension=os.path.splitext(result.path)[1].lower(),
            source_type=result.source_type,
            cloud_state=result.cloud_state,
        )

    def _extract_options(self, config) -> ExtractOptions:
        return ExtractOptions(
            limits=config.limits,
            ocr_mode=config.pdf_ocr_mode,
            ocr_languages=config.ocr_languages,
            search_formula=config.search_formula,
            include_archives=config.include_archives,
        )

    def _load(self, token: int, key: tuple, entry: FileEntry, options: ExtractOptions, on_chunks, result) -> None:
        chunks: list = []
        content = self._build(entry, options, chunks)
        if on_chunks is not None and chunks:
            # Phase 2 of JIT: the file was read in full for the preview, so the
            # caller can replace the early-accept hit count with the exact one.
            try:
                on_chunks(result, chunks)
            except Exception as exc:
                log.debug("preview callback failed: %s", exc)
        self._pending.put((token, key, content))

    def _poll_pending(self) -> None:
        while True:
            try:
                token, key, content = self._pending.get_nowait()
            except queue.Empty:
                break
            self._apply(token, key, content)
        while True:
            try:
                token, key, image, error = self._pending_pages.get_nowait()
            except queue.Empty:
                break
            if token != self._token:
                continue
            if error:
                self._draw_note(error)
                continue
            self._page_cache[key] = image
            while len(self._page_cache) > PAGE_CACHE_LIMIT:
                self._page_cache.popitem(last=False)
            if self._view == "image":
                self._show_photo(image)
        self._poll_job = self.after(120, self._poll_pending)

    def _apply(self, token: int, key: tuple, content: PreviewContent) -> None:
        if token != self._token:
            return
        self._cache[key] = content
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)
        if self._current is not None:
            self._render(content, self._current)

    def _build(self, entry: FileEntry, options: ExtractOptions, chunks: list) -> PreviewContent:
        try:
            extract(entry, entry.path, Sink(chunks.append), options)
        except Exception as exc:
            log.debug("preview extraction failed for %s: %s", entry.path, exc)
            return PreviewContent(
                title=os.path.basename(entry.path),
                lines=[f"プレビューを作成できませんでした（{type(exc).__name__}）"],
                note="本文を読み込めませんでした",
            )
        lines: list[str] = []
        locations: list[str] = []
        pages = 0
        total = 0
        for chunk in chunks:
            page_number = page_of(chunk.location)
            if page_number:
                pages = max(pages, page_number)
            for line in str(chunk.text).splitlines() or [""]:
                if not line.strip():
                    continue
                lines.append(line)
                locations.append(chunk.location)
                total += len(line)
            if total > MAX_PREVIEW_CHARS:
                lines.append("…（以降省略）")
                locations.append("")
                break
        note = ""
        if not lines:
            note = "本文テキストが見つかりませんでした（スキャンPDFはOCR設定を確認してください）"
        return PreviewContent(
            title=os.path.basename(entry.path),
            lines=lines,
            locations=locations,
            note=note,
            pages=pages,
        )

    # ------------------------------------------------------------ rendering
    def _render(self, content: PreviewContent, result: FileResult) -> None:
        self.title_var.set(content.title)
        self.info_var.set(info_line(result, content))
        self._pages = content.pages if result.file_kind is FileKind.PDF else 0
        target = page_of(result.evidence[0].location) if result.evidence else 1
        self._page = min(max(1, target or 1), self._pages or 1)
        self._update_page_controls()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        terms = list(result.matched_terms) or ([self._search_text] if self._search_text else [])
        self._match_indexes = []
        last_location = ""
        for line, location in zip(content.lines, content.locations, strict=False):
            if location and location != last_location:
                self.text.insert("end", f"{location}\n", ("location",))
                last_location = location
            start_index = self.text.index("end-1c")
            self.text.insert("end", line + "\n")
            end_index = self.text.index("end-1c")
            for term in terms:
                if not term:
                    continue
                _highlight(self.text, start_index, end_index, term, self._match_indexes)
        if content.note:
            self.text.insert("end", content.note + "\n", ("note",))
        self.text.configure(state="disabled")
        self._match_index = 0
        self._focus_current()
        # A PDF opens in text view (fast); the page image stays one click away.
        if self._view == "image" and self._pages:
            self._show_image_view()
            self._request_page()
        else:
            self._show_text_view()

    def _focus_current(self) -> None:
        if not self._match_indexes:
            self.match_var.set("一致 0")
            return
        self.text.tag_remove("current", "1.0", "end")
        index = self._match_indexes[self._match_index]
        self.text.tag_add("current", index, f"{index}+1c")
        self.text.see(index)
        self.match_var.set(f"一致 {self._match_index + 1}/{len(self._match_indexes)}")

    def _set_text(self, value: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if value:
            self.text.insert("end", value)
        self.text.configure(state="disabled")


def info_line(result: FileResult, content: PreviewContent) -> str:
    parts = [result.file_kind.value, _size(result.size)]
    if content.pages:
        parts.append(f"{content.pages}ページ")
    parts.append(os.path.dirname(result.path))
    if result.matched_terms:
        parts.append("一致: " + " / ".join(result.matched_terms[:6]))
    return " ｜ ".join(part for part in parts if part)


def page_of(location: str) -> int:
    """Extract the page number from labels such as ``12ページ [OCR]``."""
    if not location:
        return 0
    digits = ""
    for char in location:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else 0


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{value}B"


def _highlight(text: tk.Text, start: str, end: str, term: str, found: list[str]) -> None:
    """Highlight every occurrence of ``term`` between two indexes."""
    position = start
    while True:
        position = text.search(term, position, stopindex=end, nocase=True)
        if not position:
            break
        text.tag_add("hit", position, f"{position}+{len(term)}c")
        found.append(position)
        position = f"{position}+{max(1, len(term))}c"
