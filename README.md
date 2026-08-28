# FileScope

Windows向けのローカル／社内ファイル横断検索ツールです。

Excel、PDF、Word、PowerPoint、Text/CSV/Log、テキストとして判定できる未知拡張子を対象に、ローカルフォルダ・共有フォルダ・OneDrive/SharePoint同期フォルダを検索します。ファイル内容や検索語を外部AI/APIへ送信しません。

## 主な機能

- Excel / PDF / Word / PowerPoint / Text の内容検索
- ファイル全体を単位にした AND / OR 検索
- `2of(A,B,C)` による「3候補中2つ以上」の検索
- 部品番号モード（ハイフン・空白差の吸収、`*`ワイルドカード）
- PDF OCR：OFF / 自動 / 全ページ
- Tesseract OCRによる日本語＋英語OCR（ローカル処理）
- SharePoint / OneDrive同期・SMB共有で、列挙しながら検索を開始
- 検索結果の確認済み管理、並び替え、CSV保存
- 大量ヒット時のRAM・TEMP容量保護

## 検索式

- `A B` : 右側の OR / AND 選択に従う
- `A&B` : AND
- `A,B` : OR
- `2of(A,B,C)` : A/B/C のうち同一ファイル内に2語以上あれば一致
- 除外欄 : 指定語を除外

AND条件は同じセルや同じページに限定されません。たとえばExcelの別シート、PDFの別ページに各語が存在しても、同じファイル内なら成立します。

## 起動

Python 3.12系を想定しています。

```powershell
pip install -r requirements.txt
python FileScope.py
```

## PDF OCR

OCRなしでもFileScope本体は動作します。文字層を持つ通常PDFは検索できます。

スキャンPDFも検索する場合は、`requirements.txt`のPythonパッケージに加えてTesseract OCR本体をWindowsへインストールしてください。`jpn`と`eng`言語データを推奨します。

FileScopeは以下などから `tesseract.exe` を自動検出します。

- `C:\Program Files\Tesseract-OCR\tesseract.exe`
- `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`
- `%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe`
- FileScopeと同じフォルダ配下の `Tesseract-OCR\tesseract.exe`

OCRの「自動」は、まずPDFの通常文字抽出を行い、文字層がほぼないページだけOCRします。PDF解析・OCRは同時1ファイルに制限しています。

## 注意

- OneDrive / SharePointのオンライン専用ファイルは、内容を読む際にWindows/OneDrive側でダウンロードが発生する場合があります。
- 暗号化PDFなど、解析できないファイルはINFO/ERRORとして結果に表示されます。
- 設定と確認済み情報は通常 `%LOCALAPPDATA%\FileScope\settings.json` に保存されます。

## Current

Current source: FileScope v4.1
