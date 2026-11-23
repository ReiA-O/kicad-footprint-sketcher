# テストと動作確認

対象環境はWindows、Python 3.14（Tcl/Tkを含む）、KiCad 10.0系です。導入手順は[README](../README.md)を参照してください。

## ローカルで実行する

リポジトリのルートで、依存ライブラリを導入した仮想環境のPythonを使います。

```powershell
.\.venv\Scripts\python.exe tools/check_publication.py
.\.venv\Scripts\python.exe -m compileall -q footprint_sketcher tests tools examples/requirements-demo/generate.py
```

全テストは、画面を表示できるWindowsデスクトップで実行してください。GUIテストではTkのウィンドウが開閉します。次の実行方法は、テストの失敗・エラーに加え、0件の実行やスキップも失敗として扱います。

```powershell
@'
import sys
import unittest
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(f"Tests: {result.testsRun}; skips: {len(result.skipped)}")
sys.exit(0 if result.testsRun and result.wasSuccessful() and not result.skipped else 1)
'@ | .\.venv\Scripts\python.exe -
```

GUIテストはTkのイベントを再生し、ファイル選択ダイアログの戻り値を検証コードから指定します。通常のファイル選択、手操作での使いやすさ、KiCadフットプリントエディターの画面やFootprint Checkerは、別に確認する項目です。

## サンプルを再生成する

既存のサンプルを上書きしない出力先を指定します。生成されるデータは操作練習用で、特定部品の推奨ランド設計ではありません。

```powershell
.\.venv\Scripts\python.exe examples/requirements-demo/generate.py --out out/sample-check
```

`out/sample-check/Requirements_Demo.kfsproj`を本アプリで開き、下絵・パッド・図形と2つの拘束が復元されることを確認します。`out/sample-check/Requirements_Demo.pretty/Requirements_Demo.kicad_mod`はKiCad 10.0系へインポートできます。確認用フォルダはコミットしないでください。

## GitHub Actions

[Checksワークフロー](../.github/workflows/ci.yml)は、ブランチへのpushとPRで次を実行します。

- Windows／Python 3.14で依存ライブラリを導入する。
- 追跡ファイルとZIP・専用プロジェクト内部の公開チェック、Pythonの構文確認を行う。
- GUIを含むテストを実行し、スキップがあれば失敗する。
- 一時フォルダへサンプルを生成し、プロジェクトとKiCad出力を再読込みする。プロジェクト再開後の再出力が生成直後と同一バイトになることも確認する。

ワークフローは読み取り権限だけを使い、認証情報をチェックアウトへ保存せず、配布物のアップロードは行いません。`pull_request_target`は使いません。Tkを表示できないrunnerでもGUIテストを合格に置き換えず、失敗を報告します。

通常のCIはチェックアウトした内容を検査します。履歴の公開チェックは、現在の公開ブランチを新しくcloneした作業コピーで別に実行してください。古い履歴や旧PR参照を手元へ残している場合、その情報も検出対象になります。

```powershell
.\.venv\Scripts\python.exe tools/check_publication.py --history
```

このチェックは、あらゆる秘密情報や画像中の文字を検出する仕組みではありません。[公開用の記録の扱い](PUBLICATION.md)も確認してください。

## 確認済みの結果と限界

2026年11月23日時点の確認は、次のように分けています。

| 確認 | 結果と範囲 |
| --- | --- |
| 厳しめ自己レビュー後のWindows実機 | 185件（純粋ロジック141件＋GUI44件）が通過。KiCad 10.0.7で148出力を照合。[当時の記録](verification/windows-strict-review-20261123/README.md)。 |
| 公開整備時のLinuxでの再実行 | Python 3.14.4、Pillow 12.1.1、Shapely 2.2.0、NumPy 2.5.5、pypdfium2 5.15.2で純粋ロジック141件が通過。Tk・ディスプレイがないためGUI44件はスキップ。Windowsでの185件再実行やLinux正式対応の確認とは扱わない。 |
| 公開整備時のサンプル確認 | 操作サンプルを一時フォルダへ生成し、`.kfsproj`と`.kicad_mod`の再読込み、プロジェクト再開後の同一バイト出力を確認。 |
| GitHub Actions | 公開整備で追加した自動確認。実行結果はリポジトリのActions画面で確認する。ローカルでの通過をCIの成功として扱わない。 |

出力の実用確認では、データシートの推奨ランド寸法・端子番号・面・開口を照合し、KiCad側でレイヤーとChecker結果を確認してください。操作サンプルや自動検証の通過は、個別部品の設計承認を意味しません。
