# kicad-local — 画面なしでも基板を扱える KiCad 8.0.9 の改良版

KiCad 8.0.9 に、次の 2 つを足したものです。

- 画面を開かずに基板と回路図を扱う道具（配線・部品の移動・DRC/ERC・3D 画像・製造データの比較・回路の計算）
- KiCad 9〜11 や本家の MR からの機能と修正

**保存形式は KiCad 8 のまま**なので、直したファイルはふつうの KiCad 8 でそのまま開けます。

## できること

入口は `kicad-local` 1 つです（`kicad-local help` で一覧が出ます）。

```
kicad-local drc 基板.kicad_pcb            # DRC の要点（種類ごとの件数と位置）
kicad-local erc 回路図.kicad_sch
kicad-local lint 回路図.kicad_sch          # 設計の定石からの外れ（外部の線の保護・ベース抵抗・電源のコンデンサ…）
kicad-local render 基板.kicad_pcb iso      # 画面なしで 3D 画像
kicad-local stats 基板.kicad_pcb           # 部品数・穴径など

# 部品を動かし、配線する（手順は標準入力でも可）
kicad-local route 基板.kicad_pcb 出力.kicad_pcb - --instructions 手順.md <<'EOF'
findspace R10 0.25                  # 置ける場所の候補
drag R10 1,0                        # 動かすと、つながった線は迂回して引き直し、シルクの文字も逃がす
autoroute R10.2 C5.1                # ビア 1 個までの経路を自動で探す
check                               # その場で DRC
EOF

kicad-local sch 入力.kicad_sch 出力.kicad_sch 手順   # 回路図を画面なしで直す（保存後に接続と部品表の差・ERC）
kicad-local pcbsync 回路図 基板 出力                # 回路図の変更を基板へ
kicad-local fabdiff 前.kicad_pcb 後.kicad_pcb 比較/  # 製造データを層ごとに比べ、差分画像と場所の一覧
kicad-local sim run 手順.toml --out 結果/            # 回路図から自動で SPICE にして計算（ngspice）
kicad-local review 新しい版 前の版 --out 確認/        # ERC・DRC・3D・差分・計算を 1 回で → 報告.md
```

`--instructions` を付けると、同じ操作を KiCad の画面で人が再現できる手順書も出ます。
詳しい使い方は [docs/guide.md](docs/guide.md)、道具は [tools/README.md](tools/README.md)、回路の計算は [tools/ksim/README.md](tools/ksim/README.md)。

## 取り込んだ変更

| # | 内容 | 出どころ |
|---|---|---|
| 00 | kicad_route（画面なしの配線コマンド）＋ SWIG の参照数の不具合の修正 | 独自 |
| 01 | 押しのけ配線ルーターの不具合修正 19 件（8.0.9 向けに 3 件を調整） | KiCad 9〜11（master） |
| 02 | `kicad-cli pcb drc --refill-zones --save-board` | KiCad 10 |
| 03 | DRC の JSON に、違反の位置と層 | MR !2780 |
| 04 | 部品を動かすとき、つながった線を迂回させて引き直す | MR !2188 |
| 05 | `kicad-cli pcb export stats`（基板の統計） | KiCad 10 |
| 06 | `kicad-cli pcb render`（画面なしの 3D レイトレース画像） | KiCad 9 ＋ master |
| 07 | kicad-route の使い勝手（help・info・check・シルクの自動退避・手順書） | 独自 |
| 08 | `kicad-gerber`（ガーバーの情報と差分）、fabdiff、renderdiff | KiCad 11 から移植 |

それぞれの出どころ（コミット）・8.0.9 向けの調整・確かめたことは [patches/README.md](patches/README.md) にあります。
ファイル形式を変える機能（KiCad 9 以降の保存形式）は入れていません。

## 作り方（Linux）

1. ビルドに要るパッケージを入れる（Ubuntu/Debian。一覧は [patches/notes/builddeps.txt](patches/notes/builddeps.txt)）

   ```
   sudo apt install $(cat patches/notes/builddeps.txt)
   ```

2. KiCad 8.0.9 のソースを取ってきて改良を当てる（既定の場所は `~/src/kicad-8.0.9`）

   ```
   ./apply.sh
   ```

3. ビルドする（既定の場所は `~/src/build-8.0.9`。Ubuntu 24.04 で確かめた設定）

   ```
   cmake -S ~/src/kicad-8.0.9 -B ~/src/build-8.0.9 -G Ninja -DCMAKE_BUILD_TYPE=Release \
         -DKICAD_BUILD_PNS_DEBUG_TOOL=ON -DKICAD_SCRIPTING_WXPYTHON=OFF -DKICAD_BUILD_I18N=OFF
   ninja -C ~/src/build-8.0.9 -j4 kicad-cli pcbnew_kiface eeschema_kiface cvpcb_kiface kicad_route kicad_gerber \
         pcbnew/_pcbnew.so s3d_plugin_vrml s3d_plugin_oce s3d_plugin_idf
   ```

   `KICAD_BUILD_PNS_DEBUG_TOOL=ON` は kicad_route（`qa/tools/pns/`）を作るために要ります。
   メモリ 7GB で `-j5` まで、フルビルドで 20〜30 分ほどです。インストールはせず、ビルドした場所から動かします
   （`bin/` の入口が `KICAD_RUN_FROM_BUILD_DIR` などを設定します）。

4. `bin/` を PATH に入れる（または `bin/` の中身を `~/.local/bin` に置く）

   ```
   export PATH="$PWD/bin:$PATH"
   kicad-local help
   ```

   ビルドした場所を変えたときは `KICAD_LOCAL_BUILD` を、ソースの場所を変えたときは `KICAD_LOCAL_SRC` を設定します。
   部品ライブラリ（フットプリント・シンボル・3D モデル）は、システムに入れた KiCad 8 のもの（`/usr/share/kicad`）を使います。

## 試験

```
kicad-local test        # = test/regress.sh（数十秒）
```

KiCad 8.0.9 に付いてくるデモの基板（pic_programmer）と回路図（interf_u）で、配線の手順を流して結果を基準（`test/baseline/`）と比べ、
道具ひとつずつの動作も確かめます。

## フォルダ

| 場所 | 中身 |
|---|---|
| `patches/` | KiCad 8.0.9 への改良。`ALL-combined.patch` が全部をまとめたもの（`apply.sh` が当てる）。番号付きのパッチは来歴の記録 |
| `bin/` | 入口（`kicad-local`、`kicad-route`、`kicad-gerber`、`kicad-cli-local`、`kicad-python`） |
| `tools/` | Python の道具（review・lint・sch・sim・pcbsync・silkfix ほか） |
| `test/` | 回帰試験 |
| `docs/guide.md` | 手引き（使い方の流れ、最新版から機能を取り込むやり方、ハマりどころ） |

## ライセンス

GPL-3.0-or-later（KiCad と同じ）。KiCad 本体は KiCad の開発者によるもので、本家から取り込んだ変更の作者は各パッチに記録しています。
