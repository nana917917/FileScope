"""Main window: search form, file-centric results, preview, facets, coverage."""

from __future__ import annotations

import csv
import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import paths
from ..config import (
    MODE_DESCRIPTIONS,
    MODE_LABELS,
    OCR_MODE_LABELS,
    ONLINE_POLICY_LABELS,
    Settings,
)
from ..core import events
from ..core.coordinator import SearchSession
from ..core.models import (
    CLOUD_LABELS,
    SOURCE_LABELS,
    FileKind,
    FileResult,
    SourceType,
)
from ..core.paths import normalized_key
from ..diagnostics import collect as collect_diagnostics
from ..index.database import IndexDatabase
from ..logging_setup import get_logger
from ..ocr_cache import OcrCache
from ..platform import shell
from ..platform import tesseract as tesseract_module
from ..platform.tempfiles import purge_stale
from . import dialogs
from . import results as results_module
from .preview import PreviewPane
from .state import (
    UiState,
    apply_filters,
    apply_preset,
    preset_from_state,
    record_history,
    refine_result,
)

log = get_logger("ui")

DRAIN_MS = 200
MAX_ISSUE_ROWS = 500


class MainWindow(ttk.Frame):
    def __init__(self, master: tk.Tk, *, settings: Settings, ocr_status=None) -> None:
        super().__init__(master)
        self.master = master
        self.settings = settings
        self.state = UiState.from_settings(settings)
        self.ocr_status = ocr_status or tesseract_module.probe()
        self.model = results_module.ResultModel()
        self.session: SearchSession | None = None
        self.database: IndexDatabase | None = None
        self.ocr_cache = OcrCache()
        self._coverage_text = tk.StringVar(value="待機中")
        self._status_text = tk.StringVar(value="")
        self._progress_text = tk.StringVar(value="")
        self._index_text = tk.StringVar(value="")
        self._refine_var = tk.StringVar(value="")
        self._issue_rows = 0
        self._window_start = 0
        self._full_scan_queue: queue.Queue = queue.Queue()

        self.pack(fill="both", expand=True)
        self._build_menu()
        self._build_form()
        self._build_body()
        self._build_status()
        self._bind_keys()
        self._open_index()
        self._update_index_label()
        self.after(DRAIN_MS, self._drain_events)

    # ------------------------------------------------------------- building
    def _build_menu(self) -> None:
        menu = tk.Menu(self.master)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="結果をCSV保存…", command=self.export_csv, accelerator="Ctrl+S")
        file_menu.add_command(label="検索条件をプリセット保存…", command=self.save_preset)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.master.destroy)
        menu.add_cascade(label="ファイル", menu=file_menu)

        tools_menu = tk.Menu(menu, tearoff=0)
        tools_menu.add_command(label="条件を作る…", command=self.open_condition_builder, accelerator="Ctrl+B")
        tools_menu.add_command(label="詳細設定…", command=self.open_settings, accelerator="Ctrl+D")
        tools_menu.add_command(label="索引の状態…", command=self.open_index_status, accelerator="Ctrl+I")
        tools_menu.add_command(label="診断情報…", command=self.open_diagnostics)
        tools_menu.add_separator()
        tools_menu.add_command(label="プリセット…", command=self.open_presets)
        tools_menu.add_command(label="検索履歴…", command=self.open_history)
        menu.add_cascade(label="ツール", menu=tools_menu)

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="キーボードショートカット", command=self.open_shortcuts)
        help_menu.add_command(label="Privacy / 外部送信について", command=self.show_privacy)
        menu.add_cascade(label="ヘルプ", menu=help_menu)
        self.master.configure(menu=menu)

    def _build_form(self) -> None:
        form = ttk.Frame(self, padding=(8, 6, 8, 4))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        ttk.Label(form, text="検索場所").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.root_var = tk.StringVar(value=self.state.root)
        self.root_combo = ttk.Combobox(form, textvariable=self.root_var, values=self.settings.recent_roots)
        self.root_combo.grid(row=0, column=1, columnspan=2, sticky="ew")
        ttk.Button(form, text="参照", width=6, command=self.choose_root).grid(row=0, column=3, sticky="e")

        ttk.Label(form, text="検索").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(4, 0))
        self.query_var = tk.StringVar(value=self.state.query)
        query_entry = ttk.Entry(form, textvariable=self.query_var, font=("Consolas", 11))
        query_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(4, 0))
        query_entry.bind("<Return>", lambda _event: self.start_search())
        self.query_entry = query_entry
        ttk.Button(form, text="検索", command=self.start_search).grid(row=1, column=3, sticky="e", pady=(4, 0))

        ttk.Label(form, text="除外").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(4, 0))
        self.exclude_var = tk.StringVar(value=self.state.exclude_query)
        ttk.Entry(form, textvariable=self.exclude_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(form, text="（空白区切り・!語／-語と同じ）", foreground="#666").grid(
            row=2, column=3, sticky="w", pady=(4, 0)
        )

        row = ttk.Frame(self, padding=(8, 0, 8, 4))
        row.pack(fill="x")
        self.mode_var = tk.StringVar(value=self.state.mode)
        mode_frame = ttk.LabelFrame(row, text="検索モード", padding=(6, 2))
        mode_frame.pack(side="left")
        for mode in ("fast", "standard", "full"):
            ttk.Radiobutton(
                mode_frame,
                text=MODE_LABELS[mode],
                value=mode,
                variable=self.mode_var,
                command=self._update_mode_hint,
            ).pack(side="left", padx=3)
        self.mode_hint = ttk.Label(row, text=MODE_DESCRIPTIONS[self.state.mode], foreground="#555", wraplength=520)
        self.mode_hint.pack(side="left", padx=10)

        exact = ttk.Frame(self, padding=(8, 0, 8, 4))
        exact.pack(fill="x")
        self.kind_vars = {
            "excel": tk.BooleanVar(value=self.state.include_excel),
            "pdf": tk.BooleanVar(value=self.state.include_pdf),
            "word": tk.BooleanVar(value=self.state.include_word),
            "ppt": tk.BooleanVar(value=self.state.include_powerpoint),
            "text": tk.BooleanVar(value=self.state.include_text),
            "unknown": tk.BooleanVar(value=self.state.include_unknown_text),
        }
        for key, label in (("excel", "Excel"), ("pdf", "PDF"), ("word", "Word"), ("ppt", "PPT"), ("text", "Text")):
            ttk.Checkbutton(exact, text=label, variable=self.kind_vars[key]).pack(side="left", padx=2)
        ttk.Checkbutton(exact, text="未知形式", variable=self.kind_vars["unknown"]).pack(side="left", padx=2)
        ttk.Button(exact, text="条件", command=self.open_condition_builder).pack(side="left", padx=(12, 2))
        ttk.Button(exact, text="詳細設定", command=self.open_settings).pack(side="left", padx=2)
        ttk.Button(exact, text="索引", command=self.open_index_status).pack(side="left", padx=2)
        ttk.Button(exact, text="診断", command=self.open_diagnostics).pack(side="left", padx=2)

        actions = ttk.Frame(self, padding=(8, 0, 8, 4))
        actions.pack(fill="x")
        self.search_button = ttk.Button(actions, text="検索開始", command=self.start_search)
        self.search_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="中断", command=self.cancel_search, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        self.pause_button = ttk.Button(actions, text="一時停止", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=4)
        ttk.Button(actions, text="プリセット", command=self.open_presets).pack(side="left", padx=(12, 2))
        ttk.Button(actions, text="履歴", command=self.open_history).pack(side="left", padx=2)
        ttk.Button(actions, text="CSV保存", command=self.export_csv).pack(side="right")
        ttk.Label(actions, textvariable=self._index_text, foreground="#555").pack(side="right", padx=10)
        ocr_text = (
            f"OCR: {self.ocr_status.language_expression or '利用可'}"
            if self.ocr_status.ready
            else "OCR: 未導入（診断）"
        )
        self.ocr_label = ttk.Label(
            actions, text=ocr_text, foreground="#555" if self.ocr_status.ready else "#8a6d3b"
        )
        self.ocr_label.pack(side="right", padx=10)
        self.ocr_label.bind("<Button-1>", lambda _e: self.open_diagnostics())

    def _build_body(self) -> None:
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=3)
        body.add(right, weight=2)

        refine = ttk.Frame(left)
        refine.pack(fill="x", pady=(0, 4))
        ttk.Label(refine, text="結果をさらに絞る").pack(side="left")
        entry = ttk.Entry(refine, textvariable=self._refine_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self.apply_refine())
        ttk.Button(refine, text="適用", command=self.apply_refine).pack(side="left")
        ttk.Button(refine, text="解除", command=self.clear_refine).pack(side="left", padx=4)

        self.facet_frame = ttk.LabelFrame(left, text="絞り込み（クリックで適用）", padding=4)
        self.facet_frame.pack(fill="x", pady=(0, 4))
        self.facet_inner = ttk.Frame(self.facet_frame)
        self.facet_inner.pack(fill="x")

        self.tree = ttk.Treeview(
            left,
            columns=tuple(key for key, _l, _w in results_module.COLUMNS),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in results_module.COLUMNS:
            self.tree.heading(key, text=label, command=lambda column=key: self.sort_by(column))
            self.tree.column(key, width=width, anchor="w", stretch=(key in ("name", "path")))
        for tag, options in results_module.TAGS.items():
            self.tree.tag_configure(tag, **options)

        self.scrollbar = ttk.Scrollbar(left, orient="vertical", command=self._on_scroll)
        self.tree.configure(yscrollcommand=self._on_tree_scroll)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<MouseWheel>", self._on_wheel)

        issues_frame = ttk.LabelFrame(left, text="問題 / 未検索", padding=4)
        issues_frame.pack(fill="x", pady=(4, 0))
        self.issue_tree = ttk.Treeview(
            issues_frame, columns=("severity", "code", "path", "message"), show="headings", height=4
        )
        for key, label, width in (
            ("severity", "種別", 60),
            ("code", "コード", 120),
            ("path", "ファイル", 260),
            ("message", "内容", 420),
        ):
            self.issue_tree.heading(key, text=label)
            self.issue_tree.column(key, width=width, anchor="w", stretch=(key == "message"))
        self.issue_tree.tag_configure("issue_error", foreground="#a94442")
        self.issue_tree.tag_configure("issue_warn", foreground="#8a6d3b")
        self.issue_tree.tag_configure("issue_skip", foreground="#5a5a5a")
        self.issue_tree.pack(fill="x")

        self.preview = PreviewPane(right)
        self.preview.pack(fill="both", expand=True)

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(8, 2, 8, 6))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self._coverage_text).pack(side="left")
        ttk.Label(bar, textvariable=self._progress_text, foreground="#555").pack(side="left", padx=12)
        ttk.Label(bar, textvariable=self._status_text, foreground="#555").pack(side="right")
        ttk.Label(bar, text="外部送信なし / ローカル処理", foreground="#777").pack(side="right", padx=12)

    def _bind_keys(self) -> None:
        self.master.bind("<Control-l>", lambda _e: self.query_entry.focus_set())
        self.master.bind("<Control-b>", lambda _e: self.open_condition_builder())
        self.master.bind("<Control-d>", lambda _e: self.open_settings())
        self.master.bind("<Control-i>", lambda _e: self.open_index_status())
        self.master.bind("<Control-s>", lambda _e: self.export_csv())
        self.master.bind("<F5>", lambda _e: self.start_search())
        self.master.bind("<F3>", lambda _e: self.preview.step(1))
        self.master.bind("<Shift-F3>", lambda _e: self.preview.step(-1))
        self.master.bind("<Control-f>", lambda _e: self._focus_preview_search())
        self.master.bind("<Control-Return>", lambda _e: self.reveal_selected())
        self.master.bind("<Escape>", lambda _e: self._on_escape())
        # Space toggles 確認済み only while the result list has focus; binding it
        # on the window would fire while the user types a space in the search box.
        self.tree.bind("<space>", lambda _e: (self.toggle_confirmed(), "break")[1])
        self.master.bind("<Control-Shift-C>", lambda _e: self.toggle_confirmed())

    # ------------------------------------------------------------- search
    def _sync_state(self) -> None:
        self.state.root = self.root_var.get().strip()
        self.state.query = self.query_var.get()
        self.state.exclude_query = self.exclude_var.get()
        self.state.mode = self.mode_var.get()
        self.state.include_excel = bool(self.kind_vars["excel"].get())
        self.state.include_pdf = bool(self.kind_vars["pdf"].get())
        self.state.include_word = bool(self.kind_vars["word"].get())
        self.state.include_powerpoint = bool(self.kind_vars["ppt"].get())
        self.state.include_text = bool(self.kind_vars["text"].get())
        self.state.include_unknown_text = bool(self.kind_vars["unknown"].get())

    def start_search(self) -> None:
        self._sync_state()
        roots = self.state.roots()
        if not roots:
            messagebox.showinfo("検索場所", "検索するフォルダを指定してください。")
            return
        missing = [root for root in roots if not os.path.isdir(root)]
        if missing:
            messagebox.showerror("検索場所", "フォルダが見つかりません:\n" + "\n".join(missing))
            return
        if not self.state.combined_query().strip():
            messagebox.showinfo("検索条件", "検索語またはメタ条件を指定してください。")
            return
        if self.session is not None and not self.session.finished:
            self.session.cancel()

        for root in roots:
            self.settings.remember_root(root)
        self.root_combo.configure(values=self.settings.recent_roots)
        self.model.clear()
        self._clear_issues()
        self._render_results()
        self.preview.clear()
        self._status_text.set("")
        self.cancel_button.configure(state="normal")
        self.pause_button.configure(state="normal", text="一時停止")

        config = self.state.to_config(self.settings)
        self.session = SearchSession(
            config,
            database=self.database,
            ocr_cache=self.ocr_cache,
            ocr_available=self.ocr_status.ready,
        )
        self.session.start()

    def cancel_search(self) -> None:
        if self.session is not None:
            self.session.cancel()
            self._status_text.set("中断しています…")

    def toggle_pause(self) -> None:
        if self.session is None:
            return
        paused = not self.session.paused
        self.session.set_paused(paused)
        self.pause_button.configure(text="再開" if paused else "一時停止")
        self._status_text.set("一時停止中" if paused else "")

    def _on_escape(self) -> None:
        if self.session is not None and not self.session.finished:
            self.cancel_search()
        else:
            self.master.destroy()

    def _update_mode_hint(self) -> None:
        self.mode_hint.configure(text=MODE_DESCRIPTIONS.get(self.mode_var.get(), ""))

    # -------------------------------------------------------------- events
    def _drain_events(self) -> None:
        """Coalesced UI update: at most one redraw per interval (spec section 50)."""
        session = self.session
        needs_render = False
        if session is not None:
            processed = 0
            while processed < 2000:
                try:
                    event = session.events.get_nowait()
                except queue.Empty:
                    break
                processed += 1
                if isinstance(event, events.ResultAdded):
                    self.model.add(
                        event.result,
                        confirmed=normalized_key(event.result.path) in self.settings.confirmed_set(),
                    )
                    needs_render = True
                elif isinstance(event, events.IssueAdded):
                    self._add_issue(event.issue)
                elif isinstance(event, events.Progressed):
                    self._progress_text.set(
                        f"列挙 {event.progress.discovered:,} / 処理 {event.progress.scanned:,}"
                        f" / Hit {event.progress.hit_files:,}資料"
                    )
                elif isinstance(event, events.CoverageChanged):
                    self._coverage_text.set(event.coverage.line())
                elif isinstance(event, events.QueryFailed):
                    messagebox.showerror("検索式エラー", event.message)
                    self._status_text.set(f"検索式エラー: {event.message}")
                    self.cancel_button.configure(state="disabled")
                    self.pause_button.configure(state="disabled")
                elif isinstance(event, events.Finished):
                    self._on_finished(event.summary)
                    needs_render = True
        # Phase-2 updates only touch their own rows, so the selection and the
        # scroll position are preserved.
        self._apply_full_scan_updates()
        if needs_render:
            self._render_results()
        self.after(DRAIN_MS, self._drain_events)

    def _on_finished(self, summary) -> None:
        self.cancel_button.configure(state="disabled")
        self.pause_button.configure(state="disabled", text="一時停止")
        self._coverage_text.set(summary.coverage.line())
        self._progress_text.set("")
        elapsed = summary.elapsed
        self._status_text.set(f"{len(summary.results):,}資料 / {summary.total_hits:,}件一致 / {elapsed:.1f}秒")
        self._rebuild_facets()
        self._render_results()
        record_history(self.settings, self.state, len(summary.results), elapsed)
        self.settings.save()
        if self.settings.autosave_results and summary.results:
            path = self._autosave(summary)
            if path:
                self._status_text.set(f"{self._status_text.get()} / 自動保存: {path}")
        self._update_index_label()

    def _add_issue(self, issue) -> None:
        if self._issue_rows >= MAX_ISSUE_ROWS:
            return
        self._issue_rows += 1
        self.issue_tree.insert(
            "",
            "end",
            values=(issue.severity.value.upper(), issue.code, issue.path, issue.message),
            tags=(f"issue_{issue.severity.value}",),
        )

    def _clear_issues(self) -> None:
        for item in self.issue_tree.get_children():
            self.issue_tree.delete(item)
        self._issue_rows = 0

    # ------------------------------------------------------------- results
    def _visible_rows(self) -> list:
        rows = apply_filters(self.model.all_rows, self.state, self.settings.confirmed_set())
        matcher, _error = self.state.make_refine_matcher()
        if matcher is not None:
            filtered = []
            for row in rows:
                verdict = refine_result(row.result, matcher)
                if verdict is True:
                    filtered.append(row)
                elif verdict is None:
                    row.refine_unknown = True
                    filtered.append(row)
            rows = filtered
        return rows

    def _render_results(self) -> None:
        rows = self._visible_rows()
        self.model.apply_view(rows)
        self._fill_window(0)
        self._status_count()

    def _status_count(self) -> None:
        total = len(self.model.all_rows)
        shown = len(self.model.visible)
        if shown == total:
            self._progress_text.set(f"{total:,}資料")
        else:
            self._progress_text.set(f"{shown:,} / {total:,}資料（絞り込み中）")

    def _fill_window(self, start: int) -> None:
        total = len(self.model.visible)
        start = max(0, min(start, max(0, total - results_module.WINDOW_ROWS)))
        self._window_start = start
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, row in self.model.window(start, start + results_module.WINDOW_ROWS):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=results_module.values_for(row, self.ocr_status.ready),
                tags=results_module.row_tags(row, index),
            )
        if total:
            first = start / total
            last = min(1.0, (start + min(total, results_module.WINDOW_ROWS)) / total)
            self.scrollbar.set(first, last)

    def _on_scroll(self, *args) -> None:
        total = max(1, len(self.model.visible))
        if args[0] == "moveto":
            start = int(float(args[1]) * total)
            self._fill_window(start)
        elif args[0] == "scroll":
            amount, what = int(args[1]), args[2]
            step = results_module.WINDOW_ROWS // 2 if what == "pages" else 3
            self._fill_window(self._window_start + amount * step)

    def _on_tree_scroll(self, first: str, last: str) -> None:
        # Keep the scrollbar tied to the model, not to the visible window.
        total = max(1, len(self.model.visible))
        span = min(1.0, results_module.WINDOW_ROWS / total)
        position = self._window_start / total
        self.scrollbar.set(position, min(1.0, position + span))

    def _on_wheel(self, event) -> None:
        total = len(self.model.visible)
        if total <= results_module.WINDOW_ROWS:
            return
        delta = -3 if event.delta > 0 else 3
        self._fill_window(self._window_start + delta)

    def _refresh_row(self, index: int) -> None:
        row = self.model.row_at(index)
        if row is None or str(index) not in self.tree.get_children():
            return
        self.tree.item(
            str(index),
            values=results_module.values_for(row, self.ocr_status.ready),
            tags=results_module.row_tags(row, index),
        )

    def sort_by(self, column: str) -> None:
        if self.model.sort_column == column:
            self.model.sort_descending = not self.model.sort_descending
        else:
            self.model.sort_column = column
            self.model.sort_descending = column in ("hits", "modified", "size", "relevance")
        self.state.sort_column = self.model.sort_column
        self.state.sort_descending = self.model.sort_descending
        self._render_results()

    def _selected_result(self) -> FileResult | None:
        selection = self.tree.selection()
        if not selection:
            return None
        row = self.model.row_at(int(selection[0]))
        return row.result if row else None

    def _on_select(self, _event=None) -> None:
        result = self._selected_result()
        if result is None:
            self.preview.clear()
            return
        config = self.state.to_config(self.settings)
        self.preview.show(
            result, config, search_text=self.state.query, on_chunks=self._on_preview_chunks
        )

    def _on_preview_chunks(self, result, chunks) -> None:
        """Worker-thread callback: compute the exact hit set for one file.

        ``result`` is the file the chunks belong to (not whatever row happens to
        be selected now), so a fast A->B->C click sequence cannot write A's hits
        onto C's row.
        """
        if result.hit_count_exact:
            return
        matcher = self._current_matcher()
        if matcher is None:
            return
        from ..core.matcher import Outcome
        from ..core.models import FileEntry

        entry = FileEntry(
            path=result.path,
            size=result.size,
            mtime_ns=result.mtime_ns,
            extension=os.path.splitext(result.path)[1].lower(),
            source_type=result.source_type,
            cloud_state=result.cloud_state,
        )
        state = matcher.make_state(entry, name=result.name, directory=result.directory)
        for chunk in chunks:
            state.feed(chunk)
        if state.finish() is Outcome.ACCEPT:
            self._full_scan_queue.put(
                (
                    result.path,
                    state.hit_count,
                    state.evidence(),
                    state.matched_terms(),
                    state.displays(),
                )
            )

    def _current_matcher(self):
        from ..core.matcher import MatchOptions, QueryMatcher
        from ..core.query import parse_query

        try:
            node = parse_query(self.state.combined_query(), legacy_operator=self.state.legacy_operator)
        except Exception:
            return None
        if node is None:
            return None
        return QueryMatcher(
            node,
            MatchOptions(
                case_sensitive=self.state.case_sensitive,
                ignore_width=self.state.ignore_width,
                part_number_mode=self.state.part_number_mode,
                include_path_names=self.state.search_path_names,
            ),
        )

    def _apply_full_scan_updates(self) -> bool:
        """UI-thread application of the phase-2 results."""
        updated = False
        while True:
            try:
                path, hits, evidence, terms, displays = self._full_scan_queue.get_nowait()
            except queue.Empty:
                break
            for index, row in enumerate(self.model.visible):
                if row.result.path != path:
                    continue
                row.result.hit_count = hits
                row.result.hit_count_exact = True
                row.result.evidence = list(evidence)
                row.result.matched_terms = tuple(terms)
                row.result.displays = tuple(displays)
                self._refresh_row(index)
                updated = True
                break
        return updated

    # ------------------------------------------------------------- facets
    def _rebuild_facets(self) -> None:
        for child in self.facet_inner.winfo_children():
            child.destroy()
        counts_kind: dict[str, int] = {}
        counts_source: dict[str, int] = {}
        ocr_count = 0
        for row in self.model.all_rows:
            result = row.result
            counts_kind[result.file_kind.value] = counts_kind.get(result.file_kind.value, 0) + 1
            counts_source[result.source_type.value] = counts_source.get(result.source_type.value, 0) + 1
            if result.ocr_pages:
                ocr_count += 1
        for kind, count in sorted(counts_kind.items(), key=lambda item: -item[1]):
            label = f"{kind} {count}"
            ttk.Button(
                self.facet_inner,
                text=label,
                width=len(label) + 2,
                command=lambda value=kind: self._toggle_facet("kind", value),
            ).pack(side="left", padx=2)
        for source, count in sorted(counts_source.items(), key=lambda item: -item[1]):
            label = f"{SOURCE_LABELS.get(SourceType(source), source)} {count}"
            ttk.Button(
                self.facet_inner,
                text=label,
                width=len(label) + 2,
                command=lambda value=source: self._toggle_facet("source", value),
            ).pack(side="left", padx=2)
        if ocr_count:
            ttk.Button(
                self.facet_inner,
                text=f"OCRのみ {ocr_count}",
                command=self._toggle_ocr_facet,
            ).pack(side="left", padx=2)
        ttk.Button(self.facet_inner, text="解除", command=self.clear_facets).pack(side="left", padx=6)

    def _toggle_facet(self, group: str, value: str) -> None:
        target = self.state.facet_kinds if group == "kind" else self.state.facet_sources
        if value in target:
            target.discard(value)
        else:
            target.add(value)
        self._sync_state()
        self._render_results()

    def _toggle_ocr_facet(self) -> None:
        self.state.facet_ocr = not self.state.facet_ocr
        self._render_results()

    def clear_facets(self) -> None:
        self.state.facet_kinds.clear()
        self.state.facet_sources.clear()
        self.state.facet_ocr = False
        self.state.confirmed_only = False
        self.state.unconfirmed_only = False
        self._render_results()

    def apply_refine(self) -> None:
        self.state.refine_text = self._refine_var.get()
        matcher, error = self.state.make_refine_matcher()
        if error:
            messagebox.showerror("絞り込み条件のエラー", error)
            return
        self._render_results()
        if matcher is not None and not self._index_active():
            self._status_text.set(
                "索引OFFのため、絞り込みは表示中の根拠テキストとファイル名のみを対象にします"
            )

    def clear_refine(self) -> None:
        self._refine_var.set("")
        self.state.refine_text = ""
        self._render_results()

    # ------------------------------------------------------------ actions
    def open_selected(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        ok, error = shell.open_path(result.path)
        if not ok:
            messagebox.showerror("開けません", error)

    def reveal_selected(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        ok, error = shell.reveal_in_explorer(result.path)
        if not ok:
            messagebox.showerror("開けません", error)

    def open_excel_cell(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        if result.file_kind is not FileKind.EXCEL:
            self.open_selected()
            return
        sheet, cell = "", ""
        for evidence in result.evidence:
            if "!" in evidence.location:
                sheet, _, cell = evidence.location.partition("!")
                break
        ok, error = shell.open_excel_cell(result.path, sheet, cell)
        if not ok:
            # Fall back to opening the file so the user is never stuck.
            self.open_selected()
            if error:
                self._status_text.set(f"Excelセル表示に失敗: {error}")

    def copy_path(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(result.path)
        self._status_text.set("パスをコピーしました")

    def copy_content(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(results_module.copy_text_for(result))
        self._status_text.set("根拠テキストをコピーしました")

    def toggle_confirmed(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        row = self.model.row_at(index)
        if row is None:
            return
        key = normalized_key(row.result.path)
        confirmed = set(self.settings.confirmed_paths)
        if key in self.settings.confirmed_set():
            confirmed = {path for path in confirmed if normalized_key(path) != key}
            row.confirmed = False
        else:
            confirmed.add(row.result.path)
            row.confirmed = True
        self.settings.confirmed_paths = sorted(confirmed)
        self.settings.save()
        self._refresh_row(index)

    def exclude_selected(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        rows = [row for row in self.model.all_rows if row.result.path != result.path]
        self.model.all_rows = rows
        self._render_results()

    def search_this_folder(self) -> None:
        result = self._selected_result()
        if result is None:
            return
        self.root_var.set(result.directory)
        self._sync_state()
        self.start_search()

    def _show_context_menu(self, event) -> None:
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="開く", command=self.open_selected)
        menu.add_command(label="フォルダを開く", command=self.reveal_selected)
        menu.add_command(label="Excelの該当セルを開く", command=self.open_excel_cell)
        menu.add_separator()
        menu.add_command(label="Copy Path", command=self.copy_path)
        menu.add_command(label="Copy Content", command=self.copy_content)
        menu.add_separator()
        menu.add_command(label="確認済みを切り替え", command=self.toggle_confirmed)
        menu.add_command(label="結果から除外", command=self.exclude_selected)
        menu.add_separator()
        menu.add_command(label="このフォルダだけ再検索", command=self.search_this_folder)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------- dialogs & IO
    def choose_root(self) -> None:
        initial = self.root_var.get() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=initial, title="検索するフォルダ")
        if chosen:
            self.root_var.set(os.path.normpath(chosen))
            self._sync_state()
            self.settings.remember_root(chosen)
            self.root_combo.configure(values=self.settings.recent_roots)

    def open_condition_builder(self) -> None:
        dialog = dialogs.ConditionBuilderDialog(
            self.master, initial=self.query_var.get(), legacy_operator=self.state.legacy_operator
        )
        query = dialog.show()
        if query:
            self.query_var.set(query)
            self._sync_state()
            self.query_entry.focus_set()

    def open_settings(self) -> None:
        dialog = dialogs.AdvancedSettingsDialog(
            self.master, state=self.state, settings=self.settings, ocr_message=self.ocr_status.message
        )
        if dialog.show():
            self.mode_var.set(self.state.mode)
            self._update_mode_hint()
            self._sync_state()
            self._open_index()
            self._update_index_label()

    def open_index_status(self) -> None:
        dialogs.IndexStatusDialog(self.master, database=self.database, roots=(self.state.root,)).show()
        self._update_index_label()

    def open_diagnostics(self) -> None:
        size = self.database.size_bytes() if self.database is not None else 0
        files = 0
        if self.database is not None:
            try:
                files = self.database.known_count()
            except Exception:
                files = 0
        dialogs.DiagnosticsDialog(
            self.master,
            index_path=paths.index_path(),
            index_size=size,
            index_files=files,
        ).show()

    def open_shortcuts(self) -> None:
        dialogs.ShortcutsDialog(self.master).show()

    def show_privacy(self) -> None:
        messagebox.showinfo(
            "Privacy",
            "FileScopeは検索語や本文を外部のAI/API/クラウドへ送信しません。\n"
            "OCRも全文索引もこのPC内で処理します。\n"
            "OneDrive/SharePoint同期フォルダでは、Windows/OneDrive自身がファイルを取得する通信が発生します。\n"
            f"設定・索引・ログ: {paths.data_dir()}",
        )

    def save_preset(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("プリセット保存", "プリセット名を入力してください", parent=self.master)
        if not name:
            return
        self._sync_state()
        self.settings.presets = [
            preset for preset in self.settings.presets if preset.get("name") != name
        ]
        self.settings.presets.append(preset_from_state(self.state, name))
        self.settings.save()
        self._status_text.set(f"プリセット「{name}」を保存しました")

    def open_presets(self) -> None:
        entries = [(preset.get("name", "?"), preset) for preset in self.settings.presets]
        dialog = dialogs.ListDialog(
            self.master, title="プリセット", entries=entries, empty_message="プリセットがありません"
        )
        preset = dialog.show()
        if preset:
            apply_preset(self.state, preset)
            self._apply_state_to_form()
            self._status_text.set(f"プリセット「{preset.get('name')}」を読み込みました")

    def open_history(self) -> None:
        entries = [
            (
                f"{entry.get('at', '')}  {entry.get('query', '')}  ({entry.get('hits', 0)}件)"
                f"  {entry.get('mode', '')}",
                entry,
            )
            for entry in self.settings.history
        ]
        dialog = dialogs.ListDialog(
            self.master, title="検索履歴", entries=entries, empty_message="履歴がありません"
        )
        entry = dialog.show()
        if entry:
            self.query_var.set(entry.get("query", ""))
            if entry.get("root"):
                self.root_var.set(entry["root"])
            self._sync_state()
            self.start_search()

    def _apply_state_to_form(self) -> None:
        self.root_var.set(self.state.root)
        self.query_var.set(self.state.query)
        self.exclude_var.set(self.state.exclude_query)
        self.mode_var.set(self.state.mode)
        self.kind_vars["excel"].set(self.state.include_excel)
        self.kind_vars["pdf"].set(self.state.include_pdf)
        self.kind_vars["word"].set(self.state.include_word)
        self.kind_vars["ppt"].set(self.state.include_powerpoint)
        self.kind_vars["text"].set(self.state.include_text)
        self.kind_vars["unknown"].set(self.state.include_unknown_text)
        self._update_mode_hint()

    def _focus_preview_search(self) -> None:
        from tkinter import simpledialog

        term = simpledialog.askstring("プレビュー内検索", "プレビュー内で検索する語", parent=self.master)
        if not term:
            return
        result = self._selected_result()
        if result is None:
            return
        self.preview.show(result, self.state.to_config(self.settings), search_text=term)

    # ------------------------------------------------------------ exports
    def export_csv(self) -> None:
        if not self.model.visible:
            messagebox.showinfo("CSV保存", "結果がありません。")
            return
        path = filedialog.asksaveasfilename(
            title="結果をCSV保存",
            defaultextension=".csv",
            initialfile="filescope_results.csv",
            filetypes=[("CSV", "*.csv"), ("すべて", "*.*")],
        )
        if not path:
            return
        if write_results_csv(path, self.model.visible):
            self._status_text.set(f"CSV保存: {path}")
        else:
            messagebox.showerror("CSV保存", "保存に失敗しました。")

    def _autosave(self, summary) -> str:
        try:
            directory = paths.session_dir()
            stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
            path = os.path.join(directory, f"results-{stamp}.csv")
            rows = [results_module.Row(result=r, confirmed=False) for r in summary.results]
            if write_results_csv(path, rows[:100000]):
                return path
        except OSError as exc:
            log.debug("autosave failed: %s", exc)
        return ""

    # -------------------------------------------------------------- index
    def _open_index(self) -> None:
        if not self.settings.index_enabled or self.settings.index_max_bytes <= 0:
            if self.database is not None:
                self.database.close()
                self.database = None
            return
        if self.database is not None:
            return
        try:
            self.database = IndexDatabase(paths.index_path(), max_bytes=self.settings.index_max_bytes)
        except Exception as exc:
            log.warning("index unavailable: %s", exc)
            self.settings.index_disabled_reason = str(exc)
            self.database = None
            self.settings.save()

    def _index_active(self) -> bool:
        return self.database is not None

    def _update_index_label(self) -> None:
        if self.database is None:
            reason = self.settings.index_disabled_reason
            self._index_text.set(f"索引: OFF{'' if not reason else '（' + reason[:40] + '）'}")
            return
        try:
            status = self.database.status()
        except Exception as exc:
            self._index_text.set(f"索引: エラー（{type(exc).__name__}）")
            return
        self._index_text.set(
            f"索引: {status.files:,}ファイル / {human_size(status.size_bytes)}"
            + ("（容量上限で新規停止）" if status.message else "")
        )

    def on_close(self) -> None:
        try:
            if self.session is not None and not self.session.finished:
                self.session.cancel()
                self.session.wait(5)
            self._sync_state()
            self.state.save_to(self.settings)
            self.settings.save()
            if self.database is not None:
                self.database.close()
            self.ocr_cache.close()
            purge_stale()
        finally:
            self.master.destroy()


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def write_results_csv(path: str, rows) -> bool:
    """File-centric CSV with the evidence for each matched condition."""
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["済", "ファイル", "一致条件", "Hit数", "種類", "更新日時", "サイズ",
                 "Source", "OCR", "場所", "根拠", "Path"]
            )
            for row in rows:
                result = row.result
                evidence = result.evidence[0] if result.evidence else None
                writer.writerow(
                    [
                        "✓" if row.confirmed else "",
                        result.name,
                        " / ".join(result.displays),
                        result.hit_count,
                        result.file_kind.value,
                        results_module.format_time(result.mtime_ns),
                        results_module.format_size(result.size),
                        f"{SOURCE_LABELS.get(result.source_type, '?')} {CLOUD_LABELS.get(result.cloud_state, '')}".strip(),
                        result.ocr_pages or "",
                        evidence.location if evidence else "",
                        evidence.snippet if evidence else "",
                        result.path,
                    ]
                )
        return True
    except OSError as exc:
        log.warning("csv export failed: %s", exc)
        return False


_ = (OCR_MODE_LABELS, ONLINE_POLICY_LABELS, collect_diagnostics)
