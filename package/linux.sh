#!/bin/bash
# Linux 版の配布物（tar.gz）を作る: ビルドしたフォルダから、動かすのに要るものだけを同じ配置で抜き出す
#
# 使い方: package/linux.sh [ビルドの場所=~/src/build-10.0.6] [出力先=dist] [版=10.0.6]
#
# 中身: bin/（入口）、tools/、test/、patches/、package/、build/（ビルドした場所と同じ配置）
#   build/ に入れるもの:
#     kicad-cli、kicad_route、kicad_gerber、画面（kicad・pcbnew・eeschema ほか）、kiface、
#     pcbnew の Python モジュール（_pcbnew.so と pcbnew.py）、3D の読み込み、共通のライブラリ、
#     アイコン（resources/images.tar.gz）、翻訳（translation/<言語>/kicad.mo）、
#     AI チャットの仲介役（scripting/kicad_ai）と AI 用の Python（ai-runtime。版を固定した Python と Claude Agent SDK）、
#     Freerouting と、それだけを動かす最小の Java（freerouting/。package/freerouting.sh）
# 共通のライブラリの探し先は $ORIGIN からの相対にする（patchelf）ので、どこに展開しても動く。
# wxWidgets・OpenCASCADE・ngspice などと部品ライブラリは、システムに入れた本家の KiCad（同じ系列の PPA）のものを使う。
# システムから使うライブラリの出どころ（apt のパッケージ名）は DEPENDS-apt.txt に書き出す。
#
# 環境変数:
#   KJP_AI_RUNTIME=skip   AI 用の Python を入れない（既定は、仲介役のある版なら入れる。ダウンロードが要る）
#   KJP_FREEROUTING=skip  Freerouting を入れない（既定は入れる。JDK 25 以降が要る: KJP_JDK か JAVA_HOME）
#   KJP_ALLOW_NO_GUI=1    画面・アイコン・翻訳が無くても止めない（kicad-cli などだけのビルドから作るとき）
#   PATCHELF              patchelf の場所
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
build=$(readlink -f "${1:-$HOME/src/build-10.0.6}")
out=$(readlink -f "${2:-$here/dist}")
ver=${3:-10.0.6}
patchelf=${PATCHELF:-patchelf}
command -v "$patchelf" >/dev/null || { echo "patchelf が無い（apt install patchelf か pip install patchelf）" >&2; exit 2; }

# AI 用の Python（python-build-standalone。どこに置いても動く Python）。版と中身の確かめ（sha256）を固定する
PBS_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-install_only.tar.gz
PBS_SHA256=5d546fc004fc2435fa142eb8f42ed8de5eb4725963dc22f7e6671014a80eb560

name=kicad-ja-plus-$ver-linux-x86_64
dst=$out/$name
rm -rf "$dst"
mkdir -p "$dst/build"

is_elf() { [ "$(head -c4 "$1" 2>/dev/null | tr -d '\0')" = $'\x7fELF' ]; }

# ---- リポジトリの側（試験の出力と Python のキャッシュは除く）
tar -C "$here" --exclude=__pycache__ --exclude=test/now -cf - \
  bin tools test patches package README.md README.ja.md README.zh-CN.md LICENSE | tar -C "$dst" -xf -

# ---- ビルドの側
copy() {  # ビルドの場所からの相対パスを、同じ配置で写す（ライブラリは実体とリンクの両方）
  mkdir -p "$dst/build/$(dirname "$1")"
  cp -a "$build/$1" "$dst/build/$1"
}
missing=()
need() {  # 必ず要るもの
  for f in "$@"; do [ -e "$build/$f" ] || { echo "ビルドに無い: $build/$f" >&2; exit 1; }; copy "$f"; done
}
gui() {  # 画面まわり（無ければ止める。KJP_ALLOW_NO_GUI=1 なら知らせて続ける）
  for f in "$@"; do
    if [ -e "$build/$f" ]; then copy "$f"; else missing+=("$f"); fi
  done
}
opt() {  # あれば入れる
  for f in "$@"; do [ -e "$build/$f" ] && copy "$f"; done; return 0
}

need kicad/kicad-cli qa/tools/pns/kicad_route gerbview/kicad_gerber \
     pcbnew/_pcbnew.kiface pcbnew/_pcbnew.so pcbnew/pcbnew.py \
     eeschema/_eeschema.kiface cvpcb/_cvpcb.kiface \
     plugins/3d/vrml/libs3d_plugin_vrml.so plugins/3d/oce/libs3d_plugin_oce.so plugins/3d/idf/libs3d_plugin_idf.so
gui kicad/kicad pcbnew/pcbnew eeschema/eeschema resources/images.tar.gz translation/ja/kicad.mo
opt gerbview/gerbview pagelayout_editor/pl_editor pcb_calculator/pcb_calculator bitmap2component/bitmap2component \
    schemas scripting/kicad_ai resources/project_template
# kiface はすべて（gerbview・pl_editor・pcb_calculator・bitmap2component など、版で増減する）
while read -r f; do copy "${f#"$build"/}"; done < <(find "$build" -path "$build/qa" -prune -o -name CMakeFiles -prune -o -name '_*.kiface' -print)
# 翻訳（全部の言語。KiCad は <build>/translation/<言語>/kicad.mo を探す）
while read -r f; do copy "${f#"$build"/}"; done < <(find "$build/translation" -name kicad.mo 2>/dev/null)
# 共通のライブラリ（試験用の qa と、作業用の CMakeFiles は除く）
while read -r f; do copy "${f#"$build"/}"; done < <(find "$build" -path "$build/qa" -prune -o -name CMakeFiles -prune -o \
  \( -type f -o -type l \) -name 'lib*.so*' -print)

if [ ${#missing[@]} -gt 0 ]; then
  echo "画面まわりがビルドに無い: ${missing[*]}" >&2
  [ "${KJP_ALLOW_NO_GUI:-0}" = 1 ] || { echo "（画面なしで作るなら KJP_ALLOW_NO_GUI=1）" >&2; exit 1; }
fi

# ソースの側から、インストールしたときと同じ物を足す（基板エディタの Python のコンソールと、付属のプラグイン）
src=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$build/CMakeCache.txt" 2>/dev/null || true)
if [ -n "$src" ] && [ -d "$src/scripting" ]; then
  mkdir -p "$dst/build/scripting"
  for d in kicad_pyshell plugins; do
    [ -d "$src/scripting/$d" ] && cp -r "$src/scripting/$d" "$dst/build/scripting/"
  done
  find "$dst/build/scripting" \( -name CMakeLists.txt -o -name __pycache__ \) -prune -exec rm -rf {} +
fi

# ---- 記号の表を落とす（Release でも付いている。小さくなるだけで動きは同じ）
if command -v strip >/dev/null; then
  find "$dst/build" -type f | while read -r f; do if is_elf "$f"; then strip --strip-unneeded "$f"; fi; done
fi

# ---- 共通のライブラリの探し先を、ファイルの場所からの相対に
mapfile -t libdirs < <(cd "$dst/build" && find . -path ./plugins -prune -o -name 'lib*.so*' -printf '%h\n' | sed 's|^\./||' | sort -u)
find "$dst/build" -type f | while read -r f; do
  is_elf "$f" || continue
  rel=$(realpath --relative-to="$(dirname "$f")" "$dst/build")
  rp=""
  for d in "${libdirs[@]}"; do rp+="\$ORIGIN/$rel/$d:"; done
  "$patchelf" --set-rpath "${rp%:}" "$f"
done

# 探し先にビルドした場所が残っていないこと、システムに無いライブラリが無いこと。
# システムから使うものは、出どころのパッケージを書き出す（入れる人が足りない物を apt で入れられるように）
: > "$out/.libs-system"
bad=0
while read -r f; do
  is_elf "$f" || continue
  case "$("$patchelf" --print-rpath "$f")" in
    *"$build"*) echo "探し先にビルドの場所が残った: $f" >&2; bad=1 ;;
  esac
  ldd_out=$(env -u LD_LIBRARY_PATH ldd "$f" 2>&1 || true)
  if grep -q "not found" <<<"$ldd_out"; then
    echo "見つからないライブラリ: $f" >&2; grep "not found" <<<"$ldd_out" >&2; bad=1
  fi
  awk '/=> \// {print $3}' <<<"$ldd_out" | grep -v "^$dst/" >> "$out/.libs-system" || true
done < <(find "$dst/build" -type f)
[ "$bad" = 0 ] || exit 1
if command -v dpkg >/dev/null; then
  sort -u "$out/.libs-system" | while read -r l; do
    dpkg -S "$(readlink -f "$l")" 2>/dev/null || dpkg -S "$l" 2>/dev/null || echo "?: $l"
  done | cut -d: -f1 | sort -u > "$dst/DEPENDS-apt.txt"
fi
rm -f "$out/.libs-system"

# ---- AI 用の Python（仲介役のある版だけ）。置き場は <build>/ai-runtime（KiCad は 実行ファイルの 1 つ上/ai-runtime/bin/python3 を探す）
req=$dst/build/scripting/kicad_ai/requirements.txt
if [ -f "$req" ] && [ "${KJP_AI_RUNTIME:-auto}" != skip ]; then
  cache=$out/.cache; mkdir -p "$cache"
  pbs=$cache/$(basename "${PBS_URL//%2B/+}")
  if ! echo "$PBS_SHA256  $pbs" | sha256sum -c --status 2>/dev/null; then
    curl -fsSL -o "$pbs" "$PBS_URL"
    echo "$PBS_SHA256  $pbs" | sha256sum -c
  fi
  rt=$dst/build/ai-runtime
  tar -C "$dst/build" -xzf "$pbs" && mv "$dst/build/python" "$rt"
  "$rt/bin/python3" -m pip install --disable-pip-version-check --no-compile --only-binary=:all: \
    --no-warn-script-location -r "$req"
  mods="claude_agent_sdk, keyring"
  grep -q '^kicad-python' "$req" && mods="$mods, kipy"
  "$rt/bin/python3" -c "import $mods; print('AI 用の Python: claude-agent-sdk', claude_agent_sdk.__version__)"
  # 使わない物（画面の Tcl/Tk・IDLE・ヘッダー・試験）を除く。python3.12 は libpython を中に持つ（静的）ので、
  # 埋め込み用の libpython3.12.so（約 110MB）も要らない。
  # python3.12 と、SDK に同梱の claude は strip すると壊れる（前者は記号の版が、後者は末尾に足した中身が消える）
  rm -rf "$rt"/include "$rt"/share "$rt"/lib/tcl* "$rt"/lib/tk* "$rt"/lib/itcl* "$rt"/lib/thread* "$rt"/lib/libtcl* "$rt"/lib/libtk* \
         "$rt"/lib/libpython3*.so* "$rt"/lib/pkgconfig "$rt"/bin/python3*-config "$rt"/bin/python-config \
         "$rt"/lib/python3.*/{test,idlelib,tkinter,turtledemo,ensurepip} "$rt"/lib/python3.*/lib-dynload/_tkinter* \
         "$rt"/bin/idle3* "$rt"/bin/2to3*
  find "$rt" -name __pycache__ -prune -exec rm -rf {} +
  "$rt/bin/python3" -c "import $mods, ssl, sqlite3, ctypes"
  # SDK に同梱の claude が動くこと
  claude=$(ls "$rt"/lib/python3.*/site-packages/claude_agent_sdk/_bundled/claude 2>/dev/null | head -1)
  [ -x "$claude" ] || { echo "SDK に claude が入っていない" >&2; exit 1; }
  "$claude" --version
fi

# ---- Freerouting（全体の自動配線）と、それだけを動かす最小の Java。置き場は <build>/freerouting（kicad-local autoroute が探す）
# jlink のできる JDK 25 以降が要る（KJP_JDK か JAVA_HOME）。KJP_FREEROUTING=skip で入れない
if [ "${KJP_FREEROUTING:-auto}" != skip ]; then
  FR_CACHE=$out/.cache "$here/package/freerouting.sh" "$dst/build/freerouting" "${KJP_JDK:-${JAVA_HOME:-}}"
fi

{
  echo "版: $ver"
  echo "元: $(git -C "$here" describe --always --dirty 2>/dev/null || echo '?')"
  echo "作った日: $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "作った環境: $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m)"
  command -v dpkg-query >/dev/null && echo "使った本家の KiCad（PPA）: $(dpkg-query -W -f '${Version}' kicad 2>/dev/null || echo '?')"
  echo "AI 用の Python: $([ -d "$dst/build/ai-runtime" ] && "$dst/build/ai-runtime/bin/python3" -V || echo なし)"
  echo "Freerouting: $([ -f "$dst/build/freerouting/freerouting.jar" ] && head -1 "$dst/build/freerouting/README.txt" || echo なし)"
  if [ ${#missing[@]} -gt 0 ]; then echo "入っていないもの: ${missing[*]}"; fi
} > "$dst/BUILD_INFO.txt"

tar -C "$out" -czf "$out/$name.tar.gz" "$name"
(cd "$out" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
echo "$out/$name.tar.gz ($(du -h "$out/$name.tar.gz" | cut -f1))"
