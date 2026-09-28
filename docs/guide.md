# 修正版 KiCad 8.0.9（ローカル）の手引き

2026-09-24〜25 に作成。KiCad 8.0.9 をソースからビルドし、画面なしで基板を扱う道具と、
KiCad 9〜11（開発版）・MR からの機能を取り込んだもの。**保存形式は KiCad 8 のまま**
（KiCad 8 で開ける）。システムの KiCad 8（apt）はそのまま残してある。

## まず見るもの

```
kicad-local help        # 入口。ここから全部呼べる
kicad-local patches     # 取り込んだ変更の一覧(出どころ・確認結果)
kicad-local test        # 回帰試験と道具の動作確認(数十秒)
kicad-local route --help  # 画面なしの配線・部品移動の命令一覧
```

## フォルダ

| 場所 | 中身 |
|---|---|
| `~/src/kicad-8.0.9/` | ソース（GitHub 公式ミラーの 8.0.9 タグ＋ローカル修正）。**動かさない**（ビルド設定が絶対パス） |
| `$KICAD_LOCAL_BUILD/` | ビルド結果。`kicad-local rebuild` で差分ビルド |
| `patches/` | 番号付きパッチ 00〜08、`README.md`（来歴）、`bin/`（ラッパーの控え）、`notes/`（ビルド依存パッケージ一覧など） |
| `test/` | 回帰試験（`regress.sh`、`baseline/`、`steps/`、`tests/`） |
| `tools/` | Python の道具: `review.py`（`kicad-local review`、まとめて確認）、`klint.py`（`lint`、回路図の点検）、`ksch.py`（`sch`、回路図の編集）、`ksim/`（`sim`、回路図から自動で組むシミュレーション。`ksim/README.md`。定格・モデルは `ksim/models/`、基板の寄生 L は `ksim/pcbpar.py`、例題は `ksim/examples/`）、部品: `sx.py`（S 式の読み書き）、`geo.py`（端子の座標）、`ngs.py`（libngspice） |
| `~/src/kicad-master/` | KiCad 開発版（10.99）のソース。2023-12 以降の履歴つき。取り込みの参考用（3GB。容量が要るなら消してよい。再取得は GitHub ミラーから） |
| `~/.local/bin/` | `kicad-local`、`kicad-route`、`kicad-gerber`、`kicad-cli-local`、`kicad-python` |

## 基板を改良するときの流れ（例）

```
# 0. 新しい版が来たら、まずまとめて確認(ERC・DRC・3D・前の版との差分・シミュレーション → 報告.md 1 枚)
kicad-local review 新しい版フォルダ 前の版フォルダ --sim 手順.toml ... --out 確認報告/

# 1. 現状を知る
kicad-local drc 基板.kicad_pcb            # 違反・未配線・回路図との不一致の要点
kicad-local erc 回路図.kicad_sch
kicad-local lint 回路図.kicad_sch           # 定石からの外れ(外部の線の保護・ベース抵抗・向き・電源のコンデンサ)
kicad-local render 基板.kicad_pcb iso       # 3D で見る
kicad-local stats 基板.kicad_pcb            # 部品数・穴径など

# 2. 部品を動かし、配線する(手順は標準入力でも可)
kicad-local route 基板.kicad_pcb 出力.kicad_pcb - --instructions 手順.md <<'EOF'
info R10
findspace R10 0.25                  # 置ける場所の候補(place 命令の形で出る)
unroute R10.2
drag R10 1,0                        # シルクの文字も自動で逃がす(周りの文字も押し出す)
autoroute R10.2 C5.1                # ビア1個までの経路を自動で探す
optimize R10.2
check                               # その場で DRC
EOF

# 2'. 回路図を直したら基板へ反映(部品の追加・ネット・フットプリントの差し替え)→ 置いて配線
kicad-local pcbsync 回路図.kicad_sch 基板.kicad_pcb 同期後.kicad_pcb --swap
kicad-local silkfix 入力.kicad_pcb 出力.kicad_pcb R12 D6     # 文字の重なりが残ったら

# 3. 前の版と比べる
kicad-local fabdiff 前.kicad_pcb 出力.kicad_pcb 比較/   # 製造データを層ごとに(差分画像+場所の一覧)
kicad-local renderdiff 前.kicad_pcb 出力.kicad_pcb      # 3D 画像の差分

# 4. 回路を確かめる(回路図から自動で SPICE 化。手順は TOML)
kicad-local sim nets 回路図.kicad_sch                  # ネット名・端子の一覧
kicad-local sim run 手順.toml --out 結果/               # 波形・測定値・ばらつき・版の比較・部品の定格チェック → 結果/結果.md
```

- `--instructions` の手順書は、端子名・折れ点の座標・ビアの位置を引いた順に書くので、
  KiCad の画面で人が同じ操作を再現できる。
- 回路図は `kicad-local sch 入力 出力 手順`（値・欄・記号の差し替え・削除・直列挿入・部品追加・ラベル・注記・表題欄）。
  保存後に「接続の差」「部品表の差」「ERC」が出るので、意図どおりかその場で分かる。`--instructions` で画面用の手順書。
  基板への反映は KiCad の画面で「回路図から基板を更新」(F8)。
  （細かい図の手直しは `kicad-local-tools/sx.py` と `geo.py` でスクリプトも書ける）
- シミュレーションのモデルはデータシートに当てはめた自作(`ksim/models/base.lib`、根拠つき)。
  メーカー配布のモデルがあれば `ksim/models/vendor/` に置いて差し替える。手順ファイルの書き方は `ksim/README.md`。
- 部品の定格は `ksim/models/ratings.toml`（出典つき）。新しい部品は、データシートで確かめてから足す。
- `[corners]` で条件の総当たり(最悪の条件つき)、`[parasitics]` で基板の配線の L・R を入れた計算(`ksim/pcbpar.py`)。
  モデルが無くて外した部品は実行時に「！」で出るので必ず見る(TVS が外れたまま計算して誤った例あり)。

## 取り込んだもの（詳細は kicad-local patches）

| # | 内容 | 出どころ |
|---|---|---|
| 00 | kicad-route（画面なし配線）＋ SWIG の thisown 参照数バグ修正 | 独自 |
| 01 | ルーターの不具合修正 19 件 | 9〜11 |
| 02 | `pcb drc --refill-zones --save-board` | 10 |
| 03 | DRC の JSON に位置と層 | MR !2780 |
| 04 | 部品移動時に線を迂回させて引き直す | MR !2188 |
| 05 | `pcb export stats` | 10 |
| 06 | `pcb render`（画面なしレイトレース） | 9 ＋ master a9ce9da55 |
| 07 | kicad-route の使い勝手（help/info/check/silk/指示書/シルク自動退避） | 独自 |
| 08 | `kicad-gerber`（info/diff/dirdiff）、fabdiff、renderdiff | 11 の gerber_diff を移植 |

見送り: pcb render の背景透過（RGBA 化の前提改修が要る）、MR !2788・!1687・!2326、ファイル形式が変わる 9 以降の機能全般。

## 最新版から機能を取り込むときのやり方

1. 取り込む前に `cd ~/src/kicad-8.0.9 && git add -N . && git diff > /tmp/snapshot.patch`（戻せるように）。
2. コミットを探す: `cd ~/src/kicad-master && git log --oneline -- <パス>`。MR は GitLab
   （`curl https://gitlab.com/kicad/code/kicad/-/merge_requests/<番号>.diff`）。
3. `git format-patch -1 --stdout <hash> | (cd ~/src/kicad-8.0.9 && git apply --check)` で当たるか見る。
   ルーターのように件数が多いときは patch-id で 8.0 系列と突き合わせる（`git log --cherry-pick` は遅すぎる）。
4. 9 以降の名前の読み替えがよく要る: `PNS_LAYER_RANGE`→`LAYER_RANGE`、C++20 の `contains()`→`count()`、
   `BooleanSubtract(x)`→`BooleanSubtract(x, SHAPE_POLY_SET::PM_FAST)`、`json_common.h`→`nlohmann/json.hpp`、
   `ARC_LOW_DEF_MM`→0.02、`VIATYPE::BLIND/BURIED`→`BLIND_BURIED`。
5. **ファイル形式を変える機能は入れない**（保存したファイルが KiCad 8 で開けなくなる）。
6. `kicad-local rebuild` → `kicad-local test` → システム版 kicad-cli と DRC/ERC を突き合わせ。
7. `patches/` に番号付きパッチと README の行を足す。

## ハマりどころ

- ビルドはメモリ 7GB なので `-j5` まで。フルビルドは約 22 分、差分なら数分。
- C: ドライブの実空きは約 20GB（WSL の df の 800GB 超は見かけ）。大きいものを入れる前に `df -h /mnt/c`。
- 開発版 KiCad（nightly）は apt では入らない（8 の PPA 版 OCCT とぶつかる）。10 以降の Docker 版は削除済み。
- 10 以降で保存したファイルは 8 で開けない（確認済み）。
- `kicad-cli-local` は `KICAD_RUN_FROM_BUILD_DIR` と `KICAD8_*_DIR` が要る（ラッパーで設定済み）。
  ERC には cvpcb_kiface、3D モデル表示には s3d_plugin_vrml/oce/idf のビルドが要る。
- kicad-route 内の DRC(check) はライブラリ表を読まないので、ライブラリ照合は `kicad-local drc` で。
- EXCELLON_IMAGE::LoadFile には EXCELLON_DEFAULTS を渡す（nullptr だと落ちる）。
- EDA_TEXT::GetTextBox() は回転を考慮しない。文字の外形は GetBoundingBox()。
- 自作コマンドは終了時の後片付けで落ちるので、保存・表示のあと `std::_Exit` で抜けている。
- `pkill -f <パターン>` は自分のシェルも止めるので使わない。
- ksim: モデルの無い部品は外れる(実行時の「！」を見る)。版によってネット名が変わる(/+3V3 と Net-(A1-3V3) など)ので、
  手順ファイルでは `{net:A1.17}` のように端子で指す方が版をまたいで使える。
- メーカーのサイトには curl を 403 で拒否するものがある(ボット対策)。
  データシートは Playwright のブラウザで開き、ページ内の fetch で取り出す。
