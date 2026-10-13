"""Secondary windows: condition builder, settings, diagnostics, index manager,
presets/history and the shortcut list."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import ClassVar

from .. import diagnostics, paths
from ..config import (
    INDEX_SIZE_CHOICES,
    MODE_DESCRIPTIONS,
    OCR_MODE_LABELS,
    ONLINE_POLICY_LABELS,
    Settings,
)
from ..core.query import (
    TYPE_EXTENSION_MAP,
    BuilderGroups,
    Meta,
    parse_into_groups,
    parse_query,
    to_query_string,
)
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
    """Build search conditions without having to know the query syntax.

    The dialog round-trips: opening it with an existing expression fills the
    fields (anything it cannot express is kept verbatim in 詳細式), and applying
    it produces an expression with the same meaning.
    """

    TYPE_EXTENSIONS: ClassVar[dict[str, tuple[str, ...]]] = TYPE_EXTENSION_MAP

    def __init__(self, parent, *, initial: str = "", legacy_operator: str = "OR") -> None:
        super().__init__(parent, "条件を作る", width=900, height=620)
        self.legacy_operator = legacy_operator
        groups = BuilderGroups()
        self._metadata_rest: list[str] = []
        if initial.strip():
            try:
                groups = parse_into_groups(
                    parse_query(initial, legacy_operator=legacy_operator)
                )
            except QuerySyntaxError:
                # An expression that does not parse is kept as text so the user
                # can repair it instead of losing it.
                groups.advanced = initial.strip()

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="検索式を覚えなくても大丈夫です。各欄に1行ずつ入力してください（引用符は自動で処理します）。",
        ).pack(anchor="w")

        columns = ttk.Frame(body)
        columns.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = ttk.Frame(columns)
        right.pack(side="left", fill="both", expand=True, padx=(0, 6))

        self.all_of = self._multiline(
            left, "すべて含む", "入れた言葉が全部ある資料（例: 電源 / ノイズ）", 4
        )
        self.any_of = self._multiline(
            left, "どれか含む", "1つでもあればOK（例: 耐久 / 落下）", 3
        )
        self.none_of = self._multiline(
            left, "含まない", "この言葉がある資料は除外（例: 旧版）", 2
        )

        n_frame = ttk.LabelFrame(right, text="N個以上含む", padding=6)
        n_frame.pack(fill="x", pady=(0, 6))
        count_row = ttk.Frame(n_frame)
        count_row.pack(fill="x")
        ttk.Label(count_row, text="候補のうち").pack(side="left")
        self.count_var = tk.StringVar(value=str(groups.n_of_count or 2))
        ttk.Spinbox(count_row, from_=1, to=20, width=3, textvariable=self.count_var).pack(side="left", padx=4)
        ttk.Label(count_row, text="個以上").pack(side="left")
        ttk.Label(n_frame, text="例: 3つ中2つ以上あればヒット", foreground="#666").pack(anchor="w")
        self.n_of = tk.Text(n_frame, height=3, width=24, wrap="word")
        self.n_of.pack(fill="x", pady=(4, 0))

        self.phrase = self._entry(right, "完全一致", "入力した並びで探す（例: 耐久 試験）")

        near_frame = ttk.LabelFrame(right, text="近くにある", padding=6)
        near_frame.pack(fill="x", pady=(6, 0))
        near_row = ttk.Frame(near_frame)
        near_row.pack(fill="x")
        self.near_a = ttk.Entry(near_row, width=10)
        self.near_a.pack(side="left")
        ttk.Label(near_row, text="と").pack(side="left", padx=3)
        self.near_b = ttk.Entry(near_row, width=10)
        self.near_b.pack(side="left")
        self.near_distance = ttk.Entry(near_row, width=4)
        self.near_distance.pack(side="left", padx=(6, 2))
        ttk.Label(near_row, text="文字以内").pack(side="left")
        ttk.Label(near_frame, text="同じセル・ページ内で近くにある資料", foreground="#666").pack(anchor="w")

        self._build_file_conditions(body, groups)
        self._build_advanced(body, groups)

        preview_row = ttk.Frame(body)
        preview_row.pack(fill="x", pady=(8, 2))
        ttk.Label(preview_row, text="この条件で検索される式").pack(side="left")
        ttk.Button(preview_row, text="更新", command=self._preview).pack(side="right")
        self.preview_var = tk.StringVar(value="")
        ttk.Entry(body, textvariable=self.preview_var, state="readonly").pack(fill="x")
        self.error_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.error_var, foreground="#a94442").pack(anchor="w")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="この条件を使う", command=self._accept).pack(side="right", padx=4)
        ttk.Button(buttons, text="条件をクリア", command=self._clear).pack(side="right", padx=4)
        ttk.Button(buttons, text="キャンセル", command=self._cancel).pack(side="right")

        self._apply_groups(groups)
        self._preview()
        for widget in (self.all_of, self.any_of, self.none_of, self.n_of):
            widget.bind("<KeyRelease>", lambda _event: self._preview())
        for entry in (self.phrase, self.near_a, self.near_b, self.name_entry, self.size_value, self.modified_value):
            entry.bind("<KeyRelease>", lambda _event: self._preview())

    # ----------------------------------------------------------- widgets
    def _multiline(self, parent, label: str, hint: str, height: int) -> tk.Text:
        ttk.Label(parent, text=label, font=("", 9, "bold")).pack(anchor="w", pady=(6, 0))
        # An explicit small width keeps the requested size down so both columns
        # stay inside the dialog; the widget stretches with fill="x" anyway.
        widget = tk.Text(parent, height=height, width=28, wrap="word")
        widget.pack(fill="x")
        ttk.Label(parent, text=hint, foreground="#666").pack(anchor="w")
        return widget

    def _entry(self, parent, label: str, hint: str) -> ttk.Entry:
        ttk.Label(parent, text=label, font=("", 9, "bold")).pack(anchor="w", pady=(6, 0))
        entry = ttk.Entry(parent)
        entry.pack(fill="x")
        ttk.Label(parent, text=hint, foreground="#666").pack(anchor="w")
        return entry

    def _build_file_conditions(self, parent, groups: BuilderGroups) -> None:
        self.file_frame = ttk.LabelFrame(parent, text="ファイル条件（任意）", padding=6)
        self.file_frame.pack(fill="x", pady=(8, 0))
        toggle = ttk.Button(
            self.file_frame, text="▼ 表示中", width=10, command=self._toggle_file_conditions
        )
        toggle.pack(anchor="w")
        self._file_toggle = toggle
        self.file_body = ttk.Frame(self.file_frame)
        self.file_body.pack(fill="x", pady=(4, 0))

        types_row = ttk.Frame(self.file_body)
        types_row.pack(fill="x")
        ttk.Label(types_row, text="種類").pack(side="left", padx=(0, 6))
        self.type_vars = {
            key: tk.BooleanVar(value=False)
            for key in ("pdf", "excel", "word", "ppt", "text", "archive")
        }
        for key, label in (
            ("pdf", "PDF"),
            ("excel", "Excel"),
            ("word", "Word"),
            ("ppt", "PPT"),
            ("text", "Text"),
            ("archive", "ZIP"),
        ):
            ttk.Checkbutton(types_row, text=label, variable=self.type_vars[key]).pack(side="left")

        name_row = ttk.Frame(self.file_body)
        name_row.pack(fill="x", pady=(4, 0))
        ttk.Label(name_row, text="ファイル名に含む").pack(side="left")
        self.name_entry = ttk.Entry(name_row, width=24)
        self.name_entry.pack(side="left", padx=6, fill="x", expand=True)

        date_row = ttk.Frame(self.file_body)
        date_row.pack(fill="x", pady=(4, 0))
        ttk.Label(date_row, text="更新日").pack(side="left")
        self.modified_op = tk.StringVar(value=">=")
        ttk.Combobox(
            date_row, width=4, state="readonly", textvariable=self.modified_op, values=[">=", "<="]
        ).pack(side="left", padx=4)
        self.modified_value = ttk.Entry(date_row, width=12)
        self.modified_value.pack(side="left")
        ttk.Label(date_row, text="（例: 2025-01-01）").pack(side="left", padx=4)

        size_row = ttk.Frame(self.file_body)
        size_row.pack(fill="x", pady=(4, 0))
        ttk.Label(size_row, text="サイズ").pack(side="left")
        self.size_op = tk.StringVar(value="<")
        ttk.Combobox(
            size_row, width=4, state="readonly", textvariable=self.size_op, values=["<", "<=", ">", ">="]
        ).pack(side="left", padx=4)
        self.size_value = ttk.Entry(size_row, width=10)
        self.size_value.pack(side="left")
        ttk.Label(size_row, text="（例: 50MB）").pack(side="left", padx=4)

        self._prefill_file_conditions(groups.metadata)
        for variable in self.type_vars.values():
            variable.trace_add("write", lambda *_a: self._preview())

    def _build_advanced(self, parent, groups: BuilderGroups) -> None:
        self.advanced_frame = ttk.LabelFrame(parent, text="詳細式（上級者向け）", padding=6)
        self.advanced_frame.pack(fill="x", pady=(6, 0))
        self._advanced_toggle = ttk.Button(
            self.advanced_frame, text="▶ 開く", width=10, command=self._toggle_advanced
        )
        self._advanced_toggle.pack(anchor="w")
        self.advanced_body = ttk.Frame(self.advanced_frame)
        ttk.Label(
            self.advanced_body,
            text="ここに書いた式はそのまま検索式として使われます（上の欄と AND で結合）。",
            foreground="#666",
        ).pack(anchor="w")
        self.advanced = tk.Text(self.advanced_body, height=2, width=40, wrap="word")
        self.advanced.pack(fill="x")
        self._advanced_open = False
        if groups.advanced:
            self._toggle_advanced()
            self._advanced_open = True

    def _toggle_file_conditions(self) -> None:
        if self.file_body.winfo_ismapped():
            self.file_body.pack_forget()
            self._file_toggle.configure(text="▶ 表示")
        else:
            self.file_body.pack(fill="x", pady=(4, 0))
            self._file_toggle.configure(text="▼ 表示中")

    def _toggle_advanced(self) -> None:
        if self.advanced_body.winfo_ismapped():
            self.advanced_body.pack_forget()
            self._advanced_toggle.configure(text="▶ 開く")
        else:
            self.advanced_body.pack(fill="x", pady=(4, 0))
            self._advanced_toggle.configure(text="▼ 表示中")

    # ---------------------------------------------------------- population
    def _apply_groups(self, groups: BuilderGroups) -> None:
        self._set_lines(self.all_of, groups.all_of)
        self._set_lines(self.any_of, groups.any_of)
        self._set_lines(self.none_of, groups.none_of)
        self._set_lines(self.n_of, groups.n_of)
        if groups.phrases:
            self.phrase.insert(0, groups.phrases[0])
        for extra in groups.phrases[1:]:
            self._append_line(self.all_of, f'"{extra}"')
        self.near_a.insert(0, groups.near_a)
        self.near_b.insert(0, groups.near_b)
        self.near_distance.delete(0, "end")
        self.near_distance.insert(0, str(groups.near_distance or 100))
        if groups.advanced:
            self.advanced.insert("1.0", groups.advanced)

    def _prefill_file_conditions(self, metadata: list[str]) -> None:
        wanted_exts: set[str] = set()
        for text in metadata:
            try:
                node = parse_query(text, legacy_operator=self.legacy_operator)
            except QuerySyntaxError:
                self._metadata_rest.append(text)
                continue
            if not isinstance(node, Meta):
                self._metadata_rest.append(text)
                continue
            if node.field == "type":
                for key, extensions in self.TYPE_EXTENSIONS.items():
                    if extensions[0].lstrip(".") in {node.value, node.value + "x"} or key == node.value:
                        self.type_vars[key].set(True)
            elif node.field == "ext":
                wanted_exts.update("." + value.lstrip(".") for value in node.value.split(",") if value)
            elif node.field == "name":
                self.name_entry.delete(0, "end")
                self.name_entry.insert(0, node.value)
            elif node.field == "modified":
                self.modified_op.set(node.op if node.op in (">=", "<=") else ">=")
                self.modified_value.delete(0, "end")
                self.modified_value.insert(0, node.value.split("..")[0])
            elif node.field == "size":
                self.size_op.set(node.op if node.op in ("<", "<=", ">", ">=") else "<")
                self.size_value.delete(0, "end")
                self.size_value.insert(0, _human_size(node.value.split("..")[0]))
            else:
                self._metadata_rest.append(text)
        if wanted_exts:
            matched = False
            for key, extensions in self.TYPE_EXTENSIONS.items():
                if wanted_exts == set(extensions):
                    self.type_vars[key].set(True)
                    matched = True
                    break
            if not matched:
                # Keep the exact extension list instead of widening it to a group.
                self._metadata_rest.append(
                    "ext:" + ",".join(sorted(ext.lstrip(".") for ext in wanted_exts))
                )

    def _set_lines(self, widget: tk.Text, values: list[str]) -> None:
        if values:
            widget.insert("1.0", "\n".join(values))

    def _append_line(self, widget: tk.Text, value: str) -> None:
        current = widget.get("1.0", "end").strip()
        widget.delete("1.0", "end")
        widget.insert("1.0", f"{current}\n{value}" if current else value)

    def _lines(self, widget: tk.Text) -> list[str]:
        return [line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip()]

    # -------------------------------------------------------------- build
    def _file_metadata(self) -> list[str]:
        values: list[str] = []
        chosen = [key for key, var in self.type_vars.items() if var.get()]
        if chosen:
            extensions = [ext.lstrip(".") for key in chosen for ext in self.TYPE_EXTENSIONS[key]]
            values.append(f"ext:{','.join(dict.fromkeys(extensions))}")
        name = self.name_entry.get().strip()
        if name:
            values.append(f"name:{name}")
        date = self.modified_value.get().strip()
        if date:
            values.append(f"modified:{self.modified_op.get()}{date}")
        size = self.size_value.get().strip()
        if size:
            values.append(f"size:{self.size_op.get()}{size}")
        values.extend(self._metadata_rest)
        return values

    def _build_query(self) -> tuple[str, str]:
        try:
            count = max(1, int(self.count_var.get() or 1))
        except ValueError:
            return "", "「N個以上含む」の数は数字で入力してください"
        try:
            distance = max(1, int(self.near_distance.get() or 100))
        except ValueError:
            return "", "「近くにある」の距離は数字で入力してください"
        advanced = self.advanced.get("1.0", "end").strip()
        if advanced:
            try:
                parse_query(advanced, legacy_operator=self.legacy_operator)
            except QuerySyntaxError as exc:
                return "", f"詳細式のエラー: {exc.message}"
        groups = BuilderGroups(
            all_of=self._lines(self.all_of),
            any_of=self._lines(self.any_of),
            n_of_count=count if self._lines(self.n_of) else 0,
            n_of=self._lines(self.n_of),
            none_of=self._lines(self.none_of),
            phrases=[self.phrase.get()] if self.phrase.get().strip() else [],
            near_a=self.near_a.get(),
            near_b=self.near_b.get(),
            near_distance=distance,
            metadata=self._file_metadata(),
            advanced=advanced,
        )
        try:
            node = groups.build()
        except QuerySyntaxError as exc:
            return "", f"条件のエラー: {exc.message}"
        if node is None:
            return "", ""
        return to_query_string(node), ""

    def _preview(self) -> str:
        query, error = self._build_query()
        self.error_var.set(error)
        self.preview_var.set(error or query or "（条件が未入力です）")
        return query

    def _clear(self) -> None:
        for widget in (self.all_of, self.any_of, self.none_of, self.n_of, self.advanced):
            widget.delete("1.0", "end")
        for entry in (self.phrase, self.near_a, self.near_b, self.name_entry, self.size_value, self.modified_value):
            entry.delete(0, "end")
        for variable in self.type_vars.values():
            variable.set(False)
        self.count_var.set("2")
        self._metadata_rest.clear()
        self._preview()

    def _accept(self) -> None:
        query, error = self._build_query()
        if error:
            self.error_var.set(error)
            messagebox.showerror("条件エラー", error, parent=self.window)
            return
        if not query:
            messagebox.showinfo("条件が空です", "条件を1つ以上入力してください。", parent=self.window)
            return
        self._close(query)


def _human_size(value: str) -> str:
    """Render a byte count as the MB/KB form the size field expects."""
    try:
        size = int(value)
    except ValueError:
        return value
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size and size % factor == 0:
            return f"{size // factor}{unit}"
    return str(size)


class FoldersDialog(Dialog):
    """View / add / remove the search folders (v5.1 spec section 7)."""

    def __init__(self, parent, *, roots: list[str]) -> None:
        super().__init__(parent, "検索場所", width=620, height=360)
        self.roots = list(roots)
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, text="検索するフォルダです。複数指定すると、まとめて検索します。"
        ).pack(anchor="w")
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True, pady=6)
        self.listbox = tk.Listbox(list_frame, height=8, activestyle="dotbox")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.status_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.status_var, foreground="#8a6d3b").pack(anchor="w")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(buttons, text="追加…", command=self._add).pack(side="left")
        ttk.Button(buttons, text="削除", command=self._remove).pack(side="left", padx=4)
        ttk.Button(buttons, text="上へ", command=lambda: self._move(-1)).pack(side="left", padx=4)
        ttk.Button(buttons, text="下へ", command=lambda: self._move(1)).pack(side="left")
        ttk.Button(buttons, text="OK", command=self._accept).pack(side="right")
        ttk.Button(buttons, text="キャンセル", command=self._cancel).pack(side="right", padx=4)
        self._refresh()

    def _refresh(self) -> None:
        self.listbox.delete(0, "end")
        missing = 0
        for index, root in enumerate(self.roots):
            exists = os.path.isdir(root)
            if not exists:
                missing += 1
            label = root if exists else f"{root}   （見つかりません）"
            self.listbox.insert("end", label)
            if not exists:
                self.listbox.itemconfigure(index, foreground="#a94442")
        if missing:
            self.status_var.set(f"{missing}件のフォルダが見つかりません（UNCやネットワーク未接続の可能性）")
        else:
            self.status_var.set("")

    def _add(self) -> None:
        initial = self.roots[-1] if self.roots else os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=initial, title="検索場所に追加")
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        if any(os.path.normcase(root) == os.path.normcase(chosen) for root in self.roots):
            self.status_var.set("すでに追加されています")
            return
        self.roots.append(chosen)
        self._refresh()

    def _remove(self) -> None:
        selection = list(self.listbox.curselection())
        for index in reversed(selection):
            del self.roots[index]
        self._refresh()

    def _move(self, delta: int) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        target = index + delta
        if not 0 <= target < len(self.roots):
            return
        self.roots[index], self.roots[target] = self.roots[target], self.roots[index]
        self._refresh()
        self.listbox.selection_set(target)

    def _accept(self) -> None:
        self._close([root for root in self.roots if root])


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
