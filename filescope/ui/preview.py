"""Preview pane: evidence for the selected file, built on demand.

Phase 2 of the JIT design: extraction happens only for the selected file.
Nothing is generated for files the user never selects (spec sections 40-41, 86).
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections import OrderedDict
from dataclasses import dataclass, field
from tkinter import ttk

from ..core.models import FileEntry, FileResult
from ..extractors import extract
from ..extractors.base import ExtractOptions, Sink
from ..logging_setup import get_logger

log = get_logger("preview")

MAX_PREVIEW_CHARS = 40_000
CACHE_LIMIT = 8


@dataclass
class PreviewContent:
    title: str
    lines: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    note: str = ""


class PreviewPane(ttk.Frame):
    def __init__(self, master, *, on_status=None) -> None:
        super().__init__(master)
        self.on_status = on_status
        self._cache: OrderedDict[tuple, PreviewContent] = OrderedDict()
        self._match_indexes: list[str] = []
        self._match_index = 0
        self._token = 0
        self._current: FileResult | None = None
        self._config = None
        self._search_text = ""
        # Worker threads never touch Tk: finished previews arrive here and are
        # applied by the UI thread on a timer.
        self._pending: queue.Queue = queue.Queue()

        header = ttk.Frame(self)
        header.pack(fill="x", padx=4, pady=(4, 2))
        self.title_var = tk.StringVar(value="プレビュー")
        ttk.Label(header, textvariable=self.title_var, font=("", 9, "bold")).pack(side="left")
        self.match_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.match_var).pack(side="right")
        ttk.Button(header, text="▶ F3", width=6, command=lambda: self.step(1)).pack(side="right", padx=2)
        ttk.Button(header, text="◀ Shift", width=8, command=lambda: self.step(-1)).pack(side="right")

        self.text = tk.Text(self, wrap="word", height=20, width=46, undo=False)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=(0, 4))
        scroll.pack(side="right", fill="y", pady=(0, 4))

        self.text.tag_configure("hit", background="#ffe680")
        self.text.tag_configure("current", background="#ffb347")
        self.text.tag_configure("location", foreground="#0b6b3a", font=("", 9, "bold"))
        self.text.tag_configure("note", foreground="#777777")
        self.after(120, self._poll_pending)

    # ------------------------------------------------------------- public
    def show(self, result: FileResult, config, *, search_text: str = "") -> None:
        self._current = result
        self._config = config
        self._search_text = search_text
        self._token += 1
        token = self._token
        key = (result.path, result.mtime_ns)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._render(cached, result)
            return
        self.title_var.set(f"{result.name} を読み込み中…")
        self._set_text("")
        entry = FileEntry(
            path=result.path,
            size=result.size,
            mtime_ns=result.mtime_ns,
            extension=os.path.splitext(result.path)[1].lower(),
            source_type=result.source_type,
            cloud_state=result.cloud_state,
        )
        options = self._extract_options(config)
        threading.Thread(
            target=self._load,
            args=(token, key, entry, options),
            name="filescope-preview",
            daemon=True,
        ).start()

    def clear(self) -> None:
        self._current = None
        self.title_var.set("プレビュー")
        self.match_var.set("")
        self._set_text("")

    def step(self, direction: int) -> None:
        if not self._match_indexes:
            return
        self._match_index = (self._match_index + direction) % len(self._match_indexes)
        self._focus_current()

    # ------------------------------------------------------------ loading
    def _extract_options(self, config) -> ExtractOptions:
        return ExtractOptions(
            limits=config.limits,
            ocr_mode=config.pdf_ocr_mode,
            ocr_languages=config.ocr_languages,
            search_formula=config.search_formula,
            include_archives=config.include_archives,
        )

    def _load(self, token: int, key: tuple, entry: FileEntry, options: ExtractOptions) -> None:
        content = self._build(entry, options)
        self._pending.put((token, key, content))

    def _poll_pending(self) -> None:
        while True:
            try:
                token, key, content = self._pending.get_nowait()
            except queue.Empty:
                break
            self._apply(token, key, content)
        self.after(120, self._poll_pending)

    def _apply(self, token: int, key: tuple, content: PreviewContent) -> None:
        if token != self._token:
            return
        self._cache[key] = content
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)
        if self._current is not None:
            self._render(content, self._current)

    def _build(self, entry: FileEntry, options: ExtractOptions) -> PreviewContent:
        chunks = []
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
        total = 0
        for chunk in chunks:
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
            title=os.path.basename(entry.path), lines=lines, locations=locations, note=note
        )

    # ------------------------------------------------------------ rendering
    def _render(self, content: PreviewContent, result: FileResult) -> None:
        self.title_var.set(content.title)
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

    def _focus_current(self) -> None:
        if not self._match_indexes:
            self.match_var.set("一致 0")
            return
        for tag in self.text.tag_names():
            if tag == "current":
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


def _highlight(text: tk.Text, start: str, end: str, term: str, found: list[str]) -> None:
    """Highlight every occurrence of ``term`` between two indexes."""
    position = start
    needle = term.casefold()
    while True:
        position = text.search(term, position, stopindex=end, nocase=True)
        if not position:
            break
        text.tag_add("hit", position, f"{position}+{len(term)}c")
        found.append(position)
        position = f"{position}+{max(1, len(term))}c"
    _ = needle
