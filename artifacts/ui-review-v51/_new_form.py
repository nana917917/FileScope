    def _build_form(self) -> None:
        """Search form.

        Laid out with grid weights so the folder and query fields always use the
        full window width: the inputs stretch, the buttons keep a fixed size.
        """
        form = ttk.Frame(self, padding=(8, 6, 8, 2))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="検索場所").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.root_var = tk.StringVar(value=self.state.root)
        self.root_combo = ttk.Combobox(form, textvariable=self.root_var, values=self.settings.recent_roots)
        self.root_combo.grid(row=0, column=1, sticky="ew")
        folder_buttons = ttk.Frame(form)
        folder_buttons.grid(row=0, column=2, sticky="e", padx=(6, 0))
        ttk.Button(folder_buttons, text="参照", width=6, command=self.choose_root).pack(side="left")
        ttk.Button(folder_buttons, text="＋追加", width=7, command=self.add_root).pack(side="left", padx=3)
        ttk.Button(folder_buttons, text="一覧", width=6, command=self.open_folders).pack(side="left")

        ttk.Label(form, text="検索文字").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self.query_var = tk.StringVar(value=self.state.query)
        query_entry = ttk.Entry(form, textvariable=self.query_var, font=self.query_font)
        query_entry.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        query_entry.bind("<Return>", lambda _event: self.start_search())
        self.query_entry = query_entry
        self.search_button = ttk.Button(form, text="検索開始", command=self.start_search, width=10)
        self.search_button.grid(row=1, column=2, sticky="e", padx=(6, 0), pady=(4, 0))

        ttk.Label(form, text="除外").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self.exclude_var = tk.StringVar(value=self.state.exclude_query)
        ttk.Entry(form, textvariable=self.exclude_var, font=self.body_font).grid(
            row=2, column=1, sticky="ew", pady=(4, 0)
        )
        ttk.Label(form, text="ここに入れた語を含む資料は除外します", foreground="#666").grid(
            row=2, column=2, sticky="w", padx=(6, 0), pady=(4, 0)
        )

        mode_row = ttk.Frame(self, padding=(8, 2, 8, 0))
        mode_row.pack(fill="x")
        self.mode_var = tk.StringVar(value=self.state.mode)
        mode_frame = ttk.Frame(mode_row)
        mode_frame.pack(side="left")
        ttk.Label(mode_frame, text="検索モード", font=self.body_bold).pack(side="left", padx=(0, 6))
        for mode in ("fast", "standard", "full"):
            ttk.Radiobutton(
                mode_frame,
                text=MODE_LABELS[mode],
                value=mode,
                variable=self.mode_var,
                command=self._update_mode_hint,
            ).pack(side="left", padx=(0, 6))
        self.mode_hint = ttk.Label(mode_row, text=MODE_DESCRIPTIONS[self.state.mode], foreground="#555")
        self.mode_hint.pack(side="left", padx=12)

        exact = ttk.Frame(self, padding=(8, 2, 8, 2))
        exact.pack(fill="x")
        self.kind_vars = {
            "excel": tk.BooleanVar(value=self.state.include_excel),
            "pdf": tk.BooleanVar(value=self.state.include_pdf),
            "word": tk.BooleanVar(value=self.state.include_word),
            "ppt": tk.BooleanVar(value=self.state.include_powerpoint),
            "text": tk.BooleanVar(value=self.state.include_text),
            "unknown": tk.BooleanVar(value=self.state.include_unknown_text),
        }
        ttk.Label(exact, text="検索対象", font=self.body_bold).pack(side="left", padx=(0, 4))
        for key, label in (
            ("excel", "Excel"),
            ("pdf", "PDF"),
            ("word", "Word"),
            ("ppt", "PPT"),
            ("text", "Text"),
            ("unknown", "未知形式"),
        ):
            ttk.Checkbutton(exact, text=label, variable=self.kind_vars[key]).pack(side="left", padx=2)
        ttk.Button(exact, text="条件…", width=7, command=self.open_condition_builder).pack(side="left", padx=(10, 2))
        ttk.Button(exact, text="詳細設定…", width=10, command=self.open_settings).pack(side="left", padx=2)
        ttk.Button(exact, text="索引…", width=7, command=self.open_index_status).pack(side="left", padx=2)
        ttk.Button(exact, text="診断…", width=7, command=self.open_diagnostics).pack(side="left", padx=2)

        actions = ttk.Frame(self, padding=(8, 0, 8, 2))
        actions.pack(fill="x")
        self.cancel_button = ttk.Button(actions, text="中断", width=7, command=self.cancel_search, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 4))
        self.pause_button = ttk.Button(
            actions, text="一時停止", width=8, command=self.toggle_pause, state="disabled"
        )
        self.pause_button.pack(side="left", padx=4)
        ttk.Button(actions, text="プリセット", width=10, command=self.open_presets).pack(side="left", padx=(10, 2))
        ttk.Button(actions, text="履歴", width=7, command=self.open_history).pack(side="left", padx=2)
        ttk.Button(actions, text="CSV保存", width=9, command=self.export_csv).pack(side="right")
        self.ocr_label = ttk.Label(
            actions,
            text=(
                f"OCR: {self.ocr_status.language_expression or '利用可'}"
                if self.ocr_status.ready
                else "OCR: 未導入（クリックで診断）"
            ),
            foreground="#555" if self.ocr_status.ready else "#8a6d3b",
        )
        self.ocr_label.pack(side="right", padx=10)
        self.ocr_label.bind("<Button-1>", lambda _e: self.open_diagnostics())
        ttk.Label(actions, textvariable=self._index_text, foreground="#555").pack(side="right", padx=6)
