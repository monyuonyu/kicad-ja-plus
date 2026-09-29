# kicad-ja-local — 画面なしでも基板を扱える KiCad の改良版

KiCad に、次のものを足したものです。版は 2 つあります。

| 版 | 向いている使い方 |
|---|---|
| **10.0.6**（おすすめ） | 最新の安定版に合わせたもの。本家の MR から、製造データや DRC の不具合の修正も取り込んでいる。**画面が無いサーバーや CI、AI からもそのまま動く** |
| 8.0.9 | 直したファイルを KiCad 8 のまま使い続けたいとき |

足したもの:

- 画面を開かずに基板と回路図を扱う道具（配線・部品の移動・DRC/ERC・3D 画像・製造データの比較・回路の計算）
- KiCad 9〜11 や本家の MR からの機能と修正

**保存形式は、それぞれの版の本家の KiCad と同じ**なので、直したファイルはふつうの KiCad（10 または 8）でそのまま開けます。

## できること

入口は `kicad-local` 1 つです（`kicad-local help` で一覧が出ます）。

```
kicad-local drc 基板.kicad_pcb            # DRC の要点（種類ごとの件数と位置）
kicad-local erc 回路図.kicad_sch
kicad-local lint 回路図.kicad_sch          # 設計の定石からの外れ（外部の線の保護・ベース抵抗・電源のコンデンサ…）
kicad-local render 基板.kicad_pcb iso      # 画面なしで 3D 画像
kicad-local stats 基板.kicad_pcb           # 部品数・穴径など
kicad-local drc 基板.kicad_pcb --strict    # DRC が「違反 0」でも見逃す 3 つ（ネットの無いパッド・ネットクラスより細い配線・古いベタ）も
kicad-local why 基板.kicad_pcb             # 未配線の組ごとに「なぜ引けないか」（キープアウト・ほかのネットの銅・外形）を層ごとに
kicad-local view 基板.kicad_pcb --drc --net GND   # 2D の図を注釈つきで画像に（ネットの強調・DRC の違反に番号）
kicad-local autoroute 入力.kicad_pcb 出力.kicad_pcb   # 基板全体を Freerouting で自動配線し、ベタの塗り直しと DRC まで
kicad-local finish outline|zone|stitch|islands|widen 入力 出力 ...   # 外形・ベタ・スティッチングビア・島の除去・電流から配線を太く

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
**AI やスクリプトから使う**: drc・erc・why・view・stats・autoroute・finish は `--json` で機械が読める形を出し、drc・erc・why は要確認があれば
終了コード 1 を返します（文章を読み解かなくても、結果で次の判断ができる）。10.0.6 版は画面（DISPLAY）が無くても動きます。
詳しい使い方は [docs/guide.md](docs/guide.md)、道具は [tools/README.md](tools/README.md)、回路の計算は [tools/ksim/README.md](tools/ksim/README.md)。

## 取り込んだ変更

版ごとに、出どころ（本家のコミットや MR）・その版に合わせた調整・確かめたことを書いています。

- [10.0.6](patches/10.0.6/README.md): このリポジトリの改良（画面なしの配線コマンド、DRC の JSON に位置と層、部品を動かすときの迂回、ガーバーの比較ほか）と、**本家の MR 11 件**（ODB++・IPC-2581 の製造データの誤り、回路図との整合チェックのバリアント対応、`--theme` にファイルほか）
- [8.0.9](patches/8.0.9/README.md): 00〜08（ルーターの修正 19 件、KiCad 9〜11 の機能の取り込みほか）

ファイルの形式を変える機能は入れていません。

## 作り方（Linux）

1. ビルドに要るパッケージを入れる（Ubuntu/Debian。一覧は [patches/notes/builddeps.txt](patches/notes/builddeps.txt)）

   ```
   sudo apt install $(cat patches/10.0.6/builddeps.txt)     # 8.0.9 なら patches/8.0.9/notes/builddeps.txt
   ```

2. KiCad のソースを取ってきて改良を当てる（既定の場所は `~/src/kicad-<版>`）

   ```
   ./apply.sh 10.0.6        # または ./apply.sh 8.0.9
   ```

3. ビルドする（Ubuntu 24.04 で確かめた設定。8.0.9 なら 10.0.6 を 8.0.9 に読み替える）

   ```
   cmake -S ~/src/kicad-10.0.6 -B ~/src/build-10.0.6 -G Ninja -DCMAKE_BUILD_TYPE=Release \
         -DKICAD_BUILD_PNS_DEBUG_TOOL=ON -DKICAD_SCRIPTING_WXPYTHON=OFF -DKICAD_BUILD_I18N=OFF
   ninja -C ~/src/build-10.0.6 -j4 kicad-cli pcbnew_kiface eeschema_kiface cvpcb_kiface kicad_route kicad_gerber \
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

   ビルドした場所は `KICAD_LOCAL_BUILD` で指定します（既定は `~/src/build-10.0.6`。8.0.9 なら `export KICAD_LOCAL_BUILD=~/src/build-8.0.9`）。
   ソースの場所を変えたときは `KICAD_LOCAL_SRC` を設定します。
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
| `patches/10.0.6/` | KiCad 10.0.6 への改良（番号順のパッチ。本家の MR は作者の名前つき） |
| `patches/8.0.9/` | KiCad 8.0.9 への改良。`ALL-combined.patch` が全部をまとめたもの。番号付きのパッチは来歴の記録 |
| `bin/` | 入口（`kicad-local`、`kicad-route`、`kicad-gerber`、`kicad-cli-local`、`kicad-python`） |
| `tools/` | Python の道具（review・lint・sch・sim・pcbsync・silkfix ほか） |
| `test/` | 回帰試験 |
| `docs/guide.md` | 手引き（使い方の流れ、最新版から機能を取り込むやり方、ハマりどころ） |

## ライセンス

GPL-3.0-or-later（KiCad と同じ）。KiCad 本体は KiCad の開発者によるもので、本家から取り込んだ変更の作者は各パッチに記録しています。
