# KiCad Footprint Sketcher

データシートの下絵を実寸に校正し、表面SMDパッドを配置して、KiCad 10.0系へ渡す `.kicad_mod` を書き出すデスクトップGUIです。

## Windowsで起動する

Python 3.14 と Tcl/Tk を備えた Windows 環境で、リポジトリのルートから実行します。

```powershell
py -3.14 -m pip install -r requirements.txt
py -3.14 -m footprint_sketcher
```

## サンプルで確認する

1. [PNG](examples/stage1/land-pattern.png) または [JPEG](examples/stage1/land-pattern.jpg) を下絵として開きます。
2. 校正点 A と B の十字の中心を順にクリックし、実距離に `10` mm を入力して校正します。画像上の点間距離は 600 px で、縮尺は約 0.016667 mm/px です。
3. 下絵の中央を原点にします。X は右、Y は下が正です。表の番号と寸法を指定し、各パッドの中心の十字をクリックして配置します。

   | 番号 | 中心 X / Y (mm) | 幅 × 高さ (mm) | 角度 | 角 R (mm) |
   | --- | --- | --- | --- | --- |
   | 1 | −2.5 / −1.0 | 1.6 × 1.2 | 0° | 0 |
   | 2 | +2.5 / +1.0 | 2.0 × 1.2 | 30° | 0.25 |

4. フットプリント名に `Stage1_SMD_Demo` を入力し、書き出し先を選びます。両パッドのレイヤーは `F.Cu`、`F.Paste`、`F.Mask` です。
5. 出力を `.pretty` フォルダに配置し、KiCad 10.0系のフットプリントエディターで開きます。座標・寸法・角度・レイヤーを [出力例](examples/stage1/Stage1.pretty/Stage1_SMD_Demo.kicad_mod) と照合できます。

表の座標は、下絵の十字の中心を選んだときの値です。

## 第1段階の動作確認

- Windows Server 2025（10.0.26100）で、PNG・JPEGそれぞれの下絵表示からパッド配置、ファイル出力までと、パッド角度の書き出しを確認しました。[Windowsの検証ログ](https://github.com/ReiA-O/kicad-footprint-sketcher/actions/runs/45043519579)
- Ubuntu 26.04.1でPNGとJPEGをそれぞれ開き、校正、原点指定、パッド配置、ファイル出力を確認しました。両方の出力は一致しました。[操作画面](examples/stage1/sketcher-preview.png)
- Ubuntu上のKiCad 10.0.8で出力例を開き、原点、番号、位置、寸法、角度、角のR、レイヤーを照合しました。表の入力値との差はこの例では0でした。Footprint Checkerは、段階1の作図範囲外であるCourtyardの未定義を報告しました。[KiCadで開いた画面](examples/stage1/kicad-preview.png)

第1段階の採用範囲は [GOAL.md](GOAL.md)、決定記録は [要件定義書 §8.5](files/要件定義書.md#85-決定記録) を参照してください。
