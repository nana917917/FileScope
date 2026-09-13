# V4.1 → V5 feature inventory

Every user-visible V4.1 capability, checked against V5. Sources: `baseline/FileScope_v4_1_raw.py`
(buttons/labels/methods) and the V5 UI code. Verdicts are one of **kept**,
**replaced** (same need, different UI), **restored** (was missing, added back in this RC),
or **removed on purpose** (with the reason).

## Search form

| V4.1 | V5 | Verdict |
| --- | --- | --- |
| 検索フォルダ（複数: 参照/追加） | 検索場所（`;` 区切りで複数指定、履歴ドロップダウン） | **restored** (RC) — the engine always supported several roots; the UI now accepts `D:\A; D:\B` and validates each |
| 検索文字 | 検索欄 | kept |
| 除外 | 除外欄（`!語` と同じ） | kept |
| 空白区切り: OR/AND | 詳細設定の「空白区切りの既定」＋条件ビルダー | replaced (less clutter on the main form, behaviour unchanged) |
| 検索対象: Excel/PDF/Word/PPT/Text | 同じチェックボックス | kept |
| 検索対象: 未知拡張子 | 未知形式チェックボックス | **restored** (RC) |
| 子フォルダ | 詳細設定「子フォルダも検索」 | replaced (moved to advanced; default ON) |
| PDF OCR: OFF/自動/全ページ | 詳細設定の PDF OCR＋メイン画面の `OCR:` 表示 | replaced + **restored** (RC) — the status is visible again on the main window and clicking it opens 診断 |
| 検索開始 / 一時停止 / 再開 / 中断 | 検索開始 / 一時停止(再開) / 中断 | kept |
| 設定保存 | 自動保存（検索終了時と終了時）＋詳細設定のOK | replaced (no lost settings, no extra click) |
| 外部送信なし / ローカル処理 表示 | ステータスバーの「外部送信なし / ローカル処理」＋ヘルプ→Privacy | **restored** (RC) |
| 結果CSV保存 | CSV保存（Ctrl+S）＋検索終了時の自動保存 | kept |

## Results

| V4.1 | V5 | Verdict |
| --- | --- | --- |
| 結果一覧（1ヒット=1行） | 1ファイル=1行（一致条件/Hit数/種類/Source/OCR/Path列） | replaced — required by the V5 spec §39; the file-level view is what makes large result sets usable and it removes the V4 bug where the キーワード別 tab lost the file name after 確認済み |
| キーワード別タブ | 「結果をさらに絞る」＋ファセット（種類/Source/OCR） | replaced — same need (narrowing by keyword) executed on the current result set without re-reading files |
| 検索語 / ヒット数 列 | 一致条件 / Hit数 列（`8+` は下限表示） | kept + improved |
| 列クリックの並べ替え | 同じ（+ confirmed/source/relevance） | kept |
| 結果行のダブルクリックで開く | 同じ | kept |
| 確認済み（済、薄いグレー） | 同じ（Spaceは結果一覧にフォーカス時のみ） | kept |
| 大量ヒット時のRAM保護（25万ヒット） | 結果20万ファイル上限＋根拠3件/語上限 | kept |
| INFO/ERRORが結果一覧に混在 | `問題 / 未検索` を別欄に分離 | replaced — required by spec §48 |

## Right-click menu

| V4.1 | V5 | Verdict |
| --- | --- | --- |
| ファイルを開く / フォルダを開く | 同じ（Ctrl+Enterも） | kept |
| Excelの該当セルを開く | 同じ（失敗時はファイルを開く） | kept |
| 内容をコピー / ファイルパスをコピー | 同じ | kept |
| 確認済み状態を切り替え | 同じ | kept |
| — | 結果から除外 / このフォルダだけ再検索 | added (spec §84) |

## Search behaviour

| V4.1 | V5 | Verdict |
| --- | --- | --- |
| `A B`（OR/AND設定）, `A&B`, `A,B`, `2of(...)`, 除外 | 同じ（ASTパーサで互換） | kept — verified by `tests/test_v4_compat.py` on the real V4.1 engine |
| 全角/半角、大文字小文字、部品番号、`*` | 同じ | kept |
| Excel数式 | 同じ | kept |
| ファイル名/フォルダ名検索 | 同じ | kept |
| OCR（自動/全ページ、jpn+eng、PDF同時1） | 同じ＋OCRキャッシュ、`[OCR]` 表示、`ocr:true` | kept + added |
| 暗号化PDF/壊れたファイルのINFO表示 | 同じ＋`問題 / 未検索` に分類 | kept + improved |
| Excel: 値・数式・シート/セル | ＋シート名・定義名・コメント・ハイパーリンク・グラフ/テキストボックス | kept + added (spec §23) |
| Word: 段落・表 | ＋ヘッダ/フッタ/脚注/文末脚注/コメント/テキストボックス | kept + added (spec §24) |
| PPT: 図形・表・ノート | ＋非表示スライド/コメント/グラフ/SmartArt | kept + added (spec §25) |

## Infrastructure

| V4.1 | V5 | Verdict |
| --- | --- | --- |
| 検索中断、一時停止 | 同じ（キャンセルはページ単位で即応） | kept |
| 進捗/最終CSVのTEMP保存 | 検索終了時の自動保存（`%LOCALAPPDATA%\FileScope\sessions`） | replaced — V4 also streamed CSV while searching; V5 does not, so a crash mid-search loses the partial CSV (reported as a known difference) |
| TEMP容量保護・共有ファイル一時コピー | 同じ（専用stagingフォルダ、crash後の清掃つき） | kept |
| 設定・確認済みの保存 | 同じ（V4形式も読み込める。V4ファイルは `settings.v4-backup.json` に退避） | kept + **restored** (RC) |
| 検索履歴 | 同じ（V4の文字列履歴も読み込める） | kept |
| — | SQLite索引/FTS5、Direct/Index等価、カバレッジ表示、プリセット、診断、セルフテスト | added (V5) |

## Removed on purpose

| Item | Reason |
| --- | --- |
| キーワード別タブ | Replaced by the 一致条件 column plus the refine box / facets. It also carried two V4 defects (file name disappearing after 確認済み, list emptying on redraw) that the V5 model cannot reproduce. |
| 「設定保存」ボタン | Settings save automatically; a manual save button invites "did I save?" doubt with no benefit. |
| V4の逐次CSV自動保存 | V5 writes the CSV at the end of the search and keeps the result set in memory. Streaming per-hit CSV would double the write path for no user-visible gain; documented as a known difference rather than silently dropped. |
