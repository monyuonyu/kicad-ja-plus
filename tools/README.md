# kicad-local-tools

修正版 KiCad 8.0.9 と組み合わせて使う Python の道具。入口は `kicad-local`(`kicad-local help`)。
全体の手引きは `docs/guide.md`。

| ファイル | 命令 | 役目 |
|---|---|---|
| review.py | `kicad-local review 新 [前] --sim 手順.toml ...` | ERC・DRC・点検・3D・部品表/接続/製造データの差分・シミュを 1 枚の報告に |
| klint.py | `kicad-local lint 回路図` | 定石からの外れの点検(外部の線の保護・ベース抵抗・向き・電源のコンデンサ・つなぎ忘れ) |
| ksch.py | `kicad-local sch 入力 出力 手順` | 回路図の編集(値・欄・記号・削除・直列挿入・追加・ラベル)。保存後に接続の差と ERC |
| pcbsync.py | `kicad-local pcbsync 回路図 基板 出力 [--swap]` | 回路図の変更を基板へ(部品追加・ネット更新・フットプリント差し替え・古い配線の削除) |
| k3d.py / krender.py | `kicad-local models 基板` / `step 基板 [--placeholder-boxes]` / `render 基板 視点` | 3D モデルの無い部品の一覧、無いモデルを F.Fab/F.CrtYd の外形の箱(高さは欄 Height)で代用した STEP・3D 画像 |
| knetlist.py | `kicad-local netlist 回路図|基板 [--json]` | ネットリストを JSON に(回路図は kicadxml から、基板はパッドのネットから) |
| silkfix.py | `kicad-local silkfix 入力 出力 部品番号 ...` | 部品番号の文字を重ならない所へ(広く探す) |
| ksim/ | `kicad-local sim run 手順.toml` | 回路図から自動で組む ngspice シミュレーション(`ksim/README.md`) |
| sx.py / geo.py | — | KiCad の S 式の読み書き、記号の端子座標 |
| ngs.py | — | libngspice を ctypes で使う最小限の部品 |

回帰試験: `kicad-local test`(`test/regress.sh`)。
