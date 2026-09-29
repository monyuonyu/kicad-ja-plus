#!/bin/bash
# kicad-route の回帰試験: 同じ手順を流し、結線・DRC・配線の量を基準(baseline/)と比べる
# 試験用の基板は KiCad 8.0.9 付属のデモ demos/pic_programmer（in.kicad_pcb）。
# 回路図の試験は同じく demos/interf_u（sch/）。
# このリポジトリの場所(道具と試験を探す)と、修正版 KiCad をビルドした場所
KICAD_LOCAL_HOME=${KICAD_LOCAL_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}
KICAD_LOCAL_BUILD=${KICAD_LOCAL_BUILD:-$HOME/src/build-10.0.6}
export KICAD_LOCAL_HOME KICAD_LOCAL_BUILD
export PATH="$KICAD_LOCAL_HOME/bin:$PATH"   # ほかの入口(kicad-python など)を名前で呼ぶ
cd "$(dirname "$0")"
K=$KICAD_LOCAL_BUILD/qa/tools/pns/kicad_route
mkdir -p now
run() { # 入力 出力 手順
  $K "$1" "now/$2" "$3" > "now/$2.log" 2>&1
  cp in.kicad_pro "now/${2%.kicad_pcb}.kicad_pro"
  kicad-cli pcb drc --schematic-parity -o "now/$2.rpt" "now/$2" >/dev/null 2>&1
}
kicad-cli pcb drc --schematic-parity -o now/in.rpt in.kicad_pcb >/dev/null 2>&1   # 元の基板にもともとある違反
run in.kicad_pcb out_v2.kicad_pcb steps/steps_v2.txt
run in.kicad_pcb out_guided.kicad_pcb steps/steps_guided.txt
run now/out_v2.kicad_pcb out_v3.kicad_pcb steps/steps_v3.txt
run now/out_guided.kicad_pcb out_drag.kicad_pcb steps/steps_drag.txt
run now/out_guided.kicad_pcb out_findspace.kicad_pcb steps/steps_findspace.txt
python3 - <<'PY'
import re,os
def summary(pcb):
    s=open(pcb).read()
    segs=re.findall(r'\(segment\s+\(start ([-\d.]+) ([-\d.]+)\)\s+\(end ([-\d.]+) ([-\d.]+)\)',s)
    L=sum(((float(a)-float(c))**2+(float(b)-float(d))**2)**.5 for a,b,c,d in segs)
    return len(segs), s.count("(via"), round(L,1)
def cats(rpt): return set(re.findall(r'^\[(\w+)\]',open(rpt).read(),re.M))
def unconn(rpt):
    m=re.search(r'Found (\d+) unconnected',open(rpt).read()); return m.group(1) if m else "?"
pre=cats("now/in.rpt")          # 元からある種類(ライブラリ差分・ベタの細い接続など)は数えない
ok=True
for f in ("out_v2","out_guided","out_v3","out_drag","out_findspace"):
    b=summary(f"baseline/{f}.kicad_pcb") if os.path.exists(f"baseline/{f}.kicad_pcb") else None
    n=summary(f"now/{f}.kicad_pcb"); rpt=f"now/{f}.kicad_pcb.rpt"
    bad=sorted(c for c in cats(rpt)-pre if c!="unconnected_items")
    un=unconn(rpt)
    log=open(f"now/{f}.kicad_pcb.log").read(); res=re.search(r"結果: .*",log)
    print(f"{f:13s} 線分/ビア/長さ 基準{b} → 今{n}  未配線{un}  DRC違反{bad or 'なし'}")
    print(f"              {res.group(0) if res else 'ログなし'}")
    if bad or un not in ("0",): ok=False
print("判定:", "OK(DRC違反なし・未配線なし)" if ok else "NG")
raise SystemExit(0 if ok else 1)
PY
route_rc=$?

# ---- 道具の動作確認(スモークテスト) ----
echo "--- 道具の動作確認"
fails=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "  OK  $1"; else echo "  NG  $1"; fails=$((fails+1)); fi; }
chk "SWIG 修正(部品・配線の Remove 後も一覧が使える)" "kicad-python tests/swigfix_test.py | grep -q 保存OK"
chk "kicad-cli-local pcb drc(JSON に位置)" "kicad-cli-local pcb drc --format json -o now/t.json in.kicad_pcb && grep -q '\"pos\"' now/t.json"
chk "kicad-cli-local pcb export stats" "kicad-cli-local pcb export stats -o now/s.txt in.kicad_pcb && grep -q 'Through hole: 238' now/s.txt"
chk "kicad-cli-local pcb render" "kicad-cli-local pcb render --rotate -45,0,45 -w 400 -h 300 -o now/r.png in.kicad_pcb && test -s now/r.png"
chk "kicad-gerber diff(同じファイルは差なし)" "mkdir -p now/g && kicad-cli pcb export gerbers --layers F.Cu -o now/g/ in.kicad_pcb && kicad-gerber diff now/g/in-top_layer.gtl now/g/in-top_layer.gtl"
chk "kicad-local review(基板だけでも ERC/DRC/3D のまとめが出る)" "kicad-local review in.kicad_pcb --out now/review >/dev/null; grep -q '| DRC |' now/review/報告.md"
# ERC は元の回路図と同じ件数なら OK（KiCad 10 はデモの回路図にもともと指摘を出すため、0 件は求めない）
sch_erc_before=$(python3 -c "import sys,pathlib,tempfile; sys.path.insert(0,'$KICAD_LOCAL_HOME/tools'); import review; print(review.erc(pathlib.Path('sch/interf_u.kicad_sch'), pathlib.Path(tempfile.mkdtemp()))[0])" 2>/dev/null)
chk "kicad-local sch(直列挿入で接続が 1 本増え、ERC は増えない)" "printf 'insert R99 1k Device:R at R4.1\n' | kicad-local sch sch/interf_u.kicad_sch now/sch_out.kicad_sch - > now/sch.log; grep -q '新しいネット \*\*Net-(D1-A)\*\*: D1.2 R99.2' now/sch.log && grep -qF -- \"--- ERC: $sch_erc_before\" now/sch.log"
chk "基板の寄生インダクタンス(R10.2→C5.1 が 3.32nH)" "kicad-python $KICAD_LOCAL_HOME/tools/ksim/pcbpar.py in.kicad_pcb 'Net-(C5-Pad1)' R10.2 | grep -q 'C5.1 *L   3.32'"
chk "kicad-local lint(デモ回路図は要確認 0 件)" "kicad-local lint sch/interf_u.kicad_sch | grep -q '要確認'"

# 終了コード: 配線の比較か道具の確認に NG が 1 つでもあれば 1（自動の試験が失敗を見逃さないように）
if [ "$route_rc" -ne 0 ] || [ "$fails" -ne 0 ]; then
  echo "NG あり（配線の比較: $([ "$route_rc" -eq 0 ] && echo OK || echo NG)、道具の確認の NG: $fails 件）"
  exit 1
fi
echo "すべて OK"
