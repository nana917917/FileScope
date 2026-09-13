"""Secondary windows: condition builder, settings, diagnostics, index manager,
presets/history and the shortcut list."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import diagnostics, paths
from ..config import (
    INDEX_SIZE_CHOICES,
    MODE_DESCRIPTIONS,
    OCR_MODE_LABELS,
    ONLINE_POLICY_LABELS,
    Settings,
)
from ..core.query import BuilderGroups, parse_query, to_query_string
from ..errors import QuerySyntaxError


class Dialog(ttk.Frame):
    """Common modal window plumbing."""

    def __init__(self, parent, title: str, *, width: int = 640, height: int = 480) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.transient(parent)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(320, 240)
        super().__init__(self.window)
        self.pack(fill="both", expand=True)
        self.result = None

    def show(self, *, modal: bool = True):
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.bind("<Escape>", lambda _event: self._cancel())
        if modal:
            self.window.grab_set()
        self.window.wait_window()
        return self.result

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def _close(self, value=None) -> None:
        self.result = value
        self.window.destroy()


class ConditionBuilderDialog(Dialog):
    """Build AND / OR / N-of-M / exclude / phrase / proximity conditions."""

    def __init__(self, parent, *, initial: str = "", legacy_operator: str = "OR") -> None:
        super().__init__(parent, "条件を作る", width=720, height=620)
        self.legacy_operator = legacy_operator
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="入力した条件は検索欄の検索式に変換されます。検索欄では直接入力もできます。",
            wraplength=660,
        ).pack(anchor="w", pady=(0, 8))

        self.fields: dict[str, tk.Text] = {}
        self.all_of = self._multiline(body, "すべて含む (AND)", 3)
        self.any_of = self._multiline(body, "いずれか含む (OR)", 3)
        self.none_of = self._multiline(body, "含まない (NOT)", 2)

        count_row = ttk.Frame(body)
        count_row.pack(fill="x", pady=4)
        ttk.Label(count_row, text="いくつ含むか").pack(side="left")
        self.count_var = tk.StringVar(value="2")
        ttk.Spinbox(count_row, from_=1, to=9, width=4, textvariable=self.count_var).pack(side="left", padx=4)
        ttk.Label(count_row, text="個（N of M）").pack(side="left")
        self.n_of = self._multiline(body, "N個中いくつ含むか (2of(A,B,C))", 3)

        self.phrase = self._single(body, "完全一致フレーズ (\"耐久 試験\")")
        near_row = ttk.Frame(body)
        near_row.pack(fill="x", pady=4)
        ttk.Label(near_row, text="近くにある").pack(side="left")
        self.near_a = ttk.Entry(near_row, width=16)
        self.near_a.pack(side="left", padx=2)
        self.near_b = ttk.Entry(near_row, width=16)
        self.near_b.pack(side="left", padx=2)
        self.near_distance = ttk.Entry(near_row, width=6)
        self.near_distance.insert(0, "100")
        self.near_distance.pack(side="left")
        ttk.Label(near_row, text="文字以内（同一セル/ページ）").pack(side="left", padx=4)

        self.metadata = self._single(body, "メタ条件 (例: type:pdf size:<50MB modified:>=2025-01-01)")
        if initial.strip():
            self.all_of.insert("1.0", initial.strip())

        preview_row = ttk.Frame(body)
        preview_row.pack(fill="x", pady=(8, 2))
        ttk.Label(preview_row, text="検索式プレビュー").pack(side="left")
        ttk.Button(preview_row, text="更新", command=self._preview).pack(side="right")
        self.preview_var = tk.StringVar(value="")
        ttk.Entry(body, textvariable=self.preview_var, state="readonly").pack(fill="x")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="この条件を使う", command=self._accept).pack(side="right", padx=4)
        ttk.Button(buttons, text="キャンセル", command=self._cancel).pack(side="right")
        self._preview()

    def _multiline(self, parent, label: str, height: int) -> tk.Text:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(6, 0))
        widget = tk.Text(parent, height=height, wrap="word")
        widget.pack(fill="x")
        return widget

    def _single(self, parent, label: str) -> ttk.Entry:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(6, 0))
        entry = ttk.Entry(parent)
        entry.pack(fill="x")
        return entry

    def _lines(self, widget: tk.Text) -> list[str]:
        return [line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip()]

    def _build_query(self) -> tuple[str, str]:
        groups = BuilderGroups(
            all_of=self._lines(self.all_of),
            any_of=self._lines(self.any_of),
            n_of_count=int(self.count_var.get() or 0),
            n_of=self._lines(self.n_of),
            none_of=self._lines(self.none_of),
            phrases=[self.phrase.get()] if self.phrase.get().strip() else [],
            near_a=self.near_a.get(),
            near_b=self.near_b.get(),
            near_distance=int(self.near_distance.get() or 100),
        )
        metadata_text = self.metadata.get().strip()
        node = groups.build()
        query = to_query_string(node) if node is not None else ""
        if metadata_text:
            try:
                meta_node = parse_query(metadata_text, legacy_operator=self.legacy_operator)
            except QuerySyntaxError as exc:
                return "", f"メタ条件のエラー: {exc.message}"
            meta_query = to_query_string(meta_node) if meta_node is not None else ""
            query = f"({query}) & {meta_query}" if query else meta_query
        return query, ""

    def _preview(self) -> None:
        query, error = self._build_query()
        self.preview_var.set(error or query)

    def _accept(self) -> None:
        query, error = self._build_query()
        if error:
            messagebox.showerror("条件エラー", error, parent=self.window)
            return
        if not query:
            messagebox.showinfo("条件が空です", "条件を1つ以上入力してください。", parent=self.window)
            return
        self._close(query)


class AdvancedSettingsDialog(Dialog):
    """Advanced options (spec sections 7, 20, 28, 30, 60, 68)."""

    def __init__(self, parent, *, state, settings: Settings, ocr_message: str = "") -> None:
        super().__init__(parent, "詳細設定", width=640, height=640)
        self.state = state
        self.settings = settings
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        self.mode_var = tk.StringVar(value=state.mode)
        self.operator_var = tk.StringVar(value=state.legacy_operator)
        self.ocr_var = tk.StringVar(value=state.pdf_ocr_mode)
        self.online_var = tk.StringVar(value=state.online_files_policy)
        self.case_var = tk.BooleanVar(value=state.case_sensitive)
        self.width_var = tk.BooleanVar(value=state.ignore_width)
        self.part_var = tk.BooleanVar(value=state.part_number_mode)
        self.path_names_var = tk.BooleanVar(value=state.search_path_names)
        self.formula_var = tk.BooleanVar(value=state.search_formula)
        self.archives_var = tk.BooleanVar(value=state.include_archives)
        self.history_var = tk.BooleanVar(value=settings.history_enabled)
        self.autosave_var = tk.BooleanVar(value=settings.autosave_results)
        self.subfolders_var = tk.BooleanVar(value=state.include_subfolders)
        self.index_enabled_var = tk.BooleanVar(value=settings.index_enabled and settings.index_max_bytes > 0)
        self.workers_var = tk.StringVar(value=str(state.workers))
        self.index_size_var = tk.StringVar(value=_index_size_label(settings.index_max_bytes))
        self.limits_vars = {
            "office_max_bytes": tk.StringVar(value=str(settings.limits.office_max_bytes // (1024 * 1024))),
            "pdf_max_bytes": tk.StringVar(value=str(settings.limits.pdf_max_bytes // (1024 * 1024))),
            "text_max_bytes": tk.StringVar(value=str(settings.limits.text_max_bytes // (1024 * 1024))),
        }

        row = 0
        ttk.Label(body, text="検索モード", font=("", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 2))
        mode_frame = ttk.Frame(body)
        mode_frame.grid(row=row, column=1, sticky="w")
        for mode in ("fast", "standard", "full"):
            ttk.Radiobutton(
                mode_frame, text=_mode_label(mode), value=mode, variable=self.mode_var
            ).pack(side="left", padx=3)
        row += 1
        ttk.Label(body, text=MODE_DESCRIPTIONS["standard"], wraplength=520, foreground="#555").grid(
            row=row, column=1, sticky="w", pady=(0, 8)
        )
        row += 1

        self._radio_row(body, row, "空白区切りの既定", self.operator_var, [("OR", "OR"), ("AND", "AND")])
        row += 1
        self._radio_row(
            body,
            row,
            "PDF OCR",
            self.ocr_var,
            [(key, label) for key, label in OCR_MODE_LABELS.items()],
        )
        row += 1
        ttk.Label(body, text=ocr_message, foreground="#555", wraplength=520).grid(row=row, column=1, sticky="w")
        row += 1
        self._radio_row(
            body,
            row,
            "オンライン専用ファイル",
            self.online_var,
            [(key, label) for key, label in ONLINE_POLICY_LABELS.items()],
        )
        row += 1

        for label, variable in (
            ("子フォルダも検索", self.subfolders_var),
            ("大文字小文字を区別", self.case_var),
            ("全角/半角を吸収", self.width_var),
            ("部品番号モード（-・空白を吸収、*可）", self.part_var),
            ("ファイル名・フォルダ名も検索", self.path_names_var),
            ("Excel数式を検索", self.formula_var),
            ("ZIP内も検索（任意）", self.archives_var),
            ("検索履歴を保存", self.history_var),
            ("結果をTEMPへ自動保存", self.autosave_var),
        ):
            ttk.Checkbutton(body, text=label, variable=variable).grid(row=row, column=0, columnspan=2, sticky="w")
            row += 1

        ttk.Label(body, text="同時処理数（一般）").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(body, from_=1, to=16, width=6, textvariable=self.workers_var).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(body, text="インデックス").grid(row=row, column=0, sticky="w", pady=(8, 0))
        index_frame = ttk.Frame(body)
        index_frame.grid(row=row, column=1, sticky="w")
        ttk.Checkbutton(index_frame, text="使用する", variable=self.index_enabled_var).pack(side="left")
        ttk.Combobox(
            index_frame,
            width=10,
            state="readonly",
            values=[label for label, _ in INDEX_SIZE_CHOICES] + ["カスタム"],
            textvariable=self.index_size_var,
        ).pack(side="left", padx=4)
        row += 1
        ttk.Label(
            body,
            text="容量上限に達しても古い索引は削除しません（新規作成のみ停止します）。",
            foreground="#555",
        ).grid(row=row, column=1, sticky="w")
        row += 1

        for label, key in (
            ("Office 最大サイズ (MB)", "office_max_bytes"),
            ("PDF 最大サイズ (MB)", "pdf_max_bytes"),
            ("テキスト 最大サイズ (MB)", "text_max_bytes"),
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(body, width=10, textvariable=self.limits_vars[key]).grid(row=row, column=1, sticky="w")
            row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=10)
        ttk.Button(buttons, text="OK", command=self._accept).pack(side="right", padx=4)
        ttk.Button(buttons, text="キャンセル", command=self._cancel).pack(side="right")
        ttk.Label(
            body,
            text=f"設定ファイル: {paths.settings_path()}",
            foreground="#777",
            wraplength=520,
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w")

    def _radio_row(self, parent, row: int, label: str, variable, choices) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(4, 0))
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="w")
        for value, text in choices:
            ttk.Radiobutton(frame, text=text, value=value, variable=variable).pack(side="left", padx=3)

    def _accept(self) -> None:
        state = self.state
        state.mode = self.mode_var.get()
        state.legacy_operator = self.operator_var.get()
        state.pdf_ocr_mode = self.ocr_var.get()
        state.online_files_policy = self.online_var.get()
        state.case_sensitive = bool(self.case_var.get())
        state.include_subfolders = bool(self.subfolders_var.get())
        state.ignore_width = bool(self.width_var.get())
        state.part_number_mode = bool(self.part_var.get())
        state.search_path_names = bool(self.path_names_var.get())
        state.search_formula = bool(self.formula_var.get())
        state.include_archives = bool(self.archives_var.get())
        try:
            state.workers = max(1, min(16, int(self.workers_var.get())))
        except ValueError:
            state.workers = 4
        self.settings.history_enabled = bool(self.history_var.get())
        self.settings.autosave_results = bool(self.autosave_var.get())
        self.settings.index_enabled = bool(self.index_enabled_var.get())
        self.settings.index_max_bytes = _index_size_value(self.index_size_var.get(), self.settings)
        for key, variable in self.limits_vars.items():
            try:
                setattr(self.settings.limits, key, max(1, int(variable.get())) * 1024 * 1024)
            except ValueError:
                continue
        self.settings.save()
        self._close(True)


def _mode_label(mode: str) -> str:
    from ..config import MODE_LABELS

    return MODE_LABELS.get(mode, mode)


def _index_size_label(value: int) -> str:
    for label, size in INDEX_SIZE_CHOICES:
        if size == value:
            return label
    return "カスタム"


def _index_size_value(label: str, settings: Settings) -> int:
    for candidate, size in INDEX_SIZE_CHOICES:
        if candidate == label:
            return size
    return settings.index_max_bytes or 1024 * 1024 * 1024


class DiagnosticsDialog(Dialog):
    def __init__(self, parent, *, index_path: str = "", index_size: int = 0, index_files: int = 0) -> None:
        super().__init__(parent, "診断情報", width=720, height=560)
        self.report = diagnostics.collect(
            index_path=index_path, index_size=index_size, index_files=index_files
        )
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="環境と依存関係").pack(anchor="w")
        self.text = tk.Text(body, wrap="none", height=22)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.insert("1.0", self.report.as_text())
        self.text.configure(state="disabled")
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=8)
        ttk.Button(buttons, text="全部コピー", command=self._copy).pack(side="right")
        ttk.Button(buttons, text="閉じる", command=self._cancel).pack(side="right", padx=4)

    def _copy(self) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(self.report.as_text())
        messagebox.showinfo("コピーしました", "診断情報をクリップボードへコピーしました。", parent=self.window)


class IndexStatusDialog(Dialog):
    """Index coverage and maintenance actions (spec section 66)."""

    def __init__(self, parent, *, database, roots: tuple[str, ...] = ()) -> None:
        super().__init__(parent, "索引の状態", width=700, height=420)
        self.database = database
        self.roots = roots
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.info = tk.Text(body, wrap="word", height=12)
        self.info.pack(fill="both", expand=True)
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=8)
        ttk.Button(actions, text="再構築（FTS再作成）", command=self._rebuild).pack(side="left", padx=2)
        ttk.Button(actions, text="このフォルダの索引を削除", command=self._remove_root).pack(side="left", padx=2)
        ttk.Button(actions, text="すべて削除", command=self._clear).pack(side="left", padx=2)
        ttk.Button(actions, text="閉じる", command=self._cancel).pack(side="right")
        self._refresh()

    def _refresh(self) -> None:
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        if self.database is None:
            self.info.insert("end", "索引は無効です（設定で有効にできます）。\n")
        else:
            try:
                status = self.database.status()
                self.info.insert("end", f"保存先: {status.path}\n")
                self.info.insert("end", f"登録ファイル: {status.files:,}\n")
                self.info.insert("end", f"チャンク: {status.chunks:,}\n")
                self.info.insert("end", f"サイズ: {_human(status.size_bytes)}\n")
                self.info.insert("end", f"FTS5: {'OK' if status.fts5 else '不可'} / trigram: {'OK' if status.trigram else '不可'}\n")
                self.info.insert("end", f"部分索引（本文を保存できなかったファイル）: {status.truncated_files:,}\n")
                if status.last_scan:
                    import time as _time

                    self.info.insert("end", f"最終更新: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(status.last_scan))}\n")
                if status.message:
                    self.info.insert("end", f"\n{status.message}\n")
            except Exception as exc:
                self.info.insert("end", f"索引情報を取得できません: {type(exc).__name__}\n")
        self.info.insert(
            "end",
            "\n索引は検索結果を絞り込むためだけに使われ、判定は本文と同じ照合ロジックで行います。\n"
            "索引が壊れている・古い場合は自動でDirect検索へ切り替わります。\n",
        )
        self.info.configure(state="disabled")

    def _rebuild(self) -> None:
        if self.database is None:
            return
        try:
            self.database.rebuild_fts()
            messagebox.showinfo("完了", "FTS索引を再構築しました。", parent=self.window)
        except Exception as exc:
            messagebox.showerror("失敗", f"再構築に失敗しました: {exc}", parent=self.window)
        self._refresh()

    def _remove_root(self) -> None:
        if self.database is None or not self.roots:
            return
        root = self.roots[0]
        if not messagebox.askyesno("確認", f"次のフォルダの索引を削除します。\n{root}", parent=self.window):
            return
        removed = self.database.remove_root(root)
        messagebox.showinfo("完了", f"{removed:,} 件の索引を削除しました。", parent=self.window)
        self._refresh()

    def _clear(self) -> None:
        if self.database is None:
            return
        if not messagebox.askyesno("確認", "すべての索引を削除します。よろしいですか？", parent=self.window):
            return
        self.database.clear()
        messagebox.showinfo("完了", "索引を削除しました。", parent=self.window)
        self._refresh()


class ListDialog(Dialog):
    """Presets / history picker."""

    def __init__(self, parent, *, title: str, entries: list[tuple[str, object]], empty_message: str) -> None:
        super().__init__(parent, title, width=520, height=380)
        self.entries = entries
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, height=14)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        for label, _value in entries:
            self.listbox.insert("end", label)
        if not entries:
            self.listbox.insert("end", empty_message)
            self.listbox.configure(state="disabled")
        self.listbox.bind("<Double-Button-1>", lambda _event: self._accept())
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=8)
        ttk.Button(buttons, text="使う", command=self._accept).pack(side="right")
        ttk.Button(buttons, text="閉じる", command=self._cancel).pack(side="right", padx=4)

    def _accept(self) -> None:
        if not self.entries:
            return
        selection = self.listbox.curselection()
        if not selection:
            return
        self._close(self.entries[selection[0]][1])


class ShortcutsDialog(Dialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "キーボードショートカット", width=480, height=380)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        rows = [
            ("Ctrl+L", "検索欄へ移動"),
            ("Enter", "検索実行 / 選択ファイルを開く"),
            ("Ctrl+Enter", "選択ファイルのフォルダを開く"),
            ("Ctrl+F", "プレビュー内を検索"),
            ("F3 / Shift+F3", "次の一致 / 前の一致へ移動"),
            ("Ctrl+S", "結果をCSV保存"),
            ("Esc", "検索中断 / ウインドウを閉じる"),
            ("Ctrl+B", "条件ビルダーを開く"),
            ("Ctrl+D", "詳細設定を開く"),
            ("Ctrl+I", "索引の状態を表示"),
            ("F5", "再検索"),
            ("Space", "確認済みの切り替え"),
        ]
        for index, (key, description) in enumerate(rows):
            ttk.Label(body, text=key, font=("", 9, "bold")).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Label(body, text=description).grid(row=index, column=1, sticky="w", padx=12)
        ttk.Button(body, text="閉じる", command=self._cancel).grid(row=len(rows) + 1, column=1, sticky="e", pady=10)


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"
