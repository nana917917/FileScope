# FileScope 5.2 Qt 検証候補

このZIPは Windows x64 用です。展開先の `FileScope.exe` を起動します。従来のTk画面も同じEXEの `--classic-ui` から利用できます。Qt版の「表示 → クラシック」はQt内の表示設定です。

## 操作

- 検索場所に複数のパスを入れる場合はセミコロンで区切ります。参照ボタンでも追加できます。
- 「条件を作る…」で「指定数以上含む」を選び、候補を1行ずつ入れます。たとえば 680S、DEV、温度サイクルの3行と必要数2を指定すると、既存エンジンで解釈できる式を生成します。入力済みの式にも追加できます。
- 「追加条件…」は本文を含めて再検索します。一覧上の種類・ファイル名・パスは取得済みの結果を絞り込みます。
- 行を選ぶと一致箇所が先に表示され、本文が後から読み込まれます。F3とShift+F3で一致箇所を移動できます。PDFでは「PDFページ」でページ画像を表示できます。他の形式は「原本を開く」で関連付け先を起動します。

## 技術判断

| 技術 | FileScopeへの影響 |
| --- | --- |
| Tkinter継続 | 従来の起動時間、メモリ、配布サイズを維持しやすく、既存の索引管理なども使えます。結果の仮想描画、テーマ切り替え、WindowsのDPI対応はアプリ側の実装が増えます。 |
| PySide6 / Qt Widgets | Python検索エンジンと設定を共有できます。QTableViewとQAbstractTableModelが表示行を管理し、ファイルごとのウィジェット生成やアイコン問い合わせを省けます。WindowsでQtランタイムと描画のコストが増えます。 |
| C# / WPF | Windows向けのデータバインディングや描画を利用できます。Python検索を再利用するならプロセスの起動、通信、中断、障害時の回復と2系統の配布を設計する必要があります。 |

[Qt model/view](https://doc.qt.io/qt-6/model-view-programming.html) · [WPF](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview)

EverythingとWizFileから検索欄と一覧を中心にする配置を、dnGrepとFileLocator Proから条件入力と一致箇所への導線を参考にしました。dtSearch、X1、Copernicも文書確認の導線を比較するために参照しました。ライセンス製品の導入費や機器別の動作は本候補の比較対象外です。

GUIは既存のSearchSessionのイベントをタイマーで取り出します。SQLiteを開く処理、索引候補、OCR確認、本文抽出、PDFレンダリング、原本を開く処理、CSV保存は別スレッドで行います。結果表への追加を約200ms間隔でまとめ、検索世代とプレビュー選択を識別して結果を反映します。保存済みの設定とSQLiteを同じ場所で読み込み、Qt専用の列順やウィンドウ配置は既存設定にキーを追加して保存します。

PDFページはPDFiumで生成します。Office原本の埋め込みには、Windowsホストと登録済みプレビューハンドラー、またはOfficeや別の変換ソフトに依存する仕組みが必要です。候補では抽出済みの位置付きテキストと関連付けアプリでの原本表示を提供します。[Windows Preview Handlers](https://learn.microsoft.com/windows/win32/shell/preview-handlers)

## 配布とライセンス

PySide6 EssentialsとShiboken6 6.12.0を使用しています。PyInstallerのonedir方式でQt DLLを分離し、ZIPの `licenses/qt` にLGPLv3とGPLv3の本文、対応する上流ソースへのリンク、通知を収録します。ファイル形式のアイコンはFileScopeで描画し、フォルダー参照にはQtの標準アイコンを使用しています。アプリの既存ライセンス、依存ライブラリの条件、SmartScreenに影響するWindows署名は正式配布前に別途評価してください。この候補EXEは未署名です。

- [Qt for Python ライセンス](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt Windows配布](https://doc.qt.io/qt-6/windows-deployment.html)
- [Qt ライセンス条件](https://www.qt.io/licensing/open-source-lgpl-obligations)
- [PyInstaller ライセンスと例外](https://pyinstaller.org/en/stable/license.html)

Windowsの標準設定ではGDIフォントバックエンドを選択します。このPCでDirectWriteよりピークメモリが低かったためです。`QT_QPA_PLATFORM=windows` で通常のQt Windows設定を選べます。[Qt Windowsプラットフォーム引数](https://doc.qt.io/qt-6/qguiapplication.html)

## 計測と確認手順

- ソース比較は `scripts/perf_desktop.py`。検索起動直後のコールバック、1万件の合成テキスト検索、40msタイマーの実間隔、一覧上の位置移動を記録します。検索中のタイマーは周期自体を含みます。
- 配布EXEの比較は `scripts/perf_windows_startup.ps1`。プロセス起動からWindowsがトップレベルウィンドウを検出するまでを記録し、通常の閉じる操作で終了させます。
- Qtテストは `pytest -q tests/test_qt_desktop.py`。1万件超の一覧、選択維持、ソート、テーマ、文字プレビュー、索引の再利用、PDFページ、条件式を検証します。
- GitHub ActionsではWindowsランナー上でポータブルZIPを構築し、凍結版で診断、合成ファイルの検索、CLIのソース版との一致、およびQtウィンドウでの検索・プレビュー・正常終了を確認します。

## 性能結果

同一Windows PCで合成テキスト1万件を直接検索し、索引と自動保存を無効にして各構成を3回測りました。ウィンドウは表示してWindowsの描画経路を使い、操作はプログラムから行いました。

| 指標（中央値） | v5.1.1 | Tk改善版 e24d9a4 | Qt候補 25441ab |
| --- | ---: | ---: | ---: |
| Python内の計測開始から初回イベント処理まで | 726 ms | 688 ms | 1,092 ms |
| 検索開始操作のコールバック | 3.36 ms | 4.19 ms | 0.72 ms |
| 検索処理 | 3.75 秒 | 3.64 秒 | 4.20 秒 |
| CPU時間 | 6.98 秒 | 6.70 秒 | 7.47 秒 |
| ピークWorking Set | 65.1 MiB | 66.3 MiB | 97.7 MiB |
| 40msタイマー間隔 p95 | 86.0 ms | 64.9 ms | 296.2 ms |
| 40msタイマー間隔 最大 | 119.6 ms | 76.3 ms | 340.9 ms |
| 検索後のスクロール位置移動 | 78.1 ms | 42.8 ms | 22.5 ms |

探索開始前の極端値として、v5.1.1の初回コーパスアクセスを含む実行は検索に15.98秒かかりました。各版のヒット数は全実行で1万件です。Qtのウィンドウ構築と初回表示は内訳の1実行でそれぞれ約441 ms、378 msでした。OCR、索引、一時ファイル整理を検索準備のワーカーへ移した後も、Windows上のQtウィンドウ準備に時間がかかっています。起動を30%短縮する目標は達成できていません。

Qtのイベント取り出しコールバックの最大は中央値22.3 msでした。タイマーの長い間隔は、その処理だけでは説明できません。GILの切り替え間隔を短くした単発計測でも間隔は改善せず、別プロセス化の根拠が揃っていないため既存のスレッド構成としました。検索中の応答性を含め、正式版の標準UIを切り替える性能条件は満たしていません。

配布EXEの最初のウィンドウ検出は、同一PCの各3回の中央値でv5.1.1が630 ms、Qt候補が1,102 msでした。Qtの初回は1,435 ms、再起動2回は1,052〜1,102 msでした。キャッシュを消去しない通常起動の計測です。

ネイティブWindowsで日本語表示を目視しました。手動での連続操作、高DPI・複数画面、真のコールドスタート、Defenderの影響、実SMB・OneDrive・実Office混在コーパスでの負荷測定は未確認です。Qtからの索引管理・診断の専用ダイアログとOffice原本の埋め込みは今回の候補に含めていません。既存のTk画面とCLIを利用できます。

## 検証ビルド

- Windows Actions run [44670365369](https://github.com/nana917917/FileScope/actions/runs/44670365369) はアプリのソースコミット `25441ab` を使用しました。Artifactは `FileScope-portable-candidate-6` です。
- `FileScope-v5.2.0-qt-preview-6-win64.zip` は62,833,206 bytesです。
- ZIPのSHA-256は `79f23555fd8b62d8b819150592dc80ef0ff332c9f9a234fd65632ba8c7bb65c1` です。ダウンロードしたZIPから再計算し、マニフェストと一致しました。
- ローカル回帰テストは399件成功、1件スキップ。Windows Actionsでは診断、合成文書での検索一致、Qt画面での検索とプレビュー、正常終了を確認しました。
