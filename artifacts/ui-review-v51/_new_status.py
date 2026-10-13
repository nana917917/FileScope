    def _build_status(self) -> None:
        """Progress bar + counters + the two status lines (v5.1 spec sections 11-12)."""
        bar = ttk.Frame(self, padding=(8, 2, 8, 6))
        bar.pack(fill="x")
        self.progress_bar = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress_bar.pack(side="left")
        ttk.Label(bar, textvariable=self._progress_text).pack(side="left", padx=10)
        ttk.Label(bar, textvariable=self._coverage_text, foreground="#555").pack(side="left", padx=6)
        ttk.Label(bar, textvariable=self._status_text, foreground="#555").pack(side="right")
        ttk.Label(bar, text="外部送信なし / ローカル処理", foreground="#777").pack(side="right", padx=12)
