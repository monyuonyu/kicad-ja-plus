#!/bin/bash
# Freerouting（全体の自動配線）と、それだけを動かす最小の Java を、配布物に入れる形で用意する
#
# 使い方: package/freerouting.sh <置き場> [JDK の場所=$JAVA_HOME]
#   <置き場>/freerouting.jar      Freerouting（版と sha256 を固定）
#   <置き場>/jre/                 jlink で作った Java（Freerouting が使うモジュールだけ。jdeps で調べる）
#   <置き場>/LICENSE-freerouting.txt、README.txt（出どころ・版・ソースの場所）。Java の使用許諾は jre/legal/
# JDK は Freerouting 2.4.1 に合わせて 25 以降（クラスファイルの版 69）。Linux でも Windows（MSYS2 の bash）でも動く。
# 置き場が既に揃っていて版が同じなら、作り直さない（ダウンロードは $FR_CACHE、既定は <置き場>/.. の .cache に置く）
set -euo pipefail
FR_VER=2.4.1
FR_URL=https://github.com/freerouting/freerouting/releases/download/v$FR_VER/freerouting-$FR_VER.jar
FR_SHA256=251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9
FR_LICENSE_URL=https://raw.githubusercontent.com/freerouting/freerouting/v$FR_VER/LICENSE
FR_LICENSE_SHA256=e1c0ad728983d8a57335e52cf1064f1affd1d454173d8cebd3ed8b4a72b48704
JAVA_MIN=25

dst=${1:?置き場を指定する}
jdk=${2:-${JAVA_HOME:-}}
[ -n "$jdk" ] || { echo "JDK の場所が無い（引数か JAVA_HOME。Temurin $JAVA_MIN 以降）" >&2; exit 2; }
if command -v cygpath >/dev/null; then jdk=$(cygpath -u "$jdk"); fi
exe=""; [ -x "$jdk/bin/jlink.exe" ] && exe=.exe
[ -x "$jdk/bin/jlink$exe" ] || { echo "jlink が無い: $jdk/bin（JRE ではなく JDK が要る）" >&2; exit 2; }
jmajor=$(sed -n 's/^JAVA_VERSION="\([0-9]*\).*/\1/p' "$jdk/release")
[ "${jmajor:-0}" -ge $JAVA_MIN ] || { echo "JDK が古い: $jmajor（Freerouting $FR_VER には $JAVA_MIN 以降が要る）" >&2; exit 2; }

mkdir -p "$dst"
dst=$(cd "$dst" && pwd)
cache=${FR_CACHE:-$(dirname "$dst")/.cache}
mkdir -p "$cache"
get() {  # URL sha256 ファイル名
  local f=$cache/$3
  if ! echo "$2  $f" | sha256sum -c --status 2>/dev/null; then
    curl -fsSL -o "$f" "$1"
    echo "$2  $f" | sha256sum -c
  fi
  cp "$f" "$dst/$3"
}
get "$FR_URL" "$FR_SHA256" freerouting.jar
get "$FR_LICENSE_URL" "$FR_LICENSE_SHA256" LICENSE-freerouting.txt

# Freerouting が使うモジュールを jdeps で調べる（jar の中のライブラリが持つ module-info は外して、クラスとして調べる）
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
(cd "$t" && "$jdk/bin/jar$exe" xf "$dst/freerouting.jar")
find "$t" -name module-info.class -delete
rm -rf "$t/META-INF/versions"
w() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }
mods=$("$jdk/bin/jdeps$exe" --ignore-missing-deps --print-module-deps -q "$(w "$t")" | tr -d '\r' | tail -1)
case "$mods" in java.base*) ;; *) echo "jdeps の結果がおかしい: $mods" >&2; exit 1 ;; esac
echo "Freerouting が使うモジュール: $mods"

rm -rf "$dst/jre"
"$jdk/bin/jlink$exe" --add-modules "$mods" --strip-debug --no-header-files --no-man-pages --compress=zip-6 \
  --output "$(w "$dst/jre")"
java=$dst/jre/bin/java$exe
"$java" -version
jver=$("$java" -version 2>&1 | head -1 | tr -d '\r')

cat > "$dst/README.txt" <<EOF
Freerouting $FR_VER（全体の自動配線。kicad-local autoroute が使う）

- freerouting.jar: $FR_URL
  sha256 $FR_SHA256
  使用許諾: GPL-3.0（LICENSE-freerouting.txt）。ソース: https://github.com/freerouting/freerouting/tree/v$FR_VER
- jre/: Eclipse Temurin（OpenJDK）から jlink で、Freerouting が使うモジュールだけを入れた Java
  $jver
  モジュール: $mods
  使用許諾: GPL-2.0 with Classpath Exception（jre/legal/java.base/LICENSE と ASSEMBLY_EXCEPTION ほか、モジュールごとに jre/legal/）。
  ソース: https://github.com/adoptium/jdk25u
- 単独で動かすとき: jre/bin/java -jar freerouting.jar（画面あり）、
  jre/bin/java -Djava.awt.headless=true -jar freerouting.jar -de 基板.dsn -do 結果.ses --gui.enabled=false（画面なし）
EOF
du -sh "$dst"
