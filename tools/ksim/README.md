# ksim: KiCad の回路図から ngspice シミュレーション

```
kicad-local sim run  <手順.toml> [--out 出力フォルダ] [--runs N]
kicad-local sim nets <回路図.kicad_sch>      # ネット名と、つながる端子(役割つき)
kicad-local sim parts --sch <回路図>          # 部品がどう SPICE 化されるか / されないか
```

- 回路図 → `kicad-cli sch export netlist`(端子の役割つき)→ 部品ごとに SPICE の素子を自動で作る。
  手で回路を書き写さないので、回路図を直せばシミュレーションも追従する。
- ngspice は KiCad に同梱の libngspice(43)を Python の ctypes で呼ぶ(`ngs.py`)。ngspice のコマンドは不要。
- 出力: `結果.md`(測定値の表・判定・ばらつき・版の比較・外した部品)、`plot*.png`(波形。灰色はばらつきの各回)、
  `compare*.png`(版の比較)、`circuit*.cir`(実際に流した回路)、`測定値.csv`

## 部品の変換

| 回路図の部品 | SPICE |
|---|---|
| Device:R / C / C_Polarized / L | 値の文字列から(`4.7kΩ` `22µF/50V` `33µH SRC1317` `4k7`) |
| Device:D* | `D 名 A K モデル`(端子の役割 A/K) |
| Q_NPN* / Q_PNP* | `Q 名 C B E モデル` |
| Q_PMOS* / Q_NMOS* | `M 名 D G S モデル`(VDMOS) |
| Device:Fuse | 対応表の `"R:値±幅%"` で抵抗近似 |
| SW_* | 開(1TΩ)。`[parts.SW1] state = "closed"` で閉(0.05Ω) |
| 対応表の `"X:名前(IN GND OUT)"` | サブ回路(端子は役割の順) |
| コネクタ・マイコン・テストピン・取付穴 | 外す(ネットは残る → 信号源をつなぐ) |

モデルの対応表(値 → モデル)は `models/map.toml`。手順ファイルの `[models] map` で上書きできる。
モデルは `models/base.lib`。**メーカー配布のモデルではなく、データシートの代表値に当てはめたもの**(各モデルの
前に根拠を書いてある)。メーカー版を入手したら `models/vendor/` に置き、`[models] include` と `map` で差し替える。

## 手順ファイル(TOML)

```toml
title = "題名"
schematic = "回路図.kicad_sch"          # 手順ファイルからの相対パスも可
note = """結果.md の冒頭に載せる説明(前提・仮定)"""

[analysis]
tran = "10u 10m"                        # または dc = "VBAT 0 30 0.1" / 無ければ op
options = "method=gear"                 # 必要なら

[[sources]]                             # 電源・信号源(plus/minus は ネット名 / 部品.端子 / GND / 補助の点)
name = "BAT"
plus = "J1.1"
minus = "GND"
value = "DC {p:vbat}"                   # {p:名前} は [params] の値

[params]                                # 基板の外の値。モンテカルロで振る
vbat = { nominal = 24, tol = 0.1 }      # 相対 ±10%
shift = { nominal = 0, abs = 0.15 }     # 絶対 ±0.15

[extra]                                 # 追加の SPICE 行。{net:名前} でノード、{p:名前} で値
spice = """
RGPIO gpio {net:A1.9} 40
"""

[parts]                                 # 部品ごとの上書き
SW1 = { state = "closed" }
R28 = { value = "1k" }
D7 = { spice = """LD7 {n:1} d7k 10n
DD7 {n:2} d7k 1N5231B""" }             # {n:端子番号} でその端子のノード

[[measure]]                             # kind: at / max / min / avg / final / pp / integral / integral_pos / integral_abs / absmax
name = "出力の電流"
signal = "i(VSENSE)"                    # v(ネット) v(A,B) v(部品.端子) i(電源名) i(部品番号) @素子[量]
kind = "at"
at = 5e-3
unit = "A"
min = 4.5e-3                            # 判定の基準(ばらつき込みの最小・最大で判定)
max = 7e-3

[[plot]]
title = "波形"
signals = ["i(VSENSE)"]
scale = 1000
ylabel = "電流 [mA]"

[montecarlo]
runs = 100                              # 回路図の Tolerance 欄(無い抵抗 ±5%、C・L ±20%)と [params] を一様分布で振る

[[variants]]                            # 版の比較(parts / params / sources の value を上書き)
name = "1kΩ"
desc = "変更案"
parts = { R28 = { value = "1k" } }
```

## 部品の定格チェック

`run` のたびに、シミュレーションに入った部品ごとに使用値(公称とばらつきの最悪値)を定格と比べ、
`結果.md` の「部品の定格チェック」と `定格チェック.csv` に出す(`[stress] enable = false` で止める)。

| 部品 | 見る項目 |
|---|---|
| 抵抗 | 損失(平均)・電圧(尖頭) |
| コンデンサ | 電圧(尖頭)、電解は逆電圧 |
| ダイオード・TVS | 損失(平均)。瞬間の現象ではパルスのエネルギー |
| トランジスタ | Vce・Veb(逆)・Ic・Ib・損失 |
| MOSFET | Vds・Vgs・Id・損失 |
| ヒューズ・PTC | 電流(実効値) |

- 定格の出どころ: `models/ratings.toml`(値・MPN で引く。出典つき)→ MPN や値の「1/4W」「/50V」→ 回路図の Power・Voltage 欄。
  分からない部品は「判定できなかったもの」に並ぶので、ratings.toml に足す。
- 余裕の目安(ratings.toml の `[derating]`、手順ファイルの `[stress.derating]` で変更可): 損失 50%・電圧 80%・電流 80%・パルス 80%
  以下で OK、100% までは注意、超えたら NG(NG は全体の判定も NG)。部品ごとに `derate = 0.75` も書ける。
- `[stress] kind = "pulse"`: 静電気・サージなど瞬間の現象用。電圧と、TVS のパルスのエネルギー(10/1000µs の尖頭電力×1.44ms を定格とみなす)で判定。
- `[stress] window = [t0, t1]`: 平均を取る時間の範囲。
- 判定できるのは、その手順で実際に動かした部分だけ。全部の部品を見るなら、全部 ON の最悪条件の手順を作る
  (全出力を ON にし、電源を最悪条件にした手順ファイル)。

## 条件の総当たり `[corners]`

```toml
[params]
vbat = 24
d6 = 3.3
[corners]
vbat = [19.2, 24, 28.8]
d6 = [0, 3.3]                   # 信号源の値を {p:d6} にしておく
"SW1.state" = ["open", "closed"]  # 部品.欄 は部品の上書き
```
全組み合わせ(4096 まで)を回し、測定値ごとに最小・最大とその条件、定格チェックは「最悪の条件」つきで出す。
1536 通りで約 3 分(10ms の過渡解析)。結果は `条件ごとの測定値.csv` にも。

## 基板の寄生インダクタンス `[parasitics]`

```toml
[parasitics]
pcb = "auto"                    # 回路図の隣の .kicad_pcb
hubs = ["SW2.2", "J1.2"]        # この端子のネット全体を、この端子を起点に端子ごとに分ける
override = { "D7.1" = 1.0 }     # (任意)配置を変えた場合の L [nH]。[[variants]] の parasitics でも可
```
基板の配線・ビア・ベタから、起点 → 各端子の L と R を概算して、端子ごとに別ノードにして L・R でつなぐ(`pcbpar.py`)。
単体でも: `kicad-python pcbpar.py 基板 ネット 起点`。配線と GND の間の静電容量は入れていない(尖頭電圧は高め=上限側)。

## ほかの仕組み

- 電解コンデンサの ESR: ratings.toml の `[capacitor."MPN の一部"] tand = ` から(120Hz の最大値)。
  `[params] esr_scale` で倍率(0 で理想)。速い現象では `[corners] esr_scale = [0, 1]` で幅を見る。
- 測定の種類 `i2t`(電流の 2 乗の積分。ヒューズの目安)、`cross`(at の値を初めて超えた時刻。立ち上がり)。
- `[stress] window` は損失・実効値を取る区間。尖頭(電圧・電流)は全区間で見る。
- 名前の無い GND(Net-(D1-A) など)は、GND 機能の電源端子を含むネットを自動で GND にする。
- モデルが無くて外した部品は、実行時に「！」で表示する(結果に影響しうるので必ず見る)。

## 例題

- 例題の手順ファイルは同梱していない。上の書式で、自分の回路図に合わせて作る。

## 注意

- DCDC は振る舞いモデル(入力が足りる間は一定電圧・効率一定)。起動・過電流保護・リップルは含まない。
- PTC・ヒューズはトリップ・溶断を含まない(抵抗だけ)。
- `.options rshunt=1e12` を自動で付ける(マイコン端子だけにつながる浮いたノード対策)。
