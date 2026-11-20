"""Qt Widgets front end for the FileScope search engine."""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import datetime

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import SOURCE_LABELS, FileKind, SourceType
from ..core.paths import normalized_key
from . import results
from .qt_results import ResultTable, ResultView
from .state import UiState, apply_preset, display_kind, preset_from_state, record_history

KINDS = ("Excel", "CSV", "PDF", "Word", "PPT", "Text", "Archive", "?")
TYPES = {
    "include_excel": "Excel", "include_pdf": "PDF", "include_word": "Word",
    "include_powerpoint": "PowerPoint", "include_text": "Text（CSV含む）",
    "include_unknown_text": "未知形式", "include_archives": "アーカイブ",
}
DEFAULT_COLUMNS = {"confirmed", "name", "displays", "hits", "kind", "path"}
THEMES = {"light": "ライト", "dark": "ダーク", "classic": "クラシック"}


class Window(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.state = UiState.from_settings(settings)
        self.session = None
        self.summary = None
        self.tasks = queue.SimpleQueue()
        self.search_pool = ThreadPoolExecutor(1, thread_name_prefix="filescope-search")
        self.preview_pool = ThreadPoolExecutor(1, thread_name_prefix="filescope-preview")
        self.io_pool = ThreadPoolExecutor(1, thread_name_prefix="filescope-io")
        self.preparing = False
        self.cancelled = False
        self.closing = False
        self.generation = 0
        self.preview_token = 0
        self.preview_future = None
        self.preview_cancel = threading.Event()
        self.preview_cache = OrderedDict()
        self.pdf_cache = OrderedDict()
        self.current = None
        self.keys = set()
        self.kind_counts = Counter()
        self.confirmed = settings.confirmed_set()
        self.database = self.ocr_cache = None
        self.ocr_available = False
        self.page = 1
        self.setWindowTitle("FileScope")
        self.resize(1260, 790)
        self.setMinimumSize(760, 520)
        self._build()
        self._load_layout()
        self._theme(settings.theme)
        self.timer = QTimer(self, interval=30)
        self.timer.timeout.connect(self._drain)
        self.timer.start()
        self.preview_timer = QTimer(self, singleShot=True, interval=120)
        self.preview_timer.timeout.connect(self._load_preview)
        self._refresh_history()

    def _button(self, text, callback, layout=None):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if layout is not None:
            layout.addWidget(button)
        return button

    def _menu_button(self, text, layout):
        button = QToolButton(text=text, popupMode=QToolButton.InstantPopup)
        menu = QMenu(button)
        button.setMenu(menu)
        layout.addWidget(button)
        return button, menu

    def _build(self):
        base = QWidget()
        self.setCentralWidget(base)
        layout = QVBoxLayout(base)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(10)
        form = QGridLayout()
        layout.addLayout(form)
        form.setColumnStretch(1, 1)
        self.root_input = QComboBox(editable=True)
        self.root_input.setInsertPolicy(QComboBox.NoInsert)
        self.root_input.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.root_input.setMinimumContentsLength(20)
        self.root_input.lineEdit().setPlaceholderText("複数のフォルダーは ; で区切る")
        self.root_input.setEditText(self.state.root)
        self.query = QLineEdit(self.state.query)
        self.query.setPlaceholderText("検索したい言葉や品番を入力")
        self.query.returnPressed.connect(self.start_search)
        form.addWidget(QLabel("検索場所"), 0, 0)
        form.addWidget(self.root_input, 0, 1)
        browse = self._button("参照…", self._browse)
        browse.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        form.addWidget(browse, 0, 2)
        form.addWidget(QLabel("検索文字"), 1, 0)
        form.addWidget(self.query, 1, 1)
        form.addWidget(self._button("条件を作る…", self._conditions), 1, 2)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.search_button = self._button("検索", self.start_search, controls)
        self.search_button.setObjectName("primary")
        self.pause_button = self._button("一時停止", self._pause, controls)
        self.cancel_button = self._button("中断", self._cancel, controls)
        self._busy(False)
        self.refine_button = self._button("追加条件…", self._refine, controls)
        self.targets_button, targets = self._menu_button("対象形式", controls)
        self.target_actions = {}
        for key, label in TYPES.items():
            action = targets.addAction(label)
            action.setCheckable(True)
            action.setChecked(getattr(self.state, key))
            action.toggled.connect(lambda value, field=key: setattr(self.state, field, value))
            self.target_actions[key] = action
        self._button("検索設定…", self._search_settings, controls)
        self._button("履歴・プリセット…", self._history, controls)
        controls.addStretch()
        _, settings_menu = self._menu_button("表示", controls)
        for key, name in THEMES.items():
            settings_menu.addAction(name, lambda value=key: self._theme(value))
        settings_menu.addSeparator()
        settings_menu.addAction("文字サイズ…", self._font_size)
        compact = settings_menu.addAction("コンパクト表示")
        compact.setCheckable(True)
        compact.setChecked(bool(self.settings.window.get("qt_compact", 0)))
        compact.toggled.connect(self._density)
        self.split = QSplitter(Qt.Horizontal)
        layout.addWidget(self.split, 1)
        left = QWidget()
        right = QWidget()
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.setChildrenCollapsible(False)
        panel = QVBoxLayout(left)
        panel.setContentsMargins(0, 0, 4, 0)
        filters = QHBoxLayout()
        panel.addLayout(filters)
        self.kind_button, kinds_menu = self._menu_button("種類", filters)
        self.kind_actions = {}
        for kind in KINDS:
            action = kinds_menu.addAction(kind)
            action.setCheckable(True)
            action.toggled.connect(self._filters)
            self.kind_actions[kind] = action
        kinds_menu.aboutToShow.connect(self._kind_labels)
        _, more = self._menu_button("絞り込み", filters)
        self.source_actions = {}
        for source in SourceType:
            action = more.addAction(SOURCE_LABELS[source])
            action.setCheckable(True)
            action.toggled.connect(self._filters)
            self.source_actions[source.value] = action
        self.ocr_filter = more.addAction("OCRのみ")
        self.ocr_filter.setCheckable(True)
        self.ocr_filter.toggled.connect(self._filters)
        self.review_filter = QComboBox()
        self.review_filter.addItems(["確認状態すべて", "確認済み", "未確認"])
        self.review_filter.currentIndexChanged.connect(self._filters)
        more.addAction("絞り込みを解除", self._clear_filters)
        self.filename_filter = QLineEdit()
        self.filename_filter.setPlaceholderText("結果内のファイル名・パス")
        self.filename_filter.textChanged.connect(self._filters)
        filters.addWidget(self.filename_filter, 1)
        self.count = QLabel("0 資料")
        filters.addWidget(self.count)
        _, columns = self._menu_button("列", filters)
        self.table = QTableView()
        panel.addWidget(self.table, 1)
        self.model = ResultTable(self)
        self.proxy = ResultView(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSortIndicator(-1, Qt.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().currentRowChanged.connect(self._select)
        self.table.doubleClicked.connect(lambda index: self._open(self._result(index).path))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._result_menu)
        QShortcut(QKeySequence.Copy, self.table, activated=self._copy, context=Qt.WidgetShortcut)
        for i, (key, label, _) in enumerate(results.COLUMNS):
            action = columns.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(self.settings.window.get(f"show_column_{key}", key in DEFAULT_COLUMNS)))
            action.toggled.connect(lambda value, col=i: self._column_visibility(col, value))
            self.table.setColumnHidden(i, not action.isChecked())
        if all(self.table.isColumnHidden(i) for i in range(len(results.COLUMNS))):
            self.table.showColumn(1)
            columns.actions()[1].setChecked(True)
        self.column_actions = columns.actions()
        self.issues_toggle = self._button("問題・未確認 0件", self._toggle_issues, panel)
        self.issues = QTreeWidget()
        self.issues.setHeaderLabels(["種類", "場所", "内容"])
        self.issues.setMaximumHeight(155)
        self.issues.setUniformRowHeights(True)
        self.issues.setContextMenuPolicy(Qt.CustomContextMenu)
        self.issues.customContextMenuRequested.connect(self._issue_menu)
        self.issues.itemDoubleClicked.connect(lambda item, _: self._open(item.text(1)))
        panel.addWidget(self.issues)
        self.issues.hide()
        preview_layout = QVBoxLayout(right)
        preview_layout.setContentsMargins(4, 0, 0, 0)
        self.preview_title = QLabel("プレビュー")
        self.preview_title.setTextFormat(Qt.PlainText)
        self.preview_title.setWordWrap(True)
        preview_layout.addWidget(self.preview_title)
        tools = QHBoxLayout()
        preview_layout.addLayout(tools)
        self._button("原本を開く", lambda: self.current and self._open(self.current.path), tools)
        self._button("前の一致", lambda: self._find(True), tools)
        self._button("次の一致", lambda: self._find(False), tools)
        tools.addStretch()
        pdf_tools = QHBoxLayout()
        preview_layout.addLayout(pdf_tools)
        self.pdf_button = self._button("PDFページ", self._toggle_pdf, pdf_tools)
        self.back_page = self._button("前ページ", lambda: self._request_pdf(self.page - 1), pdf_tools)
        self.next_page = self._button("次ページ", lambda: self._request_pdf(self.page + 1), pdf_tools)
        self.page_label = QLabel()
        pdf_tools.addWidget(self.page_label)
        pdf_tools.addStretch()
        self.stack = QStackedWidget()
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setPlaceholderText("結果を選択すると、本文と一致箇所を表示します。")
        self.stack.addWidget(self.preview)
        scroll = QScrollArea(widgetResizable=True)
        self.pdf_image = QLabel(alignment=Qt.AlignCenter)
        scroll.setWidget(self.pdf_image)
        self.stack.addWidget(scroll)
        preview_layout.addWidget(self.stack, 1)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self._find(False))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self._find(True))
        QShortcut(QKeySequence("Escape"), self, activated=self._cancel)
        self._pdf_controls(False)
        footer = QHBoxLayout()
        layout.addLayout(footer)
        self.status = QLabel("待機中")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self._button("CSV保存…", self._export, footer)

    def _column_visibility(self, column, visible):
        if not visible and sum(not self.table.isColumnHidden(i) for i in range(len(results.COLUMNS))) == 1:
            self.column_actions[column].setChecked(True)
            return
        self.table.setColumnHidden(column, not visible)

    def _load_layout(self):
        options = self.settings.window
        widths = (42, 300, 185, 62, 74, 125, 80, 84, 54, 155)
        for i, (key, _, _) in enumerate(results.COLUMNS):
            width = options.get(f"qt_width_{key}", options.get(f"column_{key}", widths[i]))
            if not isinstance(width, int) or not 24 <= width <= 3000:
                width = widths[i]
            self.table.setColumnWidth(i, width)
        if isinstance(header := options.get("qt_header"), str):
            self.table.horizontalHeader().restoreState(QByteArray.fromHex(header.encode("ascii", "ignore")))
        if all(self.table.isColumnHidden(i) for i in range(len(results.COLUMNS))):
            self.table.showColumn(1)
        for i, action in enumerate(self.column_actions):
            action.blockSignals(True)
            action.setChecked(not self.table.isColumnHidden(i))
            action.blockSignals(False)
        legacy_sort = [column[0] for column in results.COLUMNS]
        if self.state.sort_column in legacy_sort and "qt_header" not in options:
            self.table.sortByColumn(legacy_sort.index(self.state.sort_column),
                                    Qt.DescendingOrder if self.state.sort_descending else Qt.AscendingOrder)
        if isinstance(geometry := options.get("qt_geometry"), str):
            self.restoreGeometry(QByteArray.fromHex(geometry.encode("ascii", "ignore")))
        ratio = options.get("preview_ratio_per_mille", 390)
        if not isinstance(ratio, int) or not 200 <= ratio <= 700:
            ratio = 390
        self.split.setSizes([1000 - ratio, ratio])
        self._density(bool(options.get("qt_compact", 0)))

    def _density(self, compact):
        self.settings.window["qt_compact"] = int(compact)
        self.table.verticalHeader().setDefaultSectionSize(
            max(23 if compact else 29, self.fontMetrics().height() + (4 if compact else 10))
        )

    def _font_size(self):
        value, ok = QInputDialog.getInt(self, "文字サイズ", "ポイント", self.font().pointSize(), 8, 18)
        if ok:
            self.settings.window["qt_font_size"] = value
            self._theme(self.settings.theme)

    def _theme(self, theme):
        theme = theme if theme in THEMES else "light"
        self.settings.theme = theme
        font = self.font()
        size = self.settings.window.get("qt_font_size", 10)
        font.setFamily("Segoe UI")
        font.setPointSize(size if isinstance(size, int) and 8 <= size <= 18 else 10)
        self.setFont(font)
        self._density(bool(self.settings.window.get("qt_compact", 0)))
        if theme == "classic":
            self.setStyleSheet("")
            return
        bg, surface, text, border, alt, selected = (
            ("#20242b", "#282e36", "#edf1f5", "#47515e", "#303741", "#23527f")
            if theme == "dark" else
            ("#f5f7fa", "#ffffff", "#1b2838", "#c5d0dc", "#f5f8fc", "#d5e9ff")
        )
        self.setStyleSheet(f"""
            QMainWindow, QDialog {{ background: {bg}; }}
            QWidget {{ color: {text}; }}
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTableView, QTreeWidget, QMenu, QListView {{
                background: {surface}; border: 1px solid {border}; border-radius: 3px;
                selection-background-color: {selected}; selection-color: {text};
            }}
            QLineEdit, QComboBox, QSpinBox {{ padding: 4px 7px; }}
            QPushButton, QToolButton, QHeaderView::section {{
                background: {surface}; border: 1px solid {border}; border-radius: 3px; padding: 4px 8px;
            }}
            QHeaderView::section {{ border-radius: 0; }}
            QPushButton:hover, QToolButton:hover, QMenu::item:selected {{ background: {selected}; }}
            QPushButton:disabled {{ color: #798493; }}
            QPushButton#primary {{ background: #1762aa; color: white; font-weight: bold; min-width: 70px; }}
            QPushButton#primary:disabled {{ background: #526a83; }}
            QSplitter::handle {{ background: {border}; width: 2px; }}
            QTableView {{ alternate-background-color: {alt}; }}
        """)

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(self, "検索場所を追加")
        if directory:
            previous = self.root_input.currentText().strip()
            self.root_input.setEditText(f"{previous}; {directory}" if previous else directory)

    def _sync_form(self):
        self.state.root = self.root_input.currentText()
        self.state.query = self.query.text()

    def _conditions(self):
        from .qt_conditions import condition_dialog

        expression = condition_dialog(self, self.query.text(), self.state.legacy_operator)
        if expression is not None:
            self.query.setText(expression)

    def _refine(self):
        value, ok = QInputDialog.getText(self, "追加条件（検索2）",
                                        "本文も対象に検索し直します。空欄で解除します。",
                                        text=self.state.refine_text)
        if ok:
            self.state.refine_text = value
            self.refine_button.setText("追加条件あり…" if value else "追加条件…")
            self.start_search()

    def _search_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("検索設定")
        form = QFormLayout(dialog)
        checks = {}
        for key, label in {
            **TYPES, "include_subfolders": "サブフォルダー", "search_path_names": "ファイル名・パス",
            "search_formula": "Excelの数式", "case_sensitive": "大文字と小文字を区別",
            "ignore_width": "全角と半角の差を無視", "part_number_mode": "品番検索",
        }.items():
            check = QCheckBox(label, checked=bool(getattr(self.state, key)))
            checks[key] = check
            form.addRow(check)
        mode = QComboBox()
        mode.addItems(["高速", "標準", "全文確認"])
        modes = ("fast", "standard", "full")
        mode.setCurrentIndex(modes.index(self.state.mode) if self.state.mode in modes else 1)
        form.addRow("検索モード", mode)
        ocr = QComboBox()
        ocr.addItems(["自動", "全ページ", "無効"])
        ocr_values = ("auto", "all", "off")
        ocr.setCurrentIndex(ocr_values.index(self.state.pdf_ocr_mode) if self.state.pdf_ocr_mode in ocr_values else 0)
        form.addRow("PDF OCR", ocr)
        online = QComboBox()
        policies = ("auto", "skip", "fetch")
        online.addItems(["検索モードに従う", "未取得ファイルをスキップ", "取得して検索"])
        online.setCurrentIndex(policies.index(self.state.online_files_policy)
                               if self.state.online_files_policy in policies else 0)
        form.addRow("OneDrive 未取得", online)
        languages = QLineEdit(self.state.ocr_languages)
        form.addRow("OCR言語", languages)
        excluded = QLineEdit(self.state.exclude_query)
        form.addRow("除外語", excluded)
        operator = QComboBox()
        operator.addItems(["OR", "AND"])
        operator.setCurrentText(self.state.legacy_operator)
        form.addRow("空白区切り", operator)
        autosave = QCheckBox("結果のCSV自動保存", checked=self.settings.autosave_results)
        form.addRow(autosave)
        index = QCheckBox("検索索引を使用", checked=self.settings.index_enabled)
        form.addRow(index)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            for key, check in checks.items():
                setattr(self.state, key, check.isChecked())
            self.state.mode = modes[mode.currentIndex()]
            self.state.pdf_ocr_mode = ocr_values[ocr.currentIndex()]
            self.state.online_files_policy = policies[online.currentIndex()]
            self.state.ocr_languages = languages.text()
            self.state.exclude_query = excluded.text()
            self.state.legacy_operator = operator.currentText()
            self.settings.autosave_results = autosave.isChecked()
            self.settings.index_enabled = index.isChecked()
            self._sync_targets()

    def _sync_targets(self):
        for key, action in self.target_actions.items():
            action.setChecked(bool(getattr(self.state, key)))

    def _refresh_history(self):
        current = self.root_input.currentText()
        self.root_input.clear()
        self.root_input.addItems(self.settings.recent_roots)
        self.root_input.setEditText(current)

    def _history(self):
        items = []
        presets = self.settings.presets
        for preset in presets:
            items.append("プリセット  " + preset.get("name", ""))
        for item in self.settings.history:
            items.append("履歴  " + item.get("query", "") + "  ·  " + item.get("root", ""))
        save = "現在の条件をプリセットとして保存…"
        dialog = QDialog(self)
        dialog.setWindowTitle("履歴・プリセット")
        form = QFormLayout(dialog)
        options = QComboBox()
        options.addItems([save, *items])
        form.addRow(options)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if not dialog.exec():
            return
        selected = options.currentIndex()
        if selected == 0:
            name, accepted = QInputDialog.getText(self, "プリセット", "名前")
            if accepted and name.strip():
                self._sync_form()
                self.settings.presets.append(preset_from_state(self.state, name.strip()))
            return
        if selected <= len(presets):
            apply_preset(self.state, presets[selected - 1])
        else:
            item = self.settings.history[selected - len(presets) - 1]
            self.state.query = item.get("query", "")
            self.state.root = item.get("root", "")
            self.state.mode = item.get("mode", self.state.mode)
            self.state.refine_text = self.state.exclude_query = ""
        self.root_input.setEditText(self.state.root)
        self.query.setText(self.state.query)
        self._sync_targets()
        self.refine_button.setText("追加条件あり…" if self.state.refine_text else "追加条件…")


    def _busy(self, busy):
        self.search_button.setEnabled(not busy)
        self.pause_button.setEnabled(busy)
        self.cancel_button.setEnabled(busy)
        self.pause_button.setText("一時停止")

    def start_search(self):
        if self.preparing or self.session is not None or self.closing:
            return
        self._sync_form()
        if not self.state.roots() or not self.state.combined_query().strip():
            self.status.setText("検索場所と検索文字を入力してください。")
            return
        self.generation += 1
        self._clear_preview()
        self.summary = None
        self.search_matcher = None
        self.cancelled = False
        self.preparing = True
        self.keys.clear()
        self.kind_counts.clear()
        self.model.clear()
        self.issues.clear()
        self.issues_toggle.setText("問題・未確認 0件")
        self._update_count()
        self._busy(True)
        self.pause_button.setDisabled(True)
        self.status.setText("検索準備中")
        self.search_config = None
        self.search_state = copy(self.state)
        autosave = self.settings.autosave_results
        self.search_pool.submit(self._prepare_search, self.search_state, copy(self.settings), autosave)

    def _prepare_search(self, state, settings, autosave):
        try:
            from .. import paths
            from ..core.coordinator import SearchSession
            from ..index.database import IndexDatabase
            from ..ocr_cache import OcrCache
            from ..platform import tesseract
            from ..platform.tempfiles import purge_stale

            config = state.to_config(settings)
            if self.ocr_cache is None:
                self.ocr_cache = OcrCache()
                purge_stale()
            self.ocr_available = config.pdf_ocr_mode != "off" and tesseract.probe().ready
            if config.index_enabled and self.database is None:
                try:
                    self.database = IndexDatabase(paths.index_path(), max_bytes=config.index_max_bytes)
                except Exception as exc:
                    self.tasks.put(("notice", f"索引を使用できません（{type(exc).__name__}）"))
            session = SearchSession(config, database=self.database, ocr_cache=self.ocr_cache,
                                    ocr_available=self.ocr_available)
            session.start()
            self.tasks.put(("started", session, autosave))
        except Exception as exc:
            self.tasks.put(("prepare_error", type(exc).__name__))

    def _pause(self):
        if self.session and not self.cancelled:
            paused = not self.session.paused
            self.session.set_paused(paused)
            self.pause_button.setText("再開" if paused else "一時停止")
            self.status.setText("一時停止中" if paused else "検索を再開しています…")

    def _cancel(self):
        if self.preparing or self.session:
            self.cancelled = True
            self.pause_button.setDisabled(True)
            self.cancel_button.setDisabled(True)
            self.status.setText("中断処理中")
            if self.session:
                self.session.cancel()

    def _drain(self):
        from ..core import events

        deadline = time.perf_counter() + 0.008
        while time.perf_counter() < deadline:
            try:
                task = self.tasks.get_nowait()
            except queue.Empty:
                break
            self._apply_task(task)
        if self.session is None:
            return
        batch = []
        terminal = None
        progress = None
        while len(batch) < 384 and time.perf_counter() < deadline:
            try:
                event = self.session.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, events.ResultAdded):
                result = event.result
                key = normalized_key(result.path)
                if key not in self.keys:
                    self.keys.add(key)
                    batch.append(results.Row(result, key in self.confirmed))
                    self.kind_counts[display_kind(result)] += 1
            elif isinstance(event, events.IssueAdded):
                issue = event.issue
                if self.issues.topLevelItemCount() < 500:
                    self.issues.addTopLevelItem(QTreeWidgetItem([issue.code, issue.path, issue.message]))
                    self.issues_toggle.setText(f"問題・未確認 {self.issues.topLevelItemCount()}件")
            elif isinstance(event, events.Progressed):
                progress = event.progress
            elif isinstance(event, (events.Finished, events.QueryFailed)):
                terminal = event
                break
        self.model.extend(batch)
        if batch:
            self._update_count()
        if progress and not self.cancelled and not self.session.paused:
            self.status.setText(progress.line())
        if terminal is not None:
            self._finish(terminal)
        elif self.session.finished and self.session.events.empty() and self.session.summary is not None:
            self._finish(events.Finished(self.session.summary))

    def _finish(self, event):
        from ..core.events import QueryFailed

        self.search_matcher = self.session.matcher
        self.session = None
        self._busy(False)
        if isinstance(event, QueryFailed):
            self.status.setText(f"検索式エラー（{event.position + 1}文字目付近）  {event.message}")
            self.query.setFocus()
            return
        self.summary = summary = event.summary
        coverage = summary.coverage
        if summary.truncated:
            label = "検索終了（結果上限）"
        elif coverage.cancelled:
            label = "検索中断"
        elif coverage.errors or coverage.warnings or coverage.skipped_online or coverage.skipped_size:
            label = "検索終了（一部未確認あり）"
        else:
            label = "検索完了"
        self.status.setText(coverage.summary_line(
            f"{label} ｜ {len(summary.results):,}資料一致 ｜ {summary.elapsed:.2f}秒"
        ))
        for root in self.search_state.roots():
            self.settings.remember_root(root)
        record_history(self.settings, self.search_state, len(summary.results), summary.elapsed)
        self._refresh_history()
        self.issues_toggle.setText(f"問題・未確認 {len(summary.issues):,}件")
        if self.autosave and summary.results:
            from .. import paths

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.io_pool.submit(self._autosave, paths, stamp, summary.results, self.generation)

    def _autosave(self, paths, stamp, rows, generation):
        from .main_window import write_results_csv

        try:
            path = os.path.join(paths.session_dir(), f"results-{stamp}.csv")
            ok = write_results_csv(path, (results.Row(result, False) for result in rows))
        except OSError:
            ok = False
        self.tasks.put(("saved", generation, ok))

    def _apply_task(self, task):
        kind, *payload = task
        if kind == "started":
            self.session, self.autosave = payload
            self.search_config = self.session.config
            self.preparing = False
            if self.cancelled:
                self.session.cancel()
            else:
                self.pause_button.setEnabled(True)
        elif kind == "prepare_error":
            self.preparing = False
            self._busy(False)
            self.status.setText(f"検索準備エラー（{payload[0]}）")
        elif kind == "saved":
            if payload[0] == self.generation:
                self.status.setText(self.status.text() + (" ｜ CSV保存済み" if payload[1] else " ｜ CSV保存失敗"))
        elif kind == "notice":
            self.statusBar().showMessage(payload[0], 6000)
        elif kind == "preview":
            self._apply_preview(*payload)
        elif kind == "pdf":
            self._apply_pdf(*payload)

    def _filters(self, *_):
        self.proxy.beginFilterChange()
        self.proxy.kinds = {kind for kind, action in self.kind_actions.items() if action.isChecked()}
        self.proxy.sources = {key for key, action in self.source_actions.items() if action.isChecked()}
        self.proxy.ocr = self.ocr_filter.isChecked()
        self.proxy.review = self.review_filter.currentIndex()
        self.proxy.text = self.filename_filter.text().strip().casefold()
        self.proxy.endFilterChange()
        self.kind_button.setText(f"種類 ({len(self.proxy.kinds)})" if self.proxy.kinds else "種類")
        self._update_count()

    def _clear_filters(self):
        for action in (*self.kind_actions.values(), *self.source_actions.values(), self.ocr_filter):
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self.review_filter.setCurrentIndex(0)
        self.filename_filter.clear()
        self._filters()

    def _kind_labels(self):
        for kind, action in self.kind_actions.items():
            action.setText(f"{kind}  {self.kind_counts[kind]:,}")

    def _update_count(self):
        count = self.proxy.rowCount()
        total = self.model.rowCount()
        self.count.setText(f"{count:,} / {total:,} 資料" if count != total else f"{count:,} 資料")

    def _result(self, index):
        return self.model.rows[self.proxy.mapToSource(index).row()].result

    def _selected(self):
        return [self.proxy.mapToSource(index).row() for index in self.table.selectionModel().selectedRows()]

    def _clear_preview(self):
        self.preview_token += 1
        self.preview_cancel.set()
        if self.preview_future:
            self.preview_future.cancel()
        self.preview_timer.stop()
        self.preview.clear()
        self.preview_cache.clear()
        self.pdf_cache.clear()
        self.current = None
        self.preview_title.setText("プレビュー")
        self.stack.setCurrentIndex(0)
        self.pdf_image.clear()
        self._pdf_controls(False)

    def _select(self, index, _):
        if not index.isValid():
            self.preview_token += 1
            self.preview_cancel.set()
            self.preview_timer.stop()
            self.current = None
            self.preview.clear()
            self.preview_title.setText("プレビュー")
            self.stack.setCurrentIndex(0)
            self._pdf_controls(False)
            return
        result = self._result(index)
        if result is self.current:
            return
        self.current = result
        self.preview_cancel.set()
        if self.preview_future:
            self.preview_future.cancel()
        self.preview_token += 1
        self.preview_title.setText(result.name)
        self.stack.setCurrentIndex(0)
        self._pdf_controls(result.file_kind is FileKind.PDF)
        self.page_label.clear()
        from .qt_preview import evidence_page

        self.page = evidence_page(result)
        self._show_text("\n\n".join(f"{e.location}  {e.snippet}" for e in result.evidence))
        if self.settings.preview_enabled and getattr(self, "search_config", None) is not None:
            self.preview_timer.start()

    def _load_preview(self):
        result = self.current
        if result is None:
            return
        if result.path in self.preview_cache:
            self.preview_cache.move_to_end(result.path)
            self._show_text(self.preview_cache[result.path])
            return
        matcher = self.session.matcher if self.session else self.search_matcher
        self.preview_cancel = threading.Event()
        self.preview_future = self.preview_pool.submit(
            self._preview_worker, self.preview_token, result, self.search_config,
            matcher, self.preview_cancel, self.ocr_cache, self.ocr_available,
        )

    def _preview_worker(self, token, result, config, matcher, cancel, cache, ocr):
        from .qt_preview import text_preview

        try:
            result_text, updated = text_preview(result, config, matcher, cancel, cache, ocr)
        except Exception as exc:
            result_text, updated = f"本文を表示できません（{type(exc).__name__}）", None
        if not cancel.is_set():
            self.tasks.put(("preview", token, result, result_text, updated))

    def _apply_preview(self, token, result, result_text, updated):
        if token != self.preview_token or result is not self.current:
            return
        self._cache(self.preview_cache, result.path, result_text, 12)
        if updated is not None:
            current = self.table.currentIndex()
            index = self.proxy.mapToSource(current)
            if index.isValid() and self.model.rows[index.row()].result is result:
                self.current = self.model.rows[index.row()].result = updated
                self.model.refresh(index.row(), 2, 3)
        self._show_text(result_text)

    @staticmethod
    def _cache(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _show_text(self, text):
        self.preview.setPlainText(text)
        words = sorted(set(self.current.matched_terms if self.current else ()), key=len, reverse=True)
        words = [word for word in words if word]
        self.matches = []
        if words:
            pattern = re.compile("|".join(map(re.escape, words)), re.IGNORECASE)
            cursor = QTextCursor(self.preview.document())
            formatting = QTextCharFormat()
            formatting.setBackground(QColor("#f7d477"))
            formatting.setForeground(QColor("#202020"))
            highlights = []
            # Qt cursors use UTF-16 code units. Track supplementary characters as we go.
            previous, start = 0, 0
            for match in pattern.finditer(text):
                start += len(text[previous:match.start()].encode("utf-16-le")) // 2
                end = start + len(match[0].encode("utf-16-le")) // 2
                previous = match.start()
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                selection = self._extra_selection(cursor, formatting)
                highlights.append(selection)
                self.matches.append((start, end))
                if len(highlights) >= 1000:
                    break
            self.preview.setExtraSelections(highlights)
        self.match_index = -1
        self._find(False, switch=False)

    @staticmethod
    def _extra_selection(cursor, formatting):
        from PySide6.QtWidgets import QTextEdit

        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(cursor)
        selection.format = formatting
        return selection

    def _find(self, backwards, *, switch=True):
        if not self.current or not getattr(self, "matches", ()):
            return
        if switch:
            self.stack.setCurrentIndex(0)
            self.pdf_button.setText("PDFページ")
        self.match_index = (self.match_index + (-1 if backwards else 1)) % len(self.matches)
        start, end = self.matches[self.match_index]
        cursor = self.preview.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.preview.setTextCursor(cursor)
        self.preview.centerCursor()

    def _pdf_controls(self, visible):
        for widget in (self.pdf_button, self.back_page, self.next_page, self.page_label):
            widget.setVisible(visible)
        self.pdf_button.setText("PDFページ")
        self.back_page.setDisabled(True)
        self.next_page.setDisabled(True)

    def _toggle_pdf(self):
        if self.stack.currentIndex():
            self.stack.setCurrentIndex(0)
            self.pdf_button.setText("PDFページ")
        elif self.current:
            self._request_pdf(self.page)

    def _request_pdf(self, page):
        if not self.current or getattr(self, "search_config", None) is None:
            return
        self.stack.setCurrentIndex(1)
        self.pdf_button.setText("文字表示")
        key = (self.current.path, page)
        self.pdf_request = key
        if key in self.pdf_cache:
            self._apply_pdf(self.preview_token, key, self.pdf_cache[key], "")
        else:
            self.pdf_image.setText("ページを読み込み中…")
            self.back_page.setDisabled(True)
            self.next_page.setDisabled(True)
            self.preview_pool.submit(self._pdf_worker, self.preview_token, key, self.current, self.search_config)

    def _pdf_worker(self, token, key, result, config):
        from .qt_preview import pdf_preview

        try:
            content = pdf_preview(result, config, key[1])
            self.tasks.put(("pdf", token, key, content, ""))
        except Exception as exc:
            self.tasks.put(("pdf", token, key, None, f"ページを表示できません（{type(exc).__name__}）"))

    def _apply_pdf(self, token, key, content, error):
        if token != self.preview_token or key != self.pdf_request:
            return
        if error:
            self.pdf_image.setText(error)
            return
        self._cache(self.pdf_cache, key, content, 5)
        image, page, count = content
        self.page = page
        pixmap = QPixmap()
        pixmap.loadFromData(image, "PNG")
        self.pdf_image.setPixmap(pixmap)
        self.pdf_image.setMinimumSize(pixmap.size())
        self.page_label.setText(f"{page} / {count}")
        self.back_page.setEnabled(page > 1)
        self.next_page.setEnabled(page < count)

    def _result_menu(self, point):
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        if not self.table.selectionModel().isSelected(index):
            self.table.selectRow(index.row())
            self.table.setCurrentIndex(index)
        path = self._result(index).path
        menu = QMenu(self)
        menu.addAction("原本を開く", lambda: self._open(path))
        menu.addAction("保存場所を開く", lambda: self._open(path, folder=True))
        menu.addAction("パスをコピー", self._copy)
        menu.addAction("確認済みを切り替え", self._confirm)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _confirm(self):
        for i in self._selected():
            row = self.model.rows[i]
            key = normalized_key(row.result.path)
            row.confirmed = key not in self.confirmed
            if row.confirmed:
                self.confirmed.add(key)
                self.settings.confirmed_paths.append(row.result.path)
            else:
                self.confirmed.discard(key)
                self.settings.confirmed_paths = [path for path in self.settings.confirmed_paths
                                                 if normalized_key(path) != key]
            self.model.refresh(i, 0, 0)
        self._filters()

    def _copy(self):
        QApplication.clipboard().setText("\n".join(self.model.rows[i].result.path for i in self._selected()))

    def _toggle_issues(self):
        self.issues.setVisible(not self.issues.isVisible())

    def _issue_menu(self, point):
        item = self.issues.itemAt(point)
        if item is None:
            return
        path = item.text(1)
        menu = QMenu(self)
        menu.addAction("開く", lambda: self._open(path))
        menu.addAction("フォルダーを開く", lambda: self._open(os.path.dirname(path)))
        menu.addAction("パスをコピー", lambda: QApplication.clipboard().setText(path))
        menu.exec(self.issues.viewport().mapToGlobal(point))

    def _open(self, path, *, folder=False):
        if path:
            self.io_pool.submit(self._open_worker, path, folder)

    def _open_worker(self, path, folder):
        from ..platform import shell

        ok, error = (shell.reveal_in_explorer if folder else shell.open_path)(path)
        if not ok:
            self.tasks.put(("notice", f"開けませんでした。 {error}"))

    def _export(self):
        if not self.proxy.rowCount():
            return
        path, _ = QFileDialog.getSaveFileName(self, "検索結果を保存", "filescope.csv", "CSV (*.csv)")
        if path:
            rows = [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
                    for i in range(self.proxy.rowCount())]
            self.io_pool.submit(self._export_worker, path, rows)

    def _export_worker(self, path, rows):
        from .main_window import write_results_csv

        self.tasks.put(("notice", "CSVを保存しました。" if write_results_csv(path, rows) else "CSVを保存できませんでした。"))

    def closeEvent(self, event):
        if self.preparing or self.session is not None:
            self.closing = True
            self._cancel()
            event.ignore()
            QTimer.singleShot(75, self.close)
            return
        self._clear_preview()
        self.timer.stop()
        self._sync_form()
        self.state.save_to(self.settings)
        options = self.settings.window
        options["qt_header"] = bytes(self.table.horizontalHeader().saveState().toHex()).decode("ascii")
        options["qt_geometry"] = bytes(self.saveGeometry().toHex()).decode("ascii")
        sizes = self.split.sizes()
        if sum(sizes):
            options["preview_ratio_per_mille"] = round(sizes[1] / sum(sizes) * 1000)
        self.settings.save()
        self.search_pool.shutdown(wait=False, cancel_futures=True)
        self.preview_pool.submit(self._close_resources)
        self.preview_pool.shutdown(wait=False)
        self.io_pool.shutdown(wait=False)
        event.accept()

    def _close_resources(self):
        if self.ocr_cache is not None:
            self.ocr_cache.close()
        if self.database is not None:
            self.database.close()


def run(settings, *, report="", smoke_root=""):
    app = QApplication([])
    if smoke_root:
        settings.autosave_results = False
        settings.index_enabled = False
        settings.defaults.pdf_ocr_mode = "off"
    window = Window(settings)
    window.show()

    def report_and_close(message):
        with open(report, "w", encoding="utf-8") as handle:
            handle.write(message + "\n")
        window.close()

    def verify_preview():
        if window.preview_cache:
            if "AAA" in window.preview.toPlainText() and window.matches:
                report_and_close("Qt search and preview passed")
            else:
                report_and_close("Qt preview mismatch")
        else:
            QTimer.singleShot(50, verify_preview)

    def verify_search():
        if window.summary is None:
            QTimer.singleShot(50, verify_search)
        elif window.proxy.rowCount() != 1:
            report_and_close("Qt search result count mismatch")
        else:
            window.table.setCurrentIndex(window.proxy.index(0, 1))
            QTimer.singleShot(50, verify_preview)

    if report and smoke_root:
        def start_smoke():
            window.root_input.setEditText(smoke_root)
            window.query.setText("AAA & BBB")
            window.start_search()
            QTimer.singleShot(50, verify_search)

        QTimer.singleShot(0, start_smoke)
    elif report:
        QTimer.singleShot(0, lambda: report_and_close("Qt window ready"))
    return app.exec()
