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
# 部品ライブラリの一覧（グローバルの sym-lib-table・fp-lib-table）が要る。無いと差し込んだ部品の分だけ
# 「フットプリントが見つからない」が増える。
# 比べるのは要確認の件数だけ（「参考」のライブラリの指摘は、グローバルのライブラリ表の有無で変わる）
sch_erc_before=$(python3 -c "import sys,pathlib,tempfile; sys.path.insert(0,'$KICAD_LOCAL_HOME/tools'); import review; print(review.erc(pathlib.Path('sch/interf_u.kicad_sch'), pathlib.Path(tempfile.mkdtemp()))[0].split('、')[0])" 2>/dev/null)
[ -n "$sch_erc_before" ] || sch_erc_before="(元の回路図の ERC を取れない)"
chk "kicad-local sch(直列挿入で接続が 1 本増え、ERC は増えない)" "printf 'insert R99 1k Device:R at R4.1\n' | kicad-local sch sch/interf_u.kicad_sch now/sch_out.kicad_sch - > now/sch.log; grep -q '新しいネット \*\*Net-(D1-A)\*\*: D1.2 R99.2' now/sch.log && grep -qF -- \"--- ERC: $sch_erc_before\" now/sch.log"
chk "基板の寄生インダクタンス(R10.2→C5.1 が 3.32nH)" "kicad-python $KICAD_LOCAL_HOME/tools/ksim/pcbpar.py in.kicad_pcb 'Net-(C5-Pad1)' R10.2 | grep -q 'C5.1 *L   3.32'"
# 厳しい DRC: R10.1 のネットを外した基板で、ネットの無いパッドと、デモにもともとある細い配線(VCC_PIC 0.35mm)を見つけ、終了コード 1
python3 - <<'PY'
import sys; sys.path.insert(0, "../tools"); import sx
b = sx.parse(open("in.kicad_pcb").read())
for fp in sx.find(b, "footprint"):
    if str(sx.prop(fp, "Reference")[2]) == "R10":
        for p in sx.find(fp, "pad"):
            if str(p[1]) == "1":
                p[:] = [e for e in p if not (isinstance(e, list) and e and e[0] == "net")]
open("now/netless.kicad_pcb", "w").write(sx.dump(b) + "\n")
PY
cp in.kicad_pro now/netless.kicad_pro
chk "kicad-local drc --strict(ネットの無いパッドと、ネットクラスより細い配線を見つける)" "kicad-local drc now/netless.kicad_pcb --strict --json > now/strict.json; test \$? -eq 1 && python3 -c \"import json; d=json.load(open('now/strict.json')); k={(s['kind'], s.get('ref',''), s.get('pad',''), s.get('net','')) for s in d['strict']}; assert ('netless_pad','R10','1','') in k; assert any(x[0]=='narrow_track' and x[3]=='/pic_sockets/VCC_PIC' for x in k)\""
# 引けない理由: 配線禁止の区域で塞いだ組を「すべての層をキープアウトが塞いでいる」と見分ける
kicad-python tests/make_keepout.py in.kicad_pcb now/keepout.kicad_pcb >/dev/null 2>&1; cp in.kicad_pro now/keepout.kicad_pro
chk "kicad-local why(キープアウトが塞いでいると見分ける)" "kicad-local why now/keepout.kicad_pcb --json > now/why.json; test \$? -eq 1 && python3 -c \"import json; d=json.load(open('now/why.json')); r=d['diagnosed'][0]; assert r['net']=='Net-(C5-Pad1)' and r['main_cause']==['keepout'], r\""
chk "kicad-local view(注釈つきの画像。未配線とキープアウトの基板で印が付く)" "kicad-local view now/keepout.kicad_pcb -o now/view.png --drc --ref --net 'Net-(C5-Pad1)' --json > now/view.json && test -s now/view.png && python3 -c \"import json; d=json.load(open('now/view.json')); assert any(m['type']=='unconnected_items' for m in d['marks'])\""
chk "kicad-local erc --json(要確認があれば終了コード 1)" "kicad-local erc sch/interf_u.kicad_sch --json > now/erc.json; rc=\$?; python3 -c \"import json,sys; d=json.load(open('now/erc.json')); sys.exit(0 if (rc:=int('\$rc'))==(0 if d['ok'] else 1) else 1)\""
# 全体の自動配線（Freerouting が要る。無ければ飛ばす）: 配線を 1 本消した基板を引き直して未配線 0
fr_jar=""
for c in "${FREEROUTING_JAR:-}" ~/.local/share/kicad-ja-local/freerouting.jar ~/.local/share/freerouting/freerouting.jar ~/cad-mcp-lab/tools/freerouting/freerouting-2.4.1.jar; do
  [ -n "$c" ] && [ -f "$c" ] && { fr_jar=$c; break; }
done
if [ -n "$fr_jar" ]; then
  kicad-python -c "import pcbnew,sys; b=pcbnew.LoadBoard('now/keepout.kicad_pcb'); [b.Remove(z) for z in list(b.Zones()) if z.GetIsRuleArea()]; b.Save('now/unrouted.kicad_pcb')" >/dev/null 2>&1
  cp in.kicad_pro now/unrouted.kicad_pro
  chk "kicad-local autoroute(未配線 1 → 0、新しい違反なし)" "kicad-local autoroute now/unrouted.kicad_pcb now/autorouted.kicad_pcb --jar $fr_jar --passes 5 --timeout 300 --json > now/autoroute.json && python3 -c \"import json; d=json.load(open('now/autoroute.json')); assert d['unconnected_before']==1 and d['unconnected_after']==0 and not d['new_violations'], d\""
else
  echo "  --  kicad-local autoroute: Freerouting が無いので飛ばした（FREEROUTING_JAR で指定できる）"
fi
# 仕上げ: ベタと外形を消した基板に、外形 → GND のベタ → スティッチングビア。未配線 0、クリアランス・穴の違反なし
kicad-python -c "import pcbnew; b=pcbnew.LoadBoard('in.kicad_pcb'); [b.Remove(z) for z in list(b.Zones())]; [b.Remove(d) for d in list(b.GetDrawings()) if d.GetLayer()==pcbnew.Edge_Cuts]; b.Save('now/bare.kicad_pcb')" >/dev/null 2>&1
cp in.kicad_pro now/bare.kicad_pro
chk "kicad-local finish(外形 → ベタ → スティッチングビア。未配線 0、違反なし)" "kicad-local finish outline now/bare.kicad_pcb now/fin1.kicad_pcb --round 2 >/dev/null && kicad-local finish zone now/fin1.kicad_pcb now/fin2.kicad_pcb --net GND >/dev/null && kicad-local finish stitch now/fin2.kicad_pcb now/fin3.kicad_pcb --net GND --pitch 6 --json > now/stitch.json; kicad-local drc now/fin3.kicad_pcb --json > now/fin3.json; python3 -c \"import json; s=json.load(open('now/stitch.json')); d=json.load(open('now/fin3.json')); bad={v['type'] for v in d['violations']} & {'clearance','hole_to_hole','hole_clearance','via_diameter','drill_out_of_range','shorting_items'}; assert s['placed']>100 and not d['unconnected_items'] and not bad, (s, bad)\""
chk "kicad-local lint(デモ回路図は要確認 0 件)" "kicad-local lint sch/interf_u.kicad_sch | grep -q '要確認'"

# 終了コード: 配線の比較か道具の確認に NG が 1 つでもあれば 1（自動の試験が失敗を見逃さないように）
if [ "$route_rc" -ne 0 ] || [ "$fails" -ne 0 ]; then
  echo "NG あり（配線の比較: $([ "$route_rc" -eq 0 ] && echo OK || echo NG)、道具の確認の NG: $fails 件）"
  exit 1
fi
echo "すべて OK"
