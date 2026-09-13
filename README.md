# FileScope V5

Windows向けの、社内資料を横断検索するためのローカル全文検索ツールです。
Excel、PDF、Word、PowerPoint、テキスト/CSV/ログ、テキストとして判定できる未知拡張子を対象に、
ローカルフォルダ・SMB共有フォルダ・OneDrive/SharePoint同期フォルダを検索します。

**検索語や本文を外部のAI/API/クラウド検索サービスへ送信しません。**
OCRも全文索引も、このPCの中だけで処理します。

## 30秒でわかる使い方

1. 「検索場所」でフォルダを選ぶ
2. 「検索」欄に語を入れる
3. 「検索開始」を押す

結果は **1ファイル＝1行** で表示されます。行を選ぶと右側に根拠（セル・ページ・段落）が出ます。

## 検索式

覚えなくても「条件」ボタンから同じ条件を作れます（条件ビルダー）。
検索欄に直接書く場合は次の通りです。

| 書き方 | 意味 |
| --- | --- |
| `AAA BBB` | 「空白区切りの既定」設定に従う（初期値はOR） |
| `AAA & BBB` / `AAA かつ BBB` | 同じファイル内に両方（別シート・別ページでも可） |
| `AAA, BBB` / `AAA または BBB` | どちらか |
| `AAA | BBB` | どちらか（V5の正式なOR表記） |
| `!AAA` / `-AAA` | 含まない（`-123` `ABC-123` は部品番号として扱います） |
| `(AAA | BBB) & CCC` | 括弧でまとめる |
| `"耐久 試験"` | 完全一致フレーズ |
| `2of(AAA, BBB, CCC)` | 3候補中2つ以上（`3of(A,B,C,D,E)` のように一般化） |
| `NEAR(電源, ノイズ, 100)` | 同じセル/ページ内で100文字以内 |

### メタ条件（ファイル情報で絞る）

```
type:pdf                  拡張子グループ（pdf / excel / word / ppt / text / archive）
ext:xlsx,xls              拡張子を直接指定
name:評価                 ファイル名に含む
path:耐久                 フォルダパスに含む
size:<50MB                サイズ（<, <=, >, >=, 1MB..100MB。単独指定は「以上」）
modified:>=2025-01-01     更新日（範囲は 2025-01-01..2025-12-31）
source:smb                local / smb / onedrive
confirmed:false           確認済みかどうか
ocr:true                  OCRで見つかった資料
```

`type:pdf & 耐久` のように検索語と組み合わせられます。

## 検索モード

| モード | 動き |
| --- | --- |
| 高速 | 索引とファイル情報を中心に検索。オンライン専用ファイルは取得しません。 |
| 標準（初期値） | 索引済みは索引を使い、新規・変更・未索引だけ本文を読みます。PDF OCRは「自動」。 |
| 完全 | 索引を信用せず本文を確認。必要ならオンライン専用ファイルを取得し、全ページOCRも可能。 |

## OCR（スキャンPDF）

OCRなしでもFileScopeは動作します。文字層のあるPDFはそのまま検索でき、
文字層のないページは「OCR未導入」としてスキップされ、通常検索には影響しません。

スキャンPDFも検索したい場合は、Tesseract OCR本体をインストールしてください
（`jpn` と `eng` の言語データを推奨）。FileScopeは次の場所から `tesseract.exe` を自動検出します。

- 環境変数 `TESSERACT_CMD`
- `PATH`
- `%ProgramFiles%\Tesseract-OCR`
- `%ProgramFiles(x86)%\Tesseract-OCR`
- `%LOCALAPPDATA%\Programs\Tesseract-OCR`
- `%LOCALAPPDATA%\Tesseract-OCR`
- FileScopeと同じフォルダの `Tesseract-OCR` / `tesseract`

OCRで見つかった行は結果に `OCR` と表示され、`ocr:true` で絞り込めます。
同じファイルを毎回OCRしないよう、認識結果だけをローカルにキャッシュします（画像は保存しません）。

## 索引（キャッシュ）

- 保存先: `%LOCALAPPDATA%\FileScope\index`
- 差分更新: パス・サイズ・更新日時・抽出プログラムの版・OCR設定が同じファイルは再抽出しません
- 容量上限: OFF / 512MB / 1GB / 2GB / 5GB / カスタム
- 容量に達すると **古い索引を削除せず**、新規作成だけを停止します（結果が欠けるより遅い方が安全なため）
- 画面に「索引: 8,421ファイル / 1.2GB」のように状態を表示します
- 索引が壊れている場合は検出してDirect検索へ自動で切り替えます（本体は起動します）

索引は候補を絞るために使い、**合否判定は本文検索と同じロジック**で行います。
そのため「索引ON/OFFで結果が変わる」ことはありません（自動テストで検証しています）。
日本語の1〜2文字検索（評価・電源・落下など）も必ず検索できます。

## OneDrive / SharePoint / SMB

- オンライン専用（Files On-Demand）ファイルはWindowsのファイル属性だけを見て判定します（勝手にダウンロードしません）
- 取得方針は「自動 / 取得しない / 取得して検索する」から選べます（高速モードは既定で取得しません）
- FileScopeがOneDriveの設定（Always keep / Free up space）を変更することはありません
- SMB共有のOffice/PDFは、必要に応じてローカルTEMPへ1回コピーして解析します（サイズ・空き容量を確認、終了時に削除）

## 結果の見方

- `済` … 確認済み（薄いグレー）。Spaceキーまたは右クリックで切替
- `一致条件` … どの条件でヒットしたか（`AAA&BBB` や `2of(...)`）
- `Hit数` の `+` … 条件が成立した時点で読み込みを止めたため、件数は下限値です。
  行を選ぶとそのファイルだけ最後まで読み直し、正確な件数と根拠に更新します（右側プレビュー）
- `種類` の `*` … 索引から返した結果（本文は読んでいません）
- 上部のカバレッジ表示 … `発見 / 検索完了 / Hit / 索引 / 実読込 / OCR / Online-only skip / 警告 / エラー`
- `問題 / 未検索` 一覧 … 読めなかったファイル（権限なし・暗号化PDF・OCRタイムアウト等）は結果と分けて表示

## ショートカット

`Ctrl+L` 検索欄 / `Enter` 検索・開く / `Ctrl+Enter` フォルダを開く / `Ctrl+F` プレビュー内検索 /
`F3`・`Shift+F3` 次の一致・前の一致 / `Ctrl+S` CSV保存 / `Ctrl+B` 条件ビルダー /
`Ctrl+D` 詳細設定 / `Ctrl+I` 索引の状態 / `F5` 再検索 / `Esc` 中断・閉じる / `Space` 確認済み

## プライバシー

- 外部AI/API、外部OCR、外部全文検索、テレメトリ、自動アップデート確認は行いません
- ログには検索語や本文を書きません（日時・コンポーネント・エラー種別・パス程度）
- 設定・索引・ログ・クラッシュレポートはすべて `%LOCALAPPDATA%\FileScope` の下にだけ保存します

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| 検索結果が0件 | 上部のカバレッジで「未検索」「エラー」を確認してください。`問題 / 未検索` に理由が出ます |
| スキャンPDFがヒットしない | 診断ボタンでOCR状態を確認してください |
| 検索が遅い | モードを「高速」に、または索引を有効（詳細設定）にしてください |
| 結果が古い | 索引は変更を検出して再抽出します。すぐ反映したい場合は「完全」モードで検索してください |
| 起動しない | `%LOCALAPPDATA%\FileScope\logs` と `crashes` を確認してください。設定が壊れていても既定値で起動します |
| 索引を消したい | 「索引」ボタン → 削除（フォルダ単位／すべて）。元のファイルは削除されません |
| 環境を確認したい | `python scripts/check_environment.py` またはアプリの「診断」ボタン |

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python FileScope.py
```

ヘッドレス（GUIなし）でも使えます。

```powershell
python -m filescope --diagnostics
python -m filescope --search "D:\資料" --query "2of(AAA,落下,耐久)" --mode standard
```

## 開発者向け

- 構成と設計: [ARCHITECTURE.md](ARCHITECTURE.md)
- 第三者ライブラリ: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- v4.1ベースラインの状態: [baseline/README.md](baseline/README.md)
- テスト: `python -m pytest tests`
- ベンチマーク: `python scripts/benchmark.py --generate --count 2000 --include-documents --json bench_results.json`
