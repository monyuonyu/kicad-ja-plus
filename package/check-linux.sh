#!/bin/bash
# Linux 版の配布物を展開した場所で、入れ方が正しいか・中身が揃っているかを確かめる
#
# 使い方: package/check-linux.sh [展開した場所=このスクリプトの 1 つ上] [画像の出力先]
#   画面（pcbnew・eeschema・kicad）の起動は、xvfb-run があれば仮想の画面で確かめる（無ければ飛ばす）。
#   ImageMagick（import）があれば、起動した画面の画像を出力先に残す。
# 回帰試験（kicad-local test）は別。こちらは数十秒で終わる確かめだけ。
set -uo pipefail
pkg=$(readlink -f "${1:-$(dirname "$(readlink -f "$0")")/..}")
shots=${2:-}
b=$pkg/build
export KICAD_LOCAL_HOME=$pkg KICAD_LOCAL_BUILD=$b
export PATH="$pkg/bin:$PATH"
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
fails=0
ok() { echo "  OK  $1"; }
ng() { echo "  NG  $1"; fails=$((fails + 1)); }
chk() { if eval "$2" > "$t/out" 2>&1; then ok "$1"; else ng "$1"; sed 's/^/        /' "$t/out" | tail -5; fi; }

echo "--- 中身（$pkg）"
for f in kicad/kicad-cli pcbnew/pcbnew eeschema/eeschema kicad/kicad pcbnew/_pcbnew.kiface eeschema/_eeschema.kiface \
         pcbnew/_pcbnew.so pcbnew/pcbnew.py qa/tools/pns/kicad_route gerbview/kicad_gerber \
         resources/images.tar.gz translation/ja/kicad.mo; do
  chk "$f" "test -s '$b/$f'"
done
has_ai=0
[ -f "$b/scripting/kicad_ai/bridge.py" ] && has_ai=1
if [ $has_ai = 1 ]; then
  chk "AI チャットの仲介役と AI 用の Python" "test -s '$b/scripting/kicad_ai/bridge.py' && test -x '$b/ai-runtime/bin/python3'"
fi

has_fr=0
if [ -d "$b/freerouting" ]; then
  has_fr=1
  chk "Freerouting（jar・Java・使用許諾）" "test -s '$b/freerouting/freerouting.jar' && test -x '$b/freerouting/jre/bin/java' && test -s '$b/freerouting/LICENSE-freerouting.txt' && test -s '$b/freerouting/jre/legal/java.base/LICENSE'"
fi

echo "--- 共通のライブラリ（システムに無いものが無いこと）"
: > "$t/missing"
while read -r f; do
  [ "$(head -c4 "$f")" = $'\x7fELF' ] || continue
  env -u LD_LIBRARY_PATH ldd "$f" 2>/dev/null | grep "not found" | sed "s|^|${f#"$pkg"/}: |" >> "$t/missing"
done < <(find "$b" \( -path "$b/ai-runtime" -o -path "$b/freerouting" \) -prune -o -type f \( -perm -u+x -o -name '*.so*' -o -name '*.kiface' \) -print)
if [ -s "$t/missing" ]; then
  ng "見つからないライブラリがある（DEPENDS-apt.txt のパッケージを apt で入れる）"
  sort -u "$t/missing" | head -20 | sed 's/^/        /'
else
  ok "すべて見つかる"
fi

echo "--- 動作"
chk "kicad-cli version" "kicad-cli-local version"
echo "        $(kicad-cli-local version 2>/dev/null)"
chk "kicad-python で import pcbnew（同梱のモジュール）" \
  "kicad-python -c 'import pcbnew,sys; print(pcbnew.Version()); sys.exit(0 if pcbnew.__file__.startswith(\"$b/\") else 1)'"
echo "        pcbnew.Version() = $(kicad-python -c 'import pcbnew; print(pcbnew.Version())' 2>/dev/null)"
chk "kicad-route --help" "kicad-route --help 2>&1 | grep -qi usage || kicad-route --help"
if [ $has_ai = 1 ]; then
  # 仲介役が同梱の Python で起動し、キーが無ければ need_key を返すこと（AI には接続しない）
  chk "AI チャットの仲介役（need_key を返す）" \
    "echo | env -u ANTHROPIC_API_KEY -u CLAUDE_CODE_OAUTH_TOKEN -u JA_CLAUDE_OAUTH_TOKEN PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring \
       '$b/ai-runtime/bin/python3' '$b/scripting/kicad_ai/bridge.py' --datadir '$t/ai' | grep -q '\"need_key\"'"
  chk "SDK に同梱の claude" "'$b'/ai-runtime/lib/python3.*/site-packages/claude_agent_sdk/_bundled/claude --version"
fi

if [ $has_fr = 1 ]; then
  # 同梱の jar と Java を自分で見つけて動くこと（システムの java や指定の環境変数は使わない）
  mkdir -p "$t/ar" && cp "$pkg/test/in.kicad_pcb" "$pkg/test/in.kicad_pro" "$t/ar/"
  (cd "$t/ar" && kicad-python -c "import pcbnew; b=pcbnew.LoadBoard('in.kicad_pcb'); t=[x for x in b.GetTracks() if not isinstance(x, pcbnew.PCB_VIA)]; b.Remove(t[len(t)//2]); b.Save('un.kicad_pcb')" && cp in.kicad_pro un.kicad_pro) > /dev/null 2>&1
  chk "kicad-local autoroute（同梱の Freerouting と Java で、未配線 0）" \
    "cd '$t/ar' && env -u FREEROUTING_JAR -u FREEROUTING_JAVA -u JAVA_HOME kicad-local autoroute un.kicad_pcb out.kicad_pcb --passes 5 --timeout 300 --json > ar.json; \
     python3 -c \"import json,sys; d=json.load(open('ar.json')); print(d['java']); sys.exit(0 if d['ok'] and d['unconnected_after']==0 and d['java'].startswith('$b/freerouting/') and d['jar'].startswith('$b/freerouting/') else 1)\""
fi

if command -v xvfb-run > /dev/null; then
  echo "--- 画面（仮想の画面で 35 秒動かし、落ちないこと）"
  for app in pcbnew-ja eeschema-ja kicad-ja; do
    shot=""
    [ -n "$shots" ] && command -v import > /dev/null && { mkdir -p "$shots"; shot="$(readlink -f "$shots")/$app.png"; }
    # 設定は使い捨ての場所に（初めて起動したときの窓が出るが、動いていれば良しとする）。日本語の環境があれば日本語で
    lang=C.UTF-8; locale -a 2>/dev/null | grep -qi '^ja_JP.utf-\?8$' && lang=ja_JP.UTF-8
    LANG=$lang LC_ALL=$lang HOME=$t/home XDG_CONFIG_HOME=$t/home/.config WXTRACE=KICAD_BITMAPS \
      timeout 60 xvfb-run -a -s "-screen 0 1600x1000x24" bash -c '
        "$1" > "$2" 2>&1 & p=$!
        sleep 35
        [ -n "$3" ] && import -window root "$3"
        kill -0 $p 2>/dev/null || { wait $p; echo "落ちた: 終了コード $?" >> "$2"; exit 1; }
        kill $p; exit 0' _ "$app" "$t/$app.log" "$shot"
    rc=$?
    if [ $rc = 0 ]; then ok "$app が起動して動き続ける${shot:+（画像: $shot）}"; else ng "$app（$rc）"; tail -5 "$t/$app.log" | sed 's/^/        /'; fi
    if grep -q "Loading bitmaps from" "$t/$app.log"; then
      src=$(grep -o "Loading bitmaps from .*" "$t/$app.log" | head -1)
      case "$src" in *"$b/"*) ok "アイコンは同梱のもの（${src#Loading bitmaps from }）" ;; *) ng "アイコンの場所が違う: $src" ;; esac
    fi
  done
else
  echo "--- 画面: xvfb-run が無いので飛ばす"
fi

echo "判定: $([ $fails = 0 ] && echo OK || echo "NG（$fails 件）")"
exit $((fails > 0))
