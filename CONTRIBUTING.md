# 不具合報告・変更提案

KiCad Footprint Sketcherは開発中のWindows向けGUIです。使い方は[README](README.md)、詳しい対象範囲は[要件定義書](files/要件定義書.md)を参照してください。

## 不具合を報告する

[Issues](https://github.com/ReiA-O/kicad-footprint-sketcher/issues)の「不具合の報告」から、次を分かる範囲で記載してください。

- 使用したコミット、Windows・Python・KiCadのバージョン。
- 再現手順、期待した結果、実際の結果、画面のエラー。
- 問題を再現できる最小のサンプル。PNG/JPEG、`.kfsproj`、`.kicad_mod`などは必要なものだけ添付してください。

ログやスクリーンショットは、ユーザー名・実パス・認証情報が含まれていないか確認してください。`.kfsproj`は下絵を内包します。データシートや顧客資料など、公開する権利のないデータは添付せず、公開可能な最小例へ置き換えてください。秘密情報や悪用につながる問題の扱いは[SECURITY](SECURITY.md)を参照してください。

機能提案では、やりたい作業と現在困る点を説明してください。提案は既存の要件・判断と照合して検討します。取り込みや対応時期は個別に判断します。

## コードを変更する

[LICENSE](LICENSE)に従って利用・改変できます。GitHub標準のFork等は同ライセンス第4項の範囲で認められ、それ以外の本体の再配布には事前の書面による許可が必要です。

大きな変更は、実装前にIssueで目的と影響範囲を共有してください。[GOAL](GOAL.md)に従い、確定要件と判断待ちを区別し、目的別の作業ブランチからPRで提案します。PRには変更理由、動作の変化、確認した環境・結果、未確認の点を記載してください。自分に公開・提供する権利のある変更だけを含め、第三者由来のコードや素材があれば出典と利用条件を明記してください。

READMEの手順で仮想環境へ依存ライブラリを導入し、リポジトリのルートで確認します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_publication.py
```

GUI試験にはTcl/Tkと利用可能な画面が必要です。画面がない環境でのスキップはGUI操作の通過を意味しません。出力に関わる変更は、KiCad 10で開き、番号・座標・寸法・角度・レイヤーを確認してください。過去の検証結果を新しい変更の確認結果として転記しないでください。

スキップを失敗として扱う実行方法は[テストの実行](docs/TESTING.md)を参照してください。

履歴を含む公開前チェックは次のコマンドです。[匿名化と履歴変更の説明](docs/PUBLICATION.md)も参照してください。

```powershell
.\.venv\Scripts\python.exe tools/check_publication.py --history
```
