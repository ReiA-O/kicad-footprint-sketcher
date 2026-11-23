**結論：数値入力のEnter反映を追加し、Windows70件とKiCad読戻しが通過しました。本人制作のNE555出力8パッドも照合でき、「いい感じ」という評価を記録しました。**

- 確認日：2026年11月23日（JST）。70件の試験は04:12〜04:13。Windowsで実行したローカルコミットは`29d212c8684ef7b1b4a68b89ac5da9bc920a4bd3`、公開用の同内容アプリコミットは[`fcd829e`](https://github.com/ReiA-O/kicad-footprint-sketcher)です。リポジトリ全体のtree SHA `9b6f590fc678386da63aa624d4841e240e884d21`が完全一致しています。
- 正本はmain `a38e767a95b572133f71b0c4c457f541151ebac2`の要件v1.1／GOAL v1.0。本人の追加は作業ブランチの要件v1.6 §8.5.7／GOAL v1.5へ記録し、F01〜F08原文は保持しました。Git認証ヘルパーの応答停止により、内容一致を検証したGitHub API経由で公開しています。検証した元のローカル履歴は保持します。
- 環境：USERのWindows 11 Pro `10.0.26200.9974`、Python 3.14.4、Pillow 12.3.0、Shapely 2.2.0／NumPy 2.5.5、KiCad 10.0.7。Session 1の実ウィジェットとTkイベントを使い、下絵・出力先選択の戻り値のみ検証コードから指定しました。

## Enterの反映先と確認結果

| 操作状態・入力欄 | Enterの動作 |
| --- | --- |
| 選択／移動で選択中のパッドの幅・高さ・角度・R・番号 | 選択パッドへ反映。確定した1変更をUndo1回で復元。 |
| 数値サイズで配置中 | 次に置くパッドの設定を検証・確定し、配置済みパッドは保持。 |
| 未選択で入力、またはドラッグ作成中の幅・高さ | 数値サイズで配置する状態へ移行。 |
| ドラッグ作成中の角度・R・次番号 | 入力を検証して次の作成へ使う。配置済みパッドは保持。 |
| 合体後番号 | 選択図形を合体。 |
| フィレットR | 正の数値を検証して角選択待ちへ移行。角クリックで実適用。 |

- 校正距離、上部「選択番号」、一覧番号セルのEnterも維持しています。未校正・原点未指定、不正な数値・空欄・負寸法・非有限値・大きすぎるRは元データと履歴を保持します。
- 単体52件＋GUI18件、計70件がWindowsで通過しました。失敗・エラー・スキップは0です。LinuxではTk未導入のためGUI18件をスキップし、52件が通過しました。[全実行ログ・入力モデル](editor-tests.json)／[GUI試験](../../../tests/test_gui_interactions.py)
- 入力確定直後も保存し、46出力を同PCのKiCad APIで読み戻しました。延べ89個の論理パッド（Custom 18個）と38個のPaste専用開口、計127エンティティの番号・形状・SMD属性・レイヤーを照合しました。[読戻し記録](editor-kicad.json)／[集計](summary.json)
- 角度差は0°、位置・寸法・Rの最大絶対差は`0.0000004845413263954157 mm`、Cu輪郭の双方向頂点―辺距離の最大は`0.0000004966475205121781 mm`でした。これらは読戻しの観測値で、未決T02の受入許容差ではありません。
- [case-28](case-28.pretty/Editor_Case_28.kicad_mod)は幅2.4 mm、[case-29](case-29.pretty/Editor_Case_29.kicad_mod)は高さ1.6 mm、[case-30](case-30.pretty/Editor_Case_30.kicad_mod)は角度30°、[case-31](case-31.pretty/Editor_Case_31.kicad_mod)はR0.3 mm、[case-32](case-32.pretty/Editor_Case_32.kicad_mod)は番号`R9`をEnterで反映した直後の例です。
- PNG／JPEGの補助2点方式による600 px・10 mm校正、中央／任意原点、矩形・角丸配置、書き出しも再実施しました。4出力は初回とバイト一致し、KiCad読戻し全8パッドの差は0でした。[GUI記録](regression/gui-result.json)／[KiCad記録](regression/kicad-readback.json)
- 白16 pt太字・濃色縁取り・中心十字を避ける配置も維持しました。694描画観測で、縁取りを含む文字枠と中心±6 px領域の重なりは0でした。視認性の本人評価やT02の採用基準とは区別します。

## 本人制作の成果物

- 本人から[NE555_SOIC8_Practice.kicad_mod](owner-output/NE555_SOIC8_Practice.kicad_mod)を受領し、「これを使って作った成果物はいい感じ」という評価を得ました。[PRへの本人評価記録](https://github.com/ReiA-O/kicad-footprint-sketcher/pull/2#issuecomment-7084185939)。原本を編集せず、受領時点のコピーを収録しました。
- SHA256は`5115c8367cb2f9dc158f2bc111c2a7c7d08a30bafe8b0a64ae1df265f8f26427`。同PCのKiCad 10.0.7で番号1〜8の8個、角丸SMD、幅1.55×高さ0.6 mm・R0.05 mm・角度0°・F.Cu/F.Paste/F.Maskを確認しました。元ファイルとの差の最大絶対値は`0.0000002647500760843968 mm`でした。[元ファイルの属性](owner-output/owner-expected.json)／[読戻し](owner-output/owner-readback.json)
- この本人評価は実際の制作物に対するものです。ファイルにGUIのコミットは記録されておらず、生成版を特定できません。最新のEnter操作や段階全体の正式な採否、データシート寸法に対する製造用の受入合格とは読み替えていません。

## 本人確認用の状態・未確認

- Windows画面へ「NE555（Enter反映対応）」を開き、実行コミット・表示済み・Canvas 1229×629・白番号の実フォント16 pt太字を確認しました。[起動状態](gui-ready.json)。デスクトップの「NE555で操作テスト」も新版を開き、従来ウィンドウは閉じていません。
- 未確認：最新Enter版の手操作の感触は未記録です。表示したGUIで選択パッドの各数値と次配置の設定をEnterで反映し、修正点がないか確認します。本人の成果物評価を、この追加操作の確認へ転記していません。
- 未確認：今回の各出力と本人ファイルをKiCadエディター画面で開いた表示、およびWindowsのFootprint Checkerです。今回はAPI読戻しのため、出力をエディターへインポートして属性・Cu／Mask・PasteとChecker結果を確認します。旧PNG／JPEGのエディター開封・属性一致は旧コミットの本人確認として保持しています。
- 未確認：標準選択ダイアログを含む最新コミットの本人の全手順です。自動試験では戻り値を指定したため、通常GUIで下絵と出力先を選択して確認します。第1段階の正式な採否も未記録です。

合体CustomのW/H・全体R・四隅resizeは今回対象外で、寸法変更はUndoで合体前へ戻します。円弧は最大弦誤差0.0001 mmの折線近似です。編集用保存・拘束・既存ファイル再編集などの後続範囲は維持します。

[試験・確定直後の出力保存](harness/run_enter_tests.py)／[KiCad照合](harness/read_compound_kicad.py)／[PNG／JPEG回帰](harness/regression_png_jpeg.py)／[本人ファイル照合](harness/read_owner_output.py)を収録しました。完全な証跡ZIPのSHA256は`2548bc0197a3bf6d3d15cca7e2cda4606f09858e0c7303ce590fbd99d39c2284`。ソースハッシュはWindowsのCRLFを考慮して実行コミットと照合済みです。PRはDraftを維持し、mainへの自己マージと次段階への移行は行っていません。
