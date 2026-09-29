#!/bin/bash
# Linux 版の配布物（tar.gz）を作る: ビルドしたフォルダから、動かすのに要るものだけを同じ配置で抜き出す
#
# 使い方: package/linux.sh [ビルドの場所=~/src/build-10.0.6] [出力先=dist] [版=10.0.6]
#
# 中身: bin/（入口）、tools/、test/、patches/、build/（kicad-cli、kicad_route、kicad_gerber、
#       kiface、pcbnew の Python モジュール、3D の読み込み、共通のライブラリ）
# 共通のライブラリの探し先は $ORIGIN からの相対にする（patchelf）ので、どこに展開しても動く。
# wxWidgets・OpenCASCADE・ngspice などと部品ライブラリは、システムに入れた本家の KiCad（同じ系列）のものを使う。
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
build=$(readlink -f "${1:-$HOME/src/build-10.0.6}")
out=$(readlink -f "${2:-$here/dist}")
ver=${3:-10.0.6}
patchelf=${PATCHELF:-patchelf}
command -v "$patchelf" >/dev/null || { echo "patchelf が無い（apt install patchelf か pip install patchelf）" >&2; exit 2; }

name=kicad-ja-plus-$ver-linux-x86_64
dst=$out/$name
rm -rf "$dst"
mkdir -p "$dst/build"

# リポジトリの側（試験の出力と Python のキャッシュは除く）
tar -C "$here" --exclude=__pycache__ --exclude=test/now -cf - bin tools test patches README.md LICENSE | tar -C "$dst" -xf -

# ビルドの側（ライブラリは実体とリンクの両方）
files=(
  kicad/kicad-cli
  qa/tools/pns/kicad_route
  gerbview/kicad_gerber
  pcbnew/_pcbnew.kiface pcbnew/_pcbnew.so pcbnew/pcbnew.py
  eeschema/_eeschema.kiface
  cvpcb/_cvpcb.kiface
  plugins/3d/vrml/libs3d_plugin_vrml.so plugins/3d/oce/libs3d_plugin_oce.so plugins/3d/idf/libs3d_plugin_idf.so
  schemas
)
for d in common common/gal api 3d-viewer/3d_cache/sg; do
  for f in "$build/$d"/lib*.so*; do files+=("${f#"$build"/}"); done
done
for f in "${files[@]}"; do
  [ -e "$build/$f" ] || { echo "ビルドに無い: $build/$f" >&2; exit 1; }
  mkdir -p "$dst/build/$(dirname "$f")"
  cp -a "$build/$f" "$dst/build/$f"
done

# 共通のライブラリの探し先を、ファイルの場所からの相対に
libdirs=(common common/gal api 3d-viewer/3d_cache/sg)
find "$dst/build" -type f \( -perm -u+x -o -name '*.so*' -o -name '*.kiface' \) | while read -r f; do
  head -c4 "$f" | grep -q $'\x7fELF' || continue
  rel=$(realpath --relative-to="$(dirname "$f")" "$dst/build")
  rp=""
  for d in "${libdirs[@]}"; do rp+="\$ORIGIN/$rel/$d:"; done
  "$patchelf" --set-rpath "${rp%:}" "$f"
done

# 探し先に、ビルドした場所が残っていないこと
find "$dst/build" -type f | while read -r f; do
  head -c4 "$f" | grep -q $'\x7fELF' || continue
  case "$("$patchelf" --print-rpath "$f")" in
    *"$build"*) echo "探し先にビルドの場所が残った: $f" >&2; exit 1 ;;
  esac
done

{
  echo "版: $ver"
  echo "元: $(git -C "$here" describe --always --dirty 2>/dev/null || echo '?')"
  echo "作った日: $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "作った環境: $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m)"
} > "$dst/BUILD_INFO.txt"

tar -C "$out" -czf "$out/$name.tar.gz" "$name"
echo "$out/$name.tar.gz ($(du -h "$out/$name.tar.gz" | cut -f1))"
